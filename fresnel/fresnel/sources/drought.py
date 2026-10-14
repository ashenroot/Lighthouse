"""US Drought Monitor for the home county (usdmdataservices.unl.edu, no key)."""
from __future__ import annotations

from datetime import timedelta

from .base import Result, Source

API = "https://usdmdataservices.unl.edu/api/CountyStatistics/GetDroughtSeverityStatisticsByAreaPercent"
LABELS = {"d0": "D0 abnormally dry", "d1": "D1 moderate", "d2": "D2 severe", "d3": "D3 extreme", "d4": "D4 exceptional"}


def _us(d) -> str:
    return f"{d.month}/{d.day}/{d.year}"


class Drought(Source):
    id = "drought"
    page = "https://droughtmonitor.unl.edu/"  # shown as the link until the first fetch succeeds
    title = "Drought, home county"
    panel = "environment"
    tier = "primary"
    default_interval = 21600  # the map updates weekly
    # Active when at least active_pct percent of the county is in active_category or worse.
    default_params = {"active_category": "d2", "active_pct": 50.0}

    async def fetch(self) -> Result:
        loc, today = self.ctx.location, self.ctx.now().date()
        resp = await self.ctx.client.get(
            API,
            params={
                "aoi": loc.county_fips,
                "startdate": _us(today - timedelta(days=28)),
                "enddate": _us(today),
                "statisticsType": 1,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        rows = sorted(resp.json(), key=lambda r: r["mapDate"], reverse=True)
        latest = rows[0]  # IndexError on an empty answer counts as a bad response
        pct = {k: float(latest[k]) for k in ("none", "d0", "d1", "d2", "d3", "d4")}
        worst = next((k for k in ("d4", "d3", "d2", "d1", "d0") if pct[k] >= 50.0), None)
        category = self.params["active_category"]
        active = pct[category] >= float(self.params["active_pct"])
        if worst:
            summary = f"{LABELS[worst]} or worse over {pct[worst]:.0f}% of {latest.get('county', 'county')}"
        elif pct["d0"] > 0:
            summary = f"{pct['d0']:.0f}% abnormally dry or worse"
        else:
            summary = "No drought conditions"
        return Result(
            data={
                "county": latest.get("county"),
                "map_date": latest["mapDate"][:10],
                "percent_area": pct,
                "history": [{"map_date": r["mapDate"][:10], "d1": r["d1"], "d2": r["d2"], "d3": r["d3"]} for r in rows[:5]],
            },
            summary=summary,
            status="active" if active else "nominal",
            source_url="https://droughtmonitor.unl.edu/CurrentMap/StateDroughtMonitor.aspx?" + loc.state.upper(),
        )
