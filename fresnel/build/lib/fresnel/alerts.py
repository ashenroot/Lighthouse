"""Foghorn: push notifications through ntfy when a source turns active or the brief goes missing."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

from .envelope import effective_status, is_stale

if TYPE_CHECKING:
    from .relay import Relay

log = logging.getLogger("fresnel.foghorn")


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode()  # HTTP headers must be latin-1 safe


class Foghorn:
    def __init__(self, relay: "Relay"):
        self.relay = relay
        self.cfg = relay.config.foghorn
        self.topic = self.cfg.topic or ""
        self.enabled = bool(self.cfg.enabled and self.topic)
        # Readings loaded from disk count as already announced, so a restart stays quiet.
        self.seen: dict[str, str] = {sid: self._status(sid) for sid in relay.sources}
        self.seen["brief"] = self._brief_state()
        self._task: asyncio.Task | None = None

    def _status(self, sid: str) -> str:
        src, st = self.relay.sources[sid], self.relay.states[sid]
        return effective_status(st, src.stale_after, self.relay.now())

    def _brief_state(self) -> str:
        cfg = self.relay.config.push["brief"]
        st = self.relay._push_state(self.relay.pushed.get("brief"))
        return "missing" if is_stale(st, cfg.stale_after, self.relay.now()) else "ok"

    async def send(self, title: str, message: str, priority: str = "default", click: str | None = None, tags: str = "") -> bool:
        if not self.enabled:
            return False
        headers = {"Title": _ascii(title), "Priority": priority, "Tags": tags}
        if click:
            headers["Click"] = click
        if self.cfg.token:
            headers["Authorization"] = f"Bearer {self.cfg.token}"
        try:
            r = await self.relay.client.post(
                f"{self.cfg.url.rstrip('/')}/{self.topic}", content=message.encode("utf-8"), headers=headers
            )
            r.raise_for_status()
            return True
        except Exception as exc:  # alerting must never break fetching; the URL holds the secret topic
            log.warning("ntfy send failed: %s", type(exc).__name__)
            return False

    async def check_source(self, sid: str) -> None:
        now = self._status(sid)
        before = self.seen.get(sid)
        self.seen[sid] = now
        if now == "active" and before != "active":
            src = self.relay.sources[sid]
            env = self.relay.envelope(sid)
            await self.send(f"{src.title}: active", env["summary"], priority=self.cfg.priority,
                            click=env["source_url"], tags="warning")

    async def check_brief(self) -> None:
        now = self._brief_state()
        before = self.seen.get("brief")
        self.seen["brief"] = now
        if now == "missing" and before != "missing":
            await self.send("Keeper's Log is missing", "No brief has arrived within its expected window.",
                            priority="high", tags="mailbox_closed")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(600)
            await self.check_brief()

    def start(self) -> None:
        if self.enabled:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
