"""The relay engine: owns every source, polls on a schedule, serves snapshots."""
from __future__ import annotations

import asyncio
import dataclasses
import random
from pathlib import Path
from typing import Callable

import httpx

from . import __version__
from .alerts import Foghorn
from .config import Config, SourceCfg
from .mailbox import MailboxPuller
from .ntfy_brief import NtfyBrief
from .envelope import (
    SourceState,
    build_envelope,
    effective_status,
    is_stale,
    iso,
    panel_status,
    parse_iso,
    utcnow,
)
from .push import validate_payload
from .sources import REGISTRY, Context
from .sources.base import SourceError
from .store import Store
from .tokens import TokenStore


def describe_error(exc: Exception) -> str:
    """A short error for the envelope. Never includes URLs, which carry coordinates."""
    if isinstance(exc, SourceError):
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.TransportError):
        return "connection error"
    if isinstance(exc, (KeyError, ValueError, TypeError, AttributeError, IndexError)):
        return "unexpected response format"
    return type(exc).__name__


class Relay:
    def __init__(self, config: Config, client: httpx.AsyncClient | None = None, now: Callable = utcnow):
        self.config = config
        self.now = now
        self.started_at = now()
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": config.http.user_agent},
            timeout=config.http.timeout,
            follow_redirects=True,
        )
        self.store = Store(Path(config.server.data_dir))
        self.tokens = TokenStore(Path(config.server.data_dir) / "tokens.json")
        self._tasks: list[asyncio.Task] = []

        unknown = set(config.sources) - set(REGISTRY)
        if unknown:
            raise ValueError(f"unknown sources in config: {sorted(unknown)}; known: {sorted(REGISTRY)}")

        self._extra_clients: list[httpx.AsyncClient] = []
        ctx = Context(client=self.client, location=config.location, now=now)
        self.sources = {}
        self.states: dict[str, SourceState] = {}
        for sid, cls in REGISTRY.items():
            cfg = config.sources.get(sid)
            if cfg is not None and not cfg.enabled:
                continue
            if cfg is None:
                cfg = SourceCfg()
            for param, other in cls.inherits.items():  # e.g. airports: use the FAA feed's list unless set here
                other_cfg = config.sources.get(other)
                if not cfg.params.get(param) and other_cfg and other_cfg.params.get(param):
                    cfg = cfg.model_copy(update={"params": {**cfg.params, param: other_cfg.params[param]}})
            source_ctx = ctx
            if cfg.proxy:
                proxied = httpx.AsyncClient(
                    headers={"User-Agent": config.http.user_agent},
                    timeout=config.http.timeout,
                    follow_redirects=True,
                    proxy=cfg.proxy,
                )
                self._extra_clients.append(proxied)
                source_ctx = dataclasses.replace(ctx, client=proxied)
            source = cls(cfg, source_ctx)
            if not source.configured():
                continue  # e.g. faa_airports until airports are listed
            self.sources[sid] = source
            raw = self.store.load_state(sid)
            self.states[sid] = SourceState.from_json(raw) if raw else SourceState()

        self.pushed: dict[str, dict | None] = {slot: self.store.load_push(slot) for slot in config.push}
        self.foghorn = Foghorn(self)
        self.mailbox = MailboxPuller(self)
        self.ntfy_brief = NtfyBrief(self)

    # fetching

    async def refresh(self, sid: str) -> None:
        src, state = self.sources[sid], self.states[sid]
        state.last_attempt = self.now()
        try:
            result = await src.fetch()
        except Exception as exc:  # a failing source must never take the relay down
            state.error = describe_error(exc)
            state.consecutive_failures += 1
        else:
            state.data = result.data
            state.summary = result.summary
            state.status = result.status
            state.source_url = result.source_url
            state.last_success = state.last_attempt
            state.error = None
            state.consecutive_failures = 0
        self.store.save_state(sid, state.to_json())
        await self.foghorn.check_source(sid)

    async def refresh_all(self) -> None:
        await asyncio.gather(*(self.refresh(sid) for sid in self.sources))

    async def _loop(self, sid: str, index: int) -> None:
        src = self.sources[sid]
        await asyncio.sleep(min(index * 0.7, 10) + random.uniform(0, 1))
        while True:
            await self.refresh(sid)
            failures = self.states[sid].consecutive_failures
            factor = 1 if failures == 0 else min(1 + failures, 6)  # back off while failing
            await asyncio.sleep(src.interval * factor * random.uniform(0.9, 1.1))

    def start(self) -> None:
        self.foghorn.start()
        self.mailbox.start()
        self.ntfy_brief.start()
        for index, sid in enumerate(self.sources):
            self._tasks.append(asyncio.create_task(self._loop(sid, index)))

    async def stop(self) -> None:
        await self.foghorn.stop()
        await self.mailbox.stop()
        await self.ntfy_brief.stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.client.aclose()
        for extra in self._extra_clients:
            await extra.aclose()

    # push

    def push(self, slot: str, raw: object, token_name: str, extra: dict | None = None) -> dict:
        payload = validate_payload(slot, raw)  # raises pydantic.ValidationError
        now = self.now()
        record = {"received_at": iso(now), "token": token_name, "data": payload, **(extra or {})}
        self.store.save_push(slot, record, now.date().isoformat())
        self.pushed[slot] = record
        return self.push_envelope(slot)

    def _push_state(self, record: dict | None) -> SourceState:
        if not record:
            return SourceState()
        received = parse_iso(record["received_at"])
        return SourceState(
            last_success=received,
            last_attempt=received,
            data=record["data"],
            summary=(record["data"].get("title") or "Pushed content"),
            status="info",
        )

    def _push_meta(self, slot: str) -> dict:
        return {
            "id": slot,
            "title": {"brief": "Keeper's Log", "context": "Context headlines"}.get(slot, slot),
            "panel": {"brief": "brief", "context": "geo"}.get(slot, slot),
            "tier": "push",
            "stale_after": self.config.push[slot].stale_after,
        }

    def push_envelope(self, slot: str, record: dict | None = None) -> dict:
        record = record if record is not None else self.pushed.get(slot)
        return build_envelope(self._push_meta(slot), self._push_state(record), self.now())

    def archived_envelope(self, slot: str, day: str) -> dict | None:
        record = self.store.load_archive(slot, day)
        return None if record is None else self.push_envelope(slot, record)

    # views

    def envelope(self, sid: str) -> dict:
        return build_envelope(self.sources[sid].meta(), self.states[sid], self.now())

    def snapshot(self) -> dict:
        now = self.now()
        sources = {sid: self.envelope(sid) for sid in self.sources}
        panels: dict[str, dict] = {}
        for sid, env in sources.items():
            panel = panels.setdefault(env["panel"], {"status": "nominal", "sources": []})
            panel["sources"].append(sid)
        for name, panel in panels.items():
            panel["status"] = panel_status([sources[s]["status"] for s in panel["sources"]])
        return {
            "schema": 1,
            "generated_at": iso(now),
            "location": self.config.location.public(),
            "panels": panels,
            "sources": sources,
            "push": {slot: self.push_envelope(slot) for slot in self.config.push},
        }

    def health(self) -> dict:
        now = self.now()
        rows = []
        for sid, src in self.sources.items():
            st = self.states[sid]
            rows.append(
                {
                    "id": sid,
                    "tier": src.tier,
                    "status": effective_status(st, src.stale_after, now),
                    "stale": is_stale(st, src.stale_after, now),
                    "last_success": iso(st.last_success),
                    "consecutive_failures": st.consecutive_failures,
                    "error": st.error,
                }
            )
        push = []
        for slot, cfg in self.config.push.items():
            record = self.pushed.get(slot)
            st = self._push_state(record)
            push.append(
                {
                    "slot": slot,
                    "received_at": iso(st.last_success),
                    "stale": is_stale(st, cfg.stale_after, now),
                }
            )
        degraded = any(r["stale"] or r["error"] for r in rows)
        return {
            "ok": True,
            "degraded": degraded,
            "version": __version__,
            "uptime_s": int((now - self.started_at).total_seconds()),
            "sources": rows,
            "push": push,
            "push_enabled": self.tokens.any(),
            **({"mailbox": mb} if (mb := self.mailbox.health()) else {}),
            **({"brief_ntfy": nb} if (nb := self.ntfy_brief.health()) else {}),
        }
