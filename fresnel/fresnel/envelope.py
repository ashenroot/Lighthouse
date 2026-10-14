"""The response envelope: one shape for every source, pulled or pushed.

Status is computed here, on the relay, never by the dashboard:
  nominal | active   from the source's own rules, while its data is fresh
  unknown            never fetched, failed, or older than stale_after
  info               pushed content; it never drives status or alerts
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

STATUSES = ("nominal", "active", "unknown", "info")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class SourceState:
    """What the relay last learned from one source. Persisted to disk."""

    last_success: datetime | None = None
    last_attempt: datetime | None = None
    error: str | None = None
    consecutive_failures: int = 0
    data: Any = None
    summary: str = ""
    status: str = "unknown"
    source_url: str | None = None

    def to_json(self) -> dict:
        return {
            "last_success": iso(self.last_success),
            "last_attempt": iso(self.last_attempt),
            "error": self.error,
            "consecutive_failures": self.consecutive_failures,
            "data": self.data,
            "summary": self.summary,
            "status": self.status,
            "source_url": self.source_url,
        }

    @classmethod
    def from_json(cls, raw: dict) -> "SourceState":
        return cls(
            last_success=parse_iso(raw.get("last_success")),
            last_attempt=parse_iso(raw.get("last_attempt")),
            error=raw.get("error"),
            consecutive_failures=int(raw.get("consecutive_failures") or 0),
            data=raw.get("data"),
            summary=raw.get("summary") or "",
            status=raw.get("status") if raw.get("status") in STATUSES else "unknown",
            source_url=raw.get("source_url"),
        )


def is_stale(state: SourceState, stale_after: int, now: datetime) -> bool:
    if state.last_success is None:
        return True
    return (now - state.last_success).total_seconds() > stale_after


def effective_status(state: SourceState, stale_after: int, now: datetime) -> str:
    """Stale or never-fetched data is unknown, however its last reading looked."""
    if is_stale(state, stale_after, now) or state.data is None:
        return "unknown"
    return state.status


def build_envelope(meta: dict, state: SourceState, now: datetime) -> dict:
    stale = is_stale(state, meta["stale_after"], now)
    if state.last_success is None:
        summary = "Waiting for first successful fetch"
    elif stale:
        summary = "Stale: " + (state.summary or "no summary")
    else:
        summary = state.summary
    return {
        "id": meta["id"],
        "title": meta["title"],
        "panel": meta["panel"],
        "tier": meta["tier"],
        "status": "info" if meta["tier"] == "push" and not stale and state.data is not None
        else effective_status(state, meta["stale_after"], now),
        "summary": summary,
        "source_url": state.source_url or meta.get("source_url"),
        "fetch": {
            "ok": state.last_success is not None and state.error is None,
            "fetched_at": iso(state.last_success),
            "last_attempt_at": iso(state.last_attempt),
            "stale_after": meta["stale_after"],
            "stale": stale,
            "error": state.error,
            "consecutive_failures": state.consecutive_failures,
        },
        "data": state.data,
    }


def panel_status(statuses: list[str]) -> str:
    """A panel shows its worst live reading: active beats unknown beats nominal."""
    if "active" in statuses:
        return "active"
    if "unknown" in statuses:
        return "unknown"
    return "nominal"
