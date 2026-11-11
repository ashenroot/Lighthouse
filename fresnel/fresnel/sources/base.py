"""Source base class. A source fetches one upstream feed and judges its own status."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import httpx

from ..config import Location, SourceCfg


class SourceError(Exception):
    """A failure whose short message is safe to show in the envelope (no URLs, no coordinates)."""


class KeyMissing(SourceError):
    """The source needs an API key and none is set."""


class KeyRejected(SourceError):
    """The provider refused the key (HTTP 401 or 403). The message never contains the key."""


@dataclass
class Context:
    client: httpx.AsyncClient
    location: Location
    now: Callable[[], datetime]
    keys: Callable[[str], str | None] = lambda name: None  # name -> the key, read fresh each time


@dataclass
class Result:
    data: Any
    summary: str
    status: str  # "nominal" or "active"
    source_url: str


class Source:
    """Subclass, set the class attributes, implement fetch(), and add to REGISTRY."""

    id = ""
    title = ""
    panel = ""
    page = ""  # the publisher's own page, linked from the panel even before the first successful fetch
    tier = "primary"  # primary | fragile
    default_interval = 300
    default_params: dict[str, Any] = {}
    needs: tuple[str, ...] = ()  # params that must be non-empty, or the relay skips the source
    inherits: dict[str, str] = {}  # param -> other source id whose value of it is used when this one is empty
    supersedes: tuple[str, ...] = ()  # ids of keyless sources this one replaces while it has its key
    key: str | None = None  # name of the API key this source needs (see fresnel/keys.py), if any

    def __init__(self, cfg: SourceCfg, ctx: Context):
        self.ctx = ctx
        self.interval = cfg.interval or self.default_interval
        self.stale_after = cfg.stale_after or max(self.interval * 3, 900)
        self.params = {**self.default_params, **cfg.params}

    def configured(self) -> bool:
        return all(self.params.get(k) for k in self.needs)

    def api_key(self) -> str:
        value = self.ctx.keys(self.key) if self.key else None
        if not value:
            raise KeyMissing(f"needs an API key: run fresnel key set {self.key}")
        return value

    def check_key(self, resp: httpx.Response) -> None:
        """Call on a keyed response before raise_for_status: a 401 or 403 means the key was refused."""
        if resp.status_code in (401, 403):
            from ..keys import KEYS

            label = KEYS[self.key].label if self.key in KEYS else "the provider"
            raise KeyRejected(f"{label} rejected the key (HTTP {resp.status_code}). Replace it with: fresnel key set {self.key}")

    def meta(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "panel": self.panel,
            "tier": self.tier,
            "stale_after": self.stale_after,
            "source_url": self.page or None,
        }

    async def fetch(self) -> Result:  # pragma: no cover - interface
        raise NotImplementedError
