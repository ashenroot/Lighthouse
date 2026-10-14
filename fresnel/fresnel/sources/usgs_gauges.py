"""USGS stream gauge heights near the home point (waterservices.usgs.gov, no key)."""
from __future__ import annotations

from ..geo import haversine_km
from .base import Result, Source

API = "https://waterservices.usgs.gov/nwis/iv/"


class UsgsGauges(Source):
    id = "usgs_gauges"
    page = "https://waterdata.usgs.gov/"  # shown as the link until the first fetch succeeds
    title = "Stream gauges (USGS)"
    panel = "environment"
    tier = "primary"
    default_interval = 900
    # sites: explicit USGS site numbers. Empty means the nearest max_sites gauges within radius_deg.
    # flood_stage_ft: {site_number: feet}. A gauge at or above its stage turns the panel active.
    # Without stages this source reports readings and never goes active.
    default_params = {"sites": [], "radius_deg": 0.5, "max_sites": 4, "flood_stage_ft": {}}

    async def fetch(self) -> Result:
        loc = self.ctx.location
        base = {"format": "json", "parameterCd": "00065", "siteStatus": "active"}
        sites = [str(s) for s in self.params["sites"]]
        if not sites:
            # A wide area with a day of history is slow, so first pick the nearest gauges from latest readings.
            r = float(self.params["radius_deg"])
            found = await self.ctx.client.get(
                API, params={**base, "bBox": f"{loc.lon - r:.4f},{loc.lat - r:.4f},{loc.lon + r:.4f},{loc.lat + r:.4f}"}
            )
            found.raise_for_status()
            near = {}
            for ts in found.json()["value"]["timeSeries"]:
                info = ts["sourceInfo"]
                if not any(v.get("value") not in (None, "") for v in ts["values"][0]["value"]):
                    continue
                geo = info["geoLocation"]["geogLocation"]
                near[info["siteCode"][0]["value"]] = haversine_km(loc.lat, loc.lon, geo["latitude"], geo["longitude"])
            sites = sorted(near, key=near.get)[: int(self.params["max_sites"])]
        if not sites:
            return Result(data={"gauges": []}, summary="No gauge readings available nearby",
                          status="nominal", source_url="https://waterdata.usgs.gov/")
        resp = await self.ctx.client.get(API, params={**base, "sites": ",".join(sites), "period": "P1D"})
        resp.raise_for_status()
        series = resp.json()["value"]["timeSeries"]

        stages = {str(k): float(v) for k, v in (self.params["flood_stage_ft"] or {}).items()}
        gauges = []
        for ts in series:
            info = ts["sourceInfo"]
            site = info["siteCode"][0]["value"]
            no_data = ts["variable"].get("noDataValue")
            points = [
                (v["dateTime"], float(v["value"]))
                for v in ts["values"][0]["value"]
                if v.get("value") not in (None, "") and float(v["value"]) != no_data
            ]
            if not points:
                continue
            geo = info["geoLocation"]["geogLocation"]
            height = points[-1][1]
            gauges.append(
                {
                    "site": site,
                    "name": info["siteName"],
                    "distance_km": round(haversine_km(loc.lat, loc.lon, geo["latitude"], geo["longitude"]), 1),
                    "height_ft": height,
                    "change_24h_ft": round(height - points[0][1], 2),
                    "time": points[-1][0],
                    "flood_stage_ft": stages.get(site),
                    "above_flood_stage": site in stages and height >= stages[site],
                    "url": f"https://waterdata.usgs.gov/monitoring-location/{site}/",
                }
            )
        gauges.sort(key=lambda g: g["distance_km"])

        flooding = [g for g in gauges if g["above_flood_stage"]]
        if flooding:
            summary = "Above flood stage: " + ", ".join(g["name"] for g in flooding[:2])
        elif gauges:
            rising = max(gauges, key=lambda g: g["change_24h_ft"])
            summary = f"{len(gauges)} gauges; largest 24h change {rising['change_24h_ft']:+.2f} ft at {rising['name']}"
        else:
            summary = "No gauge readings available nearby"
        return Result(
            data={"gauges": gauges},
            summary=summary,
            status="active" if flooding else "nominal",
            source_url=gauges[0]["url"] if gauges else "https://waterdata.usgs.gov/",
        )
