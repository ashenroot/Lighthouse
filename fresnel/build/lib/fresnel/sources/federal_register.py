"""Presidential documents from the Federal Register (federalregister.gov API, no key).

Lists recent executive orders as plain headlines. The panel goes active when a *new* national emergency is
declared. Annual "Continuation of the National Emergency" notices are routine renewals and are ignored.
Documents appear in the Federal Register a few days after they are signed.
"""
from __future__ import annotations

import re
from datetime import timedelta

from .base import Result, Source

API = "https://www.federalregister.gov/api/v1/documents.json"
PAGE = "https://www.federalregister.gov/presidential-documents"
_DECLARES = re.compile(r"\b(declaring|declaration of|declares)\b.*\bemergency\b", re.I)
_ROUTINE = re.compile(r"\b(continuation|anniversary)\b", re.I)


class FederalRegister(Source):
    id = "federal_register"
    title = "Presidential documents (Federal Register)"
    panel = "federal"
    page = PAGE
    tier = "primary"
    default_interval = 3600
    # window_days: how far back executive orders are listed. active_days: a new national emergency this recent turns it active.
    default_params = {"window_days": 21, "active_days": 7, "max_items": 6}

    async def fetch(self) -> Result:
        resp = await self.ctx.client.get(API, params=[
            ("conditions[type][]", "PRESDOCU"), ("order", "newest"), ("per_page", 40),
            ("fields[]", "title"), ("fields[]", "publication_date"), ("fields[]", "subtype"),
            ("fields[]", "document_number"), ("fields[]", "html_url"), ("fields[]", "executive_order_number"),
        ])
        resp.raise_for_status()
        docs = resp.json()["results"]

        today = self.ctx.now().date()
        window = (today - timedelta(days=int(self.params["window_days"]))).isoformat()
        active = (today - timedelta(days=int(self.params["active_days"]))).isoformat()
        emergencies, orders = [], []
        for d in docs:
            if d["publication_date"] < window:
                continue
            item = {"title": d["title"], "date": d["publication_date"], "url": d["html_url"], "number": d.get("executive_order_number")}
            if _DECLARES.search(d["title"]) and not _ROUTINE.search(d["title"]):
                emergencies.append(item)
            if d["subtype"] == "Executive Order":
                orders.append(item)
        new = [e for e in emergencies if e["date"] >= active]

        if new:
            summary = f"New national emergency ({new[0]['date']}): {new[0]['title']}" + (f" (+{len(new) - 1} more)" if len(new) > 1 else "")
        elif orders:
            summary = f"{len(orders)} executive order{'s' if len(orders) != 1 else ''} in {int(self.params['window_days'])} days; latest {orders[0]['date']}: {orders[0]['title']}"
        else:
            summary = f"No executive orders published in the last {int(self.params['window_days'])} days"
        return Result(
            data={"emergencies": emergencies[:3], "orders": orders[: int(self.params["max_items"])], "window_days": int(self.params["window_days"])},
            summary=summary, status="active" if new else "nominal",
            source_url=(new or orders or [{"url": PAGE}])[0]["url"],
        )
