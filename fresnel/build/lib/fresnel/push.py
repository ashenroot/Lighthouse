"""Push slots: schemas and sanitizing for data that outside producers hand to the relay.

Pushed content is untrusted text. The relay strips control characters, enforces
types and size limits, and stores it as given. Consumers must render it as plain
text or sanitized markdown, never as HTML.
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from . import briefparse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

Url = Annotated[str, StringConstraints(pattern=r"^https?://[^\s]+$", max_length=2000)]
Short = Annotated[str, StringConstraints(max_length=300)]
Medium = Annotated[str, StringConstraints(max_length=1000)]
Long = Annotated[str, StringConstraints(max_length=20000)]
BriefBody = Annotated[str, StringConstraints(max_length=60000)]  # a full 13-section briefing runs long
PanelId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]

_STRICT = ConfigDict(extra="forbid", protected_namespaces=())


class BriefSection(BaseModel):
    model_config = _STRICT

    panel: PanelId | None = None
    title: Short | None = None
    number: int | None = Field(default=None, ge=1, le=99)
    group: Short | None = None  # e.g. "Core tier"
    criticality: Literal["quiet", "notable", "elevated", "critical"] | None = None
    body: Long
    sources: list[Url] = Field(default_factory=list, max_length=100)


class Brief(BaseModel):
    """The Keeper's Log: a daily brief produced by an outside job."""

    model_config = _STRICT

    generated_at: AwareDatetime
    model: Short | None = None
    format: Literal["markdown", "text"] = "markdown"
    title: Short | None = None
    body: BriefBody | None = None
    source_notes: list[Short] = Field(default_factory=list, max_length=50)
    sections: list[BriefSection] = Field(default_factory=list, max_length=30)


class ContextItem(BaseModel):
    model_config = _STRICT

    title: Short
    summary: Medium | None = None
    url: Url | None = None
    source: Short | None = None
    published_at: AwareDatetime | None = None


class ContextSlot(BaseModel):
    """Curated geopolitics and security headlines, pushed by an outside job."""

    model_config = _STRICT

    generated_at: AwareDatetime
    items: list[ContextItem] = Field(default_factory=list, max_length=50)


SLOT_MODELS: dict[str, type[BaseModel]] = {
    "brief": Brief,
    "context": ContextSlot,
}

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean(obj: Any) -> Any:
    """Strip control characters (except tab and newline) from every string."""
    if isinstance(obj, str):
        return _CONTROL.sub("", obj)
    if isinstance(obj, list):
        return [clean(x) for x in obj]
    if isinstance(obj, dict):
        return {(clean(k) if isinstance(k, str) else k): clean(v) for k, v in obj.items()}
    return obj


def validate_payload(slot: str, raw: Any) -> dict:
    """Validate and normalize a pushed payload. Raises pydantic.ValidationError."""
    model = SLOT_MODELS[slot]
    data = model.model_validate(clean(raw)).model_dump(mode="json")
    if slot == "brief" and data.get("body") and not data.get("sections"):
        parsed = briefparse.split(data["body"])
        if parsed:
            intro, sections = parsed
            data["body"] = intro or None
            data["sections"] = [BriefSection(**s).model_dump(mode="json") for s in sections][:30]
    return data
