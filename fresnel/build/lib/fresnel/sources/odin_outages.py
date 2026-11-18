"""Customer power outages reported to ODIN (ORNL / DOE Office of Electricity; no key).

Utilities report to ODIN voluntarily, so every number here is a floor: a county with no
record is a county nobody reported, not a county with the lights on. The summary says so.
"""
from __future__ import annotations

from ..geo import STATE_NAMES
from .base import Result, Source

API = "https://ornl.opendatasoft.com/api/explore/v2.1/catalog/datasets/odin-real-time-outages-county/records"
PAGE = "https://odin.ornl.gov/"
NOTE = "Utilities report to ODIN voluntarily. Counts are a floor, and no record is not an all-clear."


class OdinOutages(Source):
    id = "odin_outages"
    title = "Power outages (ODIN)"
    panel = "grid"
    page = PAGE
    tier = "primary"
    default_interval = 900  # the portal allows 5,000 calls a day; this uses about 200
    default_params = {"county_active": 1000, "state_active": 25000, "max_items": 5}

    async def _query(self, **params) -> list[dict]:
        resp = await self.ctx.client.get(API, params=params)
        resp.raise_for_status()
        results = resp.json()["results"]
        if not isinstance(results, list):
            raise ValueError("results is not a list")
        return results

    async def fetch(self) -> Result:
        loc = self.ctx.location
        state_name = STATE_NAMES.get(loc.state.upper())
        by_state = await self._query(
            select="state,sum(metersaffected) as out,count(*) as n", group_by="state", order_by="out desc", limit=100)
        counties = await self._query(
            select="communitydescriptor,county,sum(metersaffected) as out,count(*) as n",
            where=f'startswith(communitydescriptor,"{loc.county_fips[:2]}")',
            group_by="communitydescriptor,county", order_by="out desc", limit=100)

        for row in by_state:
            row["out"] = int(row.get("out") or 0)
        national_out = sum(r["out"] for r in by_state)
        state_row = next((r for r in by_state if r.get("state") == state_name), None)
        state_out = state_row["out"] if state_row else 0
        county_row = next((c for c in counties if c.get("communitydescriptor") == loc.county_fips), None)
        county_out = int(county_row.get("out") or 0) if county_row else 0
        top_counties = [
            {"county": c.get("county"), "fips": c.get("communitydescriptor"), "out": int(c.get("out") or 0)}
            for c in counties[: int(self.params["max_items"])] if int(c.get("out") or 0) > 0
        ]

        active = county_out >= int(self.params["county_active"]) or state_out >= int(self.params["state_active"])
        place = loc.state.upper()
        if state_row:
            local = f"{place} {state_out:,} out" + (f" ({county_out:,} in your county)" if county_row else "")
        else:
            local = f"none reported in {place}"
        return Result(
            data={
                "national": {
                    "customers_out": national_out,
                    "states_reporting": sum(1 for r in by_state if r["out"] > 0),
                    "top_states": [{"state": r.get("state"), "out": r["out"]} for r in by_state[:5] if r["out"] > 0],
                },
                "state": {"name": state_name, "abbr": place, "customers_out": state_out, "reporting": bool(state_row)},
                "county": {"fips": loc.county_fips, "customers_out": county_out, "reporting": bool(county_row)},
                "top_counties": top_counties,
                "note": NOTE,
            },
            summary=f"{local}; US {national_out:,} reported. {NOTE}",
            status="active" if active else "nominal",
            source_url=PAGE,
        )
