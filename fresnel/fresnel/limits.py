"""Small in-memory limits for visitors. Nothing here is written to disk, and a restart clears it."""
from __future__ import annotations

import ipaddress
from collections import deque
from typing import Callable


class SlidingWindow:
    """At most `limit` events per `window` seconds for each key (a visitor's address)."""

    def __init__(self, limit: int, window: float, now: Callable[[], float], max_keys: int = 20000):
        self.limit, self.window, self.now, self.max_keys = limit, window, now, max_keys
        self._events: dict[str, deque[float]] = {}

    def _trim(self, key: str, t: float) -> deque[float]:
        q = self._events.setdefault(key, deque())
        while q and q[0] <= t - self.window:
            q.popleft()
        return q

    def retry_after(self, key: str) -> float:
        """Seconds until one more event would be allowed for this key; 0 when it would be allowed now."""
        t = self.now()
        q = self._trim(key, t)
        return 0.0 if len(q) < self.limit else max(1.0, q[0] + self.window - t)

    def hit(self, key: str) -> None:
        t = self.now()
        self._trim(key, t).append(t)
        if len(self._events) > self.max_keys:  # keep the table bounded under a flood of addresses
            for k in [k for k, q in self._events.items() if not q or q[-1] <= t - self.window]:
                del self._events[k]
            while len(self._events) > self.max_keys:
                self._events.pop(next(iter(self._events)))


def allowed(address: str, networks: list[str]) -> bool:
    """True when the address is inside one of the listed addresses or CIDR ranges."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(n, strict=False) for n in networks)


def client_address(peer: str | None, header_value: str | None, header_name: str) -> str:
    """The visitor's address. The header is believed only when the request arrives from this machine, which means a
    reverse proxy such as Caddy put it there; from anywhere else it could be forged, so the peer address is used."""
    if peer in ("127.0.0.1", "::1") and header_value:
        parts = [p.strip() for p in header_value.split(",") if p.strip()]
        if parts:
            # X-Forwarded-For lists each hop; the last entry is the one our own proxy appended. Other headers hold one address.
            return parts[-1] if header_name.lower() == "x-forwarded-for" else parts[0]
    return peer or "unknown"
