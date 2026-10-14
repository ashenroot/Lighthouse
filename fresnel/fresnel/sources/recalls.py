"""Product recalls: FDA enforcement reports (food) and CPSC consumer-product recalls. No keys."""
from __future__ import annotations

import re
from datetime import timedelta

from ..geo import STATE_NAMES
from .base import Result, Source


class FdaRecalls(Source):
    id = "fda_recalls"
    page = "https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts"  # shown as the link until the first fetch succeeds
    title = "FDA recalls reaching your state"
    panel = "recalls"
    tier = "primary"
    default_interval = 7200
    # Lists recalls from the last lookback_days whose distribution is nationwide or includes the state.
    # A Class I recall (serious health risk) reported within active_days turns the panel active.
    default_params = {"categories": ["food"], "lookback_days": 30, "active_days": 3, "max_items": 10}

    def _reaches(self, pattern: str) -> bool:
        state = self.ctx.location.state.upper()
        return bool(re.search(rf"\b(nationwide|{state}|{re.escape(STATE_NAMES[state])})\b", pattern or "", re.IGNORECASE))

    async def fetch(self) -> Result:
        today = self.ctx.now().date()
        start = (today - timedelta(days=int(self.params["lookback_days"]))).strftime("%Y%m%d")
        items, seen = [], set()
        for category in self.params["categories"]:
            resp = await self.ctx.client.get(
                f"https://api.fda.gov/{category}/enforcement.json",
                params={"search": f"report_date:[{start} TO {today.strftime('%Y%m%d')}]", "sort": "report_date:desc", "limit": 100},
            )
            if resp.status_code == 404:  # openFDA answers 404 when a query matches nothing
                continue
            resp.raise_for_status()
            for r in resp.json()["results"]:
                if not self._reaches(r.get("distribution_pattern")):
                    continue
                key = (category, r.get("event_id"))
                if key in seen:  # openFDA lists one row per product; show each recall event once
                    continue
                seen.add(key)
                items.append(
                    {
                        "recall_number": r.get("recall_number"),
                        "category": category,
                        "class": r.get("classification"),
                        "firm": r.get("recalling_firm"),
                        "product": (r.get("product_description") or "")[:200],
                        "reason": (r.get("reason_for_recall") or "")[:300],
                        "reported": f"{r['report_date'][:4]}-{r['report_date'][4:6]}-{r['report_date'][6:8]}",
                        "url": f"https://www.accessdata.fda.gov/scripts/ires/index.cfm?Event={r.get('event_id')}",
                    }
                )
        items.sort(key=lambda i: (i["reported"], i["class"] or ""), reverse=True)
        cutoff = (today - timedelta(days=int(self.params["active_days"]))).isoformat()
        serious = [i for i in items if i["class"] == "Class I" and i["reported"] >= cutoff]
        class_one = sum(1 for i in items if i["class"] == "Class I")
        summary = f"{len(items)} recalls in {self.params['lookback_days']} days ({class_one} Class I)" if items else "No recent recalls reaching your state"
        return Result(
            data={"recalls": items[: int(self.params["max_items"])], "count": len(items), "class_i": class_one},
            summary=summary,
            status="active" if serious else "nominal",
            source_url="https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts",
        )


class CpscRecalls(Source):
    id = "cpsc_recalls"
    page = "https://www.cpsc.gov/Recalls"  # shown as the link until the first fetch succeeds
    title = "Consumer product recalls (CPSC)"
    panel = "recalls"
    tier = "primary"
    default_interval = 7200
    # Informational: CPSC does not grade severity, so this source lists recalls and stays nominal.
    default_params = {"lookback_days": 14, "max_items": 10}

    async def fetch(self) -> Result:
        start = (self.ctx.now().date() - timedelta(days=int(self.params["lookback_days"]))).isoformat()
        resp = await self.ctx.client.get(
            "https://www.saferproducts.gov/RestWebServices/Recall",
            params={"format": "json", "RecallDateStart": start},
        )
        resp.raise_for_status()
        rows = resp.json()
        rows.sort(key=lambda r: r["RecallDate"], reverse=True)
        recalls = [
            {
                "number": r.get("RecallNumber"),
                "title": r.get("Title"),
                "date": (r.get("RecallDate") or "")[:10],
                "hazard": ((r.get("Hazards") or [{}])[0].get("Name") or "")[:300],
                "url": r.get("URL"),
            }
            for r in rows
        ]
        summary = f"{len(recalls)} recalls in the last {self.params['lookback_days']} days" if recalls else "No recent recalls"
        return Result(
            data={"recalls": recalls[: int(self.params["max_items"])], "count": len(recalls)},
            summary=summary,
            status="nominal",
            source_url="https://www.cpsc.gov/Recalls",
        )
