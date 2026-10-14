"""Brief over ntfy: the job that writes the brief posts it to a private ntfy topic, and Fresnel reads the topic.

Use this when the job can reach a public ntfy server but cannot reach the relay. The topic name is the
only secret, so make it long and random (`fresnel ntfy-topic` prints one).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

if TYPE_CHECKING:
    from .relay import Relay

MAX_BODY = 60000
MAX_FILE = 300_000  # bytes read from an attachment


class NtfyBriefError(Exception):
    pass


def pick(messages: list[dict], base_host: str) -> tuple[dict | None, str | None]:
    """Newest message event, and the attachment URL to fetch when the body was sent as a file."""
    events = [m for m in messages if m.get("event") == "message"]
    if not events:
        return None, None
    m = max(events, key=lambda e: e.get("time", 0))
    att = m.get("attachment") or {}
    url = att.get("url")
    if url and urlparse(url).hostname != base_host:
        raise NtfyBriefError("attachment is not hosted on the configured ntfy server")
    return m, url


class NtfyBrief:
    def __init__(self, relay: "Relay"):
        self.relay = relay
        self.cfg = relay.config.brief_ntfy
        self.last_poll: datetime | None = None
        self.last_ok: datetime | None = None
        self.error: str | None = None
        self.last_id: str | None = None
        self._task: asyncio.Task | None = None
        current = relay.pushed.get("brief")
        if current and current.get("token") == "ntfy":
            self.last_id = current.get("message_id")

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.enabled and self.cfg.topic)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.cfg.token}"} if self.cfg.token else {}

    async def _read(self, url: str) -> bytes:
        async with self.relay.client.stream("GET", url, headers=self._headers()) as resp:
            resp.raise_for_status()
            chunks, size = [], 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > MAX_FILE:
                    raise NtfyBriefError("message is too large")
                chunks.append(chunk)
        return b"".join(chunks)

    async def poll(self) -> None:
        now = self.relay.now()
        self.last_poll = now
        base = self.cfg.url.rstrip("/")
        try:
            raw = await self._read(f"{base}/{self.cfg.topic}/json?poll=1&since={self.cfg.lookback_days * 24}h")
            messages = [json.loads(line) for line in raw.decode("utf-8", "replace").splitlines() if line.strip()]
            msg, attachment = pick(messages, urlparse(base).hostname)
            self.error, self.last_ok = None, now
            if msg is None or msg.get("id") == self.last_id:
                return
            if attachment:
                text = (await self._read(attachment)).decode("utf-8", "replace")
            else:
                text = msg.get("message", "")
            text = text.strip()
            if not text:
                raise NtfyBriefError("message has no text")
            when = datetime.fromtimestamp(msg.get("time", now.timestamp()), tz=timezone.utc)
            title = (msg.get("title") or text.splitlines()[0])[:200]
            current = self.relay.pushed.get("brief")
            current_at = (current or {}).get("data", {}).get("generated_at")
            if current and current.get("token") != "ntfy" and current_at and when.isoformat() <= current_at.replace("Z", "+00:00"):
                return  # a newer direct push wins
            self.relay.push(
                "brief",
                {"generated_at": when.isoformat(), "model": "ntfy", "format": "text", "title": title, "body": text[:MAX_BODY]},
                "ntfy",
                extra={"message_id": msg.get("id")},
            )
            self.last_id = msg.get("id")
        except NtfyBriefError as exc:
            self.error = str(exc)
        except httpx.HTTPStatusError as exc:
            self.error = f"ntfy server answered HTTP {exc.response.status_code}"
        except Exception as exc:  # never take the relay down
            self.error = f"unexpected error ({type(exc).__name__})"

    async def _loop(self) -> None:
        await asyncio.sleep(5)
        while True:
            await self.poll()
            await asyncio.sleep(self.cfg.interval)

    def start(self) -> None:
        if self.enabled:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def health(self) -> dict | None:
        if not self.enabled:
            return None
        fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None
        return {"last_poll": fmt(self.last_poll), "last_ok": fmt(self.last_ok), "error": self.error}
