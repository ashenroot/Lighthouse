"""Places: the snapshot as seen for another US ZIP Code.

The owner's board shows the owner's place. A request that names a ZIP (the `X-Lighthouse-Zip` header) gets the same
snapshot with the place-dependent sources recomputed for that ZIP's place:

  visitors  see the keyless local sources marked place = "visitor": cached per place, reused for `ttl_seconds`, fetched
            at most once at a time per source and place, and limited by address (new places, requests) and overall
            (concurrent and per-minute upstream fetches). Sources that need a key, or belong to the owner's own
            settings, become link rows to the publisher.
  the owner (a token with the `admin` slot) sees every placeable source at its normal interval, with no limits.

Owner sources run on their own timers and never wait on anything here, so visitor traffic cannot slow them. Places
live in memory only and are dropped least-recently-used first. A ZIP is never stored with a visitor, logged or
published; the snapshot carries only the place's county and state.
"""
from __future__ import annotations

import asyncio
import dataclasses
from collections import OrderedDict, deque

from . import zipcodes
from .config import SourceCfg
from .envelope import SourceState, build_envelope, panel_status
from .limits import SlidingWindow, allowed
from .sources import REGISTRY
from .sources.base import Source

RETRY_AFTER_FAILURE = 300  # seconds before a place's failed source is tried again
BREAKER_AFTER = 5          # consecutive visitor-place failures of one source before it is paused
BREAKER_PAUSE = 300        # seconds a paused source is left alone, so an unhappy upstream is not hammered


class Place:
    def __init__(self, location):
        self.location = location
        self.sources: dict[str, Source] = {}
        self.states: dict[str, SourceState] = {}
        self.inflight: dict[str, asyncio.Task] = {}

    def peer_data(self, sid: str) -> dict | None:
        st = self.states.get(sid)
        return st.data if st and st.data is not None and st.last_success and not st.consecutive_failures else None


