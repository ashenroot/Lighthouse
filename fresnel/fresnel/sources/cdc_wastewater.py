"""CDC wastewater viral activity for the home state (data.cdc.gov, no key)."""
from __future__ import annotations

from statistics import median

from ..geo import STATE_NAMES
from .base import Result, Source

API = "https://data.cdc.gov/resource/atcp-73re.json"
LEVELS = ["Very Low", "Low", "Moderate", "High", "Very High"]


class CdcWastewater(Source):
    id = "cdc_wastewater"
    page = "https://www.cdc.gov/nwss/rv/COVID19-statetrend.html"  # shown as the link until the first fetch succeeds
    title = "Wastewater viral activity (CDC)"
    panel = "health"
    tier = "primary"
    default_interval = 21600  # the data updates weekly
    # Active when at least active_share of a pathogen's sites report High or Very High.
    default_params = {"active_share": 0.5}

    async def fetch(self) -> Result:
        state = STATE_NAMES[self.ctx.location.state.upper()]
        resp = await self.ctx.client.get(
            API,
            params={"$where": f"state_territory='{state}'", "$order": "week_end DESC", "$limit": 2000},
        )
        resp.raise_for_status()
        rows = resp.json()
        week = rows[0]["week_end"]  # IndexError on an empty answer counts as a bad response
        by_pathogen: dict[str, list[int]] = {}
        for r in rows:
            level = r.get("site_wval_category")
            if r["week_end"] == week and level in LEVELS:
                by_pathogen.setdefault(r["pathogen_target"], []).append(LEVELS.index(level))

        pathogens = {}
        active_names = []
        for name, levels in sorted(by_pathogen.items()):
            high_share = sum(1 for lv in levels if lv >= 3) / len(levels)
            pathogens[name] = {
                "sites": len(levels),
                "median_level": LEVELS[int(median(levels))],
                "high_share": round(high_share, 2),
            }
            if high_share >= float(self.params["active_share"]):
                active_names.append(name)
        if not pathogens:
            raise ValueError("no wastewater sites reported")
        summary = ", ".join(f"{n.replace(' virus', '')} {p['median_level'].lower()}" for n, p in pathogens.items())
        return Result(
            data={"state": state, "week_end": week[:10], "pathogens": pathogens, "elevated": active_names},
            summary=summary,
            status="active" if active_names else "nominal",
            source_url="https://www.cdc.gov/nwss/rv/COVID19-statetrend.html",
        )
