"""FDIC bank failures (api.fdic.gov, no key).

Failures are rare, so this panel is mostly a count of days since the last one. A failure inside
`active_days` turns it active. The list follows FDIC's own publication, which can run a few days behind.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .base import Result, Source, SourceError

API = "https://api.fdic.gov/banks/failures"
PAGE = "https://www.fdic.gov/resources/resolutions/bank-failures/failed-bank-list/"


class FdicFailures(Source):
    id = "fdic_failures"
    title = "Bank failures (FDIC)"
    panel = "finance"
    page = PAGE
    tier = "primary"
    default_interval = 21600
    # active_days: a failure this recent turns the panel active. window_days: how far back the count and list reach.
    default_params = {"active_days": 14, "window_days": 365, "max_items": 5}

    async def fetch(self) -> Result:
        resp = await self.ctx.client.get(API, params={
            "sort_by": "FAILDATE", "sort_order": "DESC", "limit": 25,
            "fields": "NAME,CITYST,FAILDATE,QBFASSET,RESTYPE",
        })
        resp.raise_for_status()
        rows = resp.json()["data"]
        if not rows:
            raise SourceError("no failures returned")
        today = self.ctx.now().date()
        failures = []
        for r in rows:
            d = r["data"]
            failed = datetime.strptime(d["FAILDATE"], "%m/%d/%Y").date()
            assets = d.get("QBFASSET")
            failures.append({
                "name": " ".join(w if "&" in w else w.title() for w in d["NAME"].split()), "place": d.get("CITYST"), "date": failed.isoformat(),
                "days_ago": (today - failed).days, "type": d.get("RESTYPE"),
                "assets_usd": int(assets) * 1000 if assets is not None else None,  # FDIC reports thousands of dollars
            })
        failures.sort(key=lambda f: f["date"], reverse=True)
        window = [f for f in failures if f["days_ago"] <= int(self.params["window_days"])]
        recent = [f for f in failures if f["days_ago"] <= int(self.params["active_days"])]
        last = failures[0]
        if recent:
            summary = f"{last['name']} ({last['place']}) failed {last['date']}" + (f" (+{len(recent) - 1} more)" if len(recent) > 1 else "")
        else:
            summary = f"Last failure {last['days_ago']} days ago: {last['name']} ({last['place']}); {len(window)} in the past {int(self.params['window_days'])} days"
        return Result(
            data={"failures": failures[: int(self.params["max_items"])], "window_count": len(window),
                  "window_days": int(self.params["window_days"]), "days_since_last": last["days_ago"],
                  "active_days": int(self.params["active_days"])},
            summary=summary, status="active" if recent else "nominal", source_url=PAGE,
        )
