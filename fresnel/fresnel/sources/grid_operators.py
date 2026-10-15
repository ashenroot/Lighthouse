"""Links to the regional grid operators' own dashboards.

There is no keyless, reliable alert feed for most operators, so this panel links to the
dashboards each one maintains instead of guessing at their data. Nothing is fetched.
"""
from __future__ import annotations

from .base import Result, Source

OPERATORS = {
    "ercot": ("ERCOT", "Texas", "https://www.ercot.com/gridmktinfo/dashboards"),
    "caiso": ("CAISO", "California", "https://www.caiso.com/todays-outlook"),
    "pjm": ("PJM", "Mid-Atlantic and Ohio Valley", "https://www.pjm.com/markets-and-operations/ops-analysis"),
    "miso": ("MISO", "Midcontinent", "https://www.misoenergy.org/markets-and-operations/real-time--market-data/real-time-displays/"),
    "spp": ("SPP", "Southwest Power Pool", "https://www.spp.org/markets-operations/current-grid-conditions/"),
    "nyiso": ("NYISO", "New York", "https://www.nyiso.com/real-time-dashboard"),
    "isone": ("ISO-NE", "New England", "https://www.iso-ne.com/isoexpress/"),
    "eia": ("EIA Grid Monitor", "every region, including utilities outside the ISO markets",
            "https://www.eia.gov/electricity/gridmonitor/"),
}


class GridOperators(Source):
    id = "grid_operators"
    title = "Regional grid dashboards"
    panel = "grid"
    page = "https://www.eia.gov/electricity/gridmonitor/"
    tier = "primary"
    default_interval = 86400
    # operators: ids from OPERATORS to show. links: your own, as {name, url} (for example your utility).
    default_params = {"operators": list(OPERATORS), "links": []}

    async def fetch(self) -> Result:
        wanted = self.params["operators"]
        unknown = [o for o in wanted if o not in OPERATORS]
        if unknown:
            raise ValueError("unknown operators: " + ", ".join(unknown))
        links = [{"name": OPERATORS[o][0], "region": OPERATORS[o][1], "url": OPERATORS[o][2]} for o in wanted]
        for extra in self.params["links"]:
            name, url = str(extra["name"]), str(extra["url"])
            if not url.startswith(("https://", "http://")):
                raise ValueError("link is not http(s)")
            links.append({"name": name, "region": extra.get("region", ""), "url": url})
        return Result(data={"links": links}, summary=f"{len(links)} dashboards", status="nominal", source_url=self.page)
