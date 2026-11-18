"""NERC alerts (nerc.com's own search service, no key).

NERC builds its alert list in the browser, so the HTML is empty to a plain request. The site's
search service returns the documents as JSON. Dates are the documents' modified dates, so this
source is informational and never turns a panel active.
"""
from __future__ import annotations

import re
from datetime import datetime

from .base import Result, Source

SEARCH = "https://www.nerc.com/api/search"
PAGE = "https://www.nerc.com/programs/bulk-power-system-awareness/alerts"
QUERIES = ("NERC Alert", "Alert Level 2", "Level 3 Alert")
PATH = "/programs/bpsa/alerts/"
SKIP = re.compile(r"presentation|webinar|worksheet|instruction|template|response|slides|faq|summary of", re.I)


def _date(text: str | None):
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime((text or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


class NercAlerts(Source):
    id = "nerc_alerts"
    title = "NERC alerts"
    panel = "grid"
    page = PAGE
    tier = "fragile"  # an undocumented search service behind nerc.com
    default_interval = 21600
    default_params = {"max_items": 5, "pages": 2}

    async def fetch(self) -> Result:
        found: dict[str, dict] = {}
        for query in QUERIES:
            for page in range(1, int(self.params["pages"]) + 1):
                resp = await self.ctx.client.get(SEARCH, params={"q": query, "pg": page})
                resp.raise_for_status()
                items = resp.json().get("items") or []
                if not items:
                    break
                for item in items:
                    doc = item.get("documentResult") or {}
                    url, title = doc.get("url") or "", doc.get("title") or ""
                    day = _date(doc.get("modifiedDate"))
                    if PATH not in url or not day or not re.search(r"alert", title, re.I) or SKIP.search(title):
                        continue
                    level = re.search(r"Level\s*([123])", title, re.I)
                    found[url] = {
                        "title": re.sub(r"\.pdf$", "", title, flags=re.I).strip(),
                        "date": day.isoformat(),
                        "level": int(level.group(1)) if level else None,
                        "url": "https://www.nerc.com" + url if url.startswith("/") else url,
                    }
        if not found:
            # The service answered but nothing sat where alerts live. That means the layout moved,
            # which is a failure, never "no alerts".
            raise ValueError("no alert documents found")
        alerts = sorted(found.values(), key=lambda a: a["date"], reverse=True)
        top = alerts[: int(self.params["max_items"])]
        return Result(
            data={"alerts": top, "found": len(alerts)},
            summary=f"Latest: {top[0]['title']} ({top[0]['date']})",
            status="nominal",
            source_url=PAGE,
        )
