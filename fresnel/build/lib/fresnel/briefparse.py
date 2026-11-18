"""Split a plain-text daily brief into sections, so the board can show a list with a rating on each.

The producer writes numbered section headings, optionally ending in a rating in brackets:

    CORE TIER
    1. Geopolitical / Conflict [NOTABLE]
    ...text...
    3. Critical Infrastructure & Cyber [CRITICAL]
    Sources
    https://example.org/page

Headings must be numbered 1, 2, 3 in order, so numbered lists inside a section are not mistaken for
new sections. A brief with fewer than three such headings is left as it is.
"""
from __future__ import annotations

import re

LEVELS = ("quiet", "notable", "elevated", "critical")
_ALIASES = {
    "quiet": "quiet", "low": "quiet", "routine": "quiet", "none": "quiet", "normal": "quiet",
    "notable": "notable", "moderate": "notable", "medium": "notable", "watch": "notable", "guarded": "notable",
    "elevated": "elevated", "high": "elevated", "significant": "elevated",
    "critical": "critical", "severe": "critical", "extreme": "critical",
}
_RULE = r"(?:[=#*_-]{2,}\s*)?"
_HEADING = re.compile(rf"^\s*{_RULE}(?P<num>\d{{1,2}})[.)]\s+(?P<title>\S[^\n]{{1,160}}?)\s*(?:\[(?P<lvl>[A-Za-z ]{{3,12}})\])?\s*{_RULE}$")
_TIER = re.compile(rf"^\s*{_RULE}(?P<name>core|watch)\s+tier\b[^\n]{{0,80}}?{_RULE}$", re.I)
_SOURCES = re.compile(rf"^\s*{_RULE}sources\s*:?\s*{_RULE}$", re.I)
_ITEM = re.compile(r"^\s*\d{1,2}[.)]\s+\S")
_URL = re.compile(r"https?://[^\s<>\"')\]]+")


def level(raw: str | None) -> str | None:
    return _ALIASES.get((raw or "").strip().lower())


def split(text: str) -> tuple[str, list[dict]] | None:
    """Return (intro, sections) or None when the text has no usable structure."""
    intro: list[str] = []
    sections: list[dict] = []
    current: list[str] | None = None
    group: str | None = None
    expected = 1

    def close() -> None:
        if sections and current is not None:
            sections[-1]["body"] = "\n".join(current).strip()

    prev_is_item = False  # the previous line was a numbered list entry inside a section
    for line in text.splitlines():
        head = _HEADING.match(line)
        tier = _TIER.match(line)
        is_heading = bool(head) and int(head["num"]) == expected and not prev_is_item
        if is_heading:
            close()
            current = []
            expected += 1
            sections.append({"number": int(head["num"]), "title": head["title"].strip(" =#*_-"), "criticality": level(head["lvl"]), "group": group, "body": "", "sources": []})
        elif tier:
            group = f"{tier['name'].capitalize()} tier"
        elif sections and _SOURCES.match(line):
            close()
            current = []
            sections.append({"number": None, "title": "Sources", "criticality": None, "group": None, "body": "", "sources": []})
        elif current is not None:
            current.append(line)
        else:
            intro.append(line)
        prev_is_item = not is_heading and bool(_ITEM.match(line))
    close()
    if sum(1 for s in sections if s["number"]) < 3:
        return None
    for s in sections:
        if s["title"] == "Sources":
            s["sources"] = list(dict.fromkeys(_URL.findall(s["body"])))[:100]
    return "\n".join(intro).strip(), sections