class Places:
    def __init__(self, relay):
        self.relay = relay
        self.cfg = relay.config.visitors
        self.cache: OrderedDict[str, Place] = OrderedDict()
        self.new_places = SlidingWindow(self.cfg.new_zips_per_hour, 3600, self._t)
        self.requests = SlidingWindow(self.cfg.requests_per_minute, 60, self._t)
        self.slots = asyncio.Semaphore(self.cfg.max_concurrent_fetches)
        self._fetch_times: deque[float] = deque()
        self._failures: dict[str, int] = {}
        self._paused_until: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()

    def _t(self) -> float:
        return self.relay.now().timestamp()

    # which sources a view shows

    @staticmethod
    def mode(sid: str) -> str | None:
        return getattr(REGISTRY.get(sid), "place", None)

    def placed(self, admin: bool) -> list[str]:
        """Sources recomputed for the place: the visitor set, plus the owner-only set for the owner, minus any that need a key not yet set."""
        wanted = ("visitor", "admin") if admin else ("visitor",)
        return [sid for sid in self.relay.sources if self.mode(sid) in wanted and not self.relay.waiting_for_key(sid)]

    def ttl(self, sid: str, admin: bool) -> float:
        interval = self.relay.sources[sid].interval
        if admin:
            return interval
        return max(interval, self.cfg.ttl_by_source.get(sid, self.cfg.ttl_seconds))

    # fetching

    def _source(self, place: Place, sid: str) -> Source:
        if sid not in place.sources:
            owner = self.relay.sources[sid]
            ctx = dataclasses.replace(owner.ctx, location=place.location, peer=place.peer_data)
            place.sources[sid] = REGISTRY[sid](self.relay.config.sources.get(sid) or SourceCfg(), ctx)
        return place.sources[sid]

    def _due(self, place: Place, sid: str, admin: bool) -> bool:
        if sid in place.inflight:
            return False
        st = place.states.get(sid)
        if st is None or st.last_attempt is None:
            return True
        age = self._t() - st.last_attempt.timestamp()
        return age >= (min(self.ttl(sid, admin), RETRY_AFTER_FAILURE) if st.error else self.ttl(sid, admin))

    def _budget(self, sid: str) -> bool:
        """The visitor-side caps: an upstream that keeps failing is paused, and all places together make a bounded number of fetches a minute."""
        t = self._t()
        if self._paused_until.get(sid, 0) > t:
            return False
        while self._fetch_times and self._fetch_times[0] <= t - 60:
            self._fetch_times.popleft()
        if len(self._fetch_times) >= self.cfg.max_fetches_per_minute:
            return False
        self._fetch_times.append(t)
        return True

    async def _run(self, place: Place, sid: str, admin: bool) -> None:
        from .relay import describe_error

        src, st = self._source(place, sid), place.states.setdefault(sid, SourceState())
        st.last_attempt = self.relay.now()
        try:
            result = await src.fetch()
        except Exception as exc:  # one place's failing source must not affect anything else
            st.error = describe_error(exc)
            st.consecutive_failures += 1
            if not admin:
                self._failures[sid] = self._failures.get(sid, 0) + 1
                if self._failures[sid] >= BREAKER_AFTER:
                    self._paused_until[sid] = self._t() + BREAKER_PAUSE
                    self._failures[sid] = 0
        else:
            st.data, st.summary, st.status, st.source_url = result.data, result.summary, result.status, result.source_url
            st.last_success, st.error, st.consecutive_failures = st.last_attempt, None, 0
            self._failures[sid] = 0

    async def _fetch(self, place: Place, sid: str, admin: bool) -> None:
        try:
            if admin:
                await self._run(place, sid, admin)  # the owner is not held to the visitor caps
            elif self._budget(sid):
                async with self.slots:
                    await self._run(place, sid, admin)
        finally:
            place.inflight.pop(sid, None)

    def _start(self, place: Place, sid: str, admin: bool) -> None:
        task = asyncio.create_task(self._fetch(place, sid, admin))
        place.inflight[sid] = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # building a view

    def _envelope(self, place: Place, sid: str, admin: bool) -> dict:
        src = self._source(place, sid)
        st = place.states.get(sid) or SourceState()
        meta = src.meta()
        meta["stale_after"] = max(src.stale_after, int(self.ttl(sid, admin) * 2))  # data is reused for a full TTL; do not grey it out sooner
        env = build_envelope(meta, st, self.relay.now())
        if st.last_success is None and not st.error:
            env["summary"] = "Loading local data"
        elif st.last_success is None:
            env["summary"] = st.error
        return env

    def _refuse(self, base: dict, info: dict, reason: str, message: str, **more) -> dict:
        info.update(ok=False, reason=reason, message=message, **more)
        return base

    async def snapshot(self, raw_zip: str, admin: bool, address: str) -> dict:
        base = self.relay.snapshot()
        info: dict = {"role": "owner" if admin else "visitor"}
        base["place"] = info
        found = zipcodes.lookup(raw_zip) if len(str(raw_zip)) <= 12 else None
        if not found:
            return self._refuse(base, info, "unknown_zip", "That is not a known US ZIP Code.")
        if not admin:
            if not self.cfg.enabled:
                return self._refuse(base, info, "disabled", "This relay only shows its own place.")
            if self.cfg.allow_ips and not allowed(address, self.cfg.allow_ips):
                return self._refuse(base, info, "not_allowed", "This relay limits other places to approved addresses.")
            wait = self.requests.retry_after(address)
            if wait:
                return self._refuse(base, info, "rate_limited", "Too many requests. Try again shortly.", retry_after=int(wait))
            self.requests.hit(address)

        zip5 = found["zip"]
        place = self.cache.get(zip5)
        if place is None:
            if not admin:
                wait = self.new_places.retry_after(address)
                if wait:
                    return self._refuse(base, info, "new_places_limit", "Too many new places from this address. Showing the default place.", retry_after=int(wait))
                self.new_places.hit(address)
            from .config import Location

            place = Place(Location(zip=zip5))
            self.cache[zip5] = place
            while len(self.cache) > self.cfg.max_places:
                self.cache.popitem(last=False)
        self.cache.move_to_end(zip5)

        shown = self.placed(admin)
        for sid in shown:
            if self._due(place, sid, admin):
                self._start(place, sid, admin)
        waiting = [place.inflight[sid] for sid in shown if sid in place.inflight]
        if waiting:
            await asyncio.wait(waiting, timeout=self.cfg.wait_seconds)

        sources, linked = base["sources"], []
        for sid in self.relay.sources:
            if self.mode(sid) is not None:
                sources.pop(sid, None)
        for sid in shown:
            sources[sid] = self._envelope(place, sid, admin)
        for sid in shown:  # a keyed source that is shown replaces the keyless one it supersedes, as on the owner's own view
            if any(sid in REGISTRY[o].supersedes for o in shown if o != sid):
                sources.pop(sid, None)
        for sid in self.relay.sources:
            if self.mode(sid) in ("admin", "link") and sid not in shown:
                cls = REGISTRY[sid]
                linked.append({"id": sid, "title": cls.title, "panel": cls.panel, "url": cls.page or None})
        base["sources"] = sources
        base["panels"] = self.relay.build_panels(sources)
        for item in linked:  # a panel that has only links still needs to exist for the board to draw them under
            base["panels"].setdefault(item["panel"], {"status": "nominal", "sources": []})
        base["linked"] = linked
        base["location"] = place.location.public()
        if not admin:
            base["setup"] = []  # setup items tell the owner to run commands; they mean nothing for a visitor's place
        info.update(ok=True, name=place.location.name, state=place.location.state.upper(),
                    ttl_seconds=None if admin else self.cfg.ttl_seconds,
                    loading=sum(1 for sid in shown if place.states.get(sid) is None or place.states[sid].last_success is None))
        return base

    def health(self) -> dict:
        return {"enabled": self.cfg.enabled, "places": len(self.cache)}

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
