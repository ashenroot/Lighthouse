"""River flood categories from NOAA's National Water Prediction Service (api.water.noaa.gov, no key).

NWS forecast gauges carry official flood thresholds, so the category is NOAA's own call: action, minor,
moderate or major, for the observed stage and for the forecast. Gauges that are out of service or have
stale readings are counted as not reporting, never as fine.
"""
from __future__ import annotations

from datetime import datetime
from math import cos, radians

from ..geo import haversine_km
from .base import Result, Source, SourceError

API = "https://api.water.noaa.gov/nwps/v1/gauges"
PAGE = "https://water.noaa.gov/"
RANK = {"no_flooding": 0, "low_threshold": 0, "action": 1, "minor": 2, "moderate": 3, "major": 4}
LABEL = {1: "action stage", 2: "minor flooding", 3: "moderate flooding", 4: "major flooding"}
NOT_REPORTING = {"obs_not_current", "out_of_service"}


def _parse_time(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((text or "").replace("Z", "+00:00"))
    except ValueError:
        return None


class RiverStages(Source):
    id = "river_stages"
    title = "River flood stages (NWS)"
    panel = "environment"
    page = PAGE
    tier = "primary"
    default_interval = 900
    # radius_km: gauges within this distance of the home point. active_min: the lowest category that turns the
    # panel active (action | minor | moderate | major), judged on the observed stage and on the NWS forecast.
    # max_age_hours: an observation older than this means the gauge is not reporting.
    default_params = {"radius_km": 50, "active_min": "minor", "max_age_hours": 6, "max_listed": 8}

    async def fetch(self) -> Result:
        threshold = str(self.params["active_min"]).lower()
        names = {LABEL[r].split()[0]: r for r in LABEL}  # action, minor, moderate, major
        if threshold not in names:
            raise SourceError("active_min must be action, minor, moderate or major")
        floor = names[threshold]

        loc = self.ctx.location
        radius = float(self.params["radius_km"])
        dlat = radius / 111.0
        dlon = radius / (111.0 * max(cos(radians(loc.lat)), 0.01))
        resp = await self.ctx.client.get(API, params={
            "bbox.xmin": f"{loc.lon - dlon:.4f}", "bbox.xmax": f"{loc.lon + dlon:.4f}",
            "bbox.ymin": f"{loc.lat - dlat:.4f}", "bbox.ymax": f"{loc.lat + dlat:.4f}", "srid": "EPSG_4326",
        })
        resp.raise_for_status()
        listing = resp.json()["gauges"]
        if not isinstance(listing, list):
            raise ValueError("gauges is not a list")

        now = self.ctx.now()
        limit = float(self.params["max_age_hours"]) * 3600
        gauges = []
        for g in listing:
            distance = haversine_km(loc.lat, loc.lon, g["latitude"], g["longitude"])
            if distance > radius:
                continue
            obs, fc = g["status"]["observed"], g["status"]["forecast"]
            when = _parse_time(obs.get("validTime"))
            fresh = when is not None and when.year > 2000 and (now - when).total_seconds() <= limit
            obs_cat = obs.get("floodCategory")
            reporting = fresh and obs_cat not in NOT_REPORTING
            rated = reporting and obs_cat in RANK  # not_defined: the gauge has no flood thresholds
            obs_rank = RANK[obs_cat] if rated else 0
            fc_cat = fc.get("floodCategory")
            fc_rank = RANK.get(fc_cat, 0) if fc_cat in RANK and fc_cat != "no_flooding" else 0
            gauges.append({
                "lid": g["lid"], "name": g["name"], "state": (g.get("state") or {}).get("abbreviation"),
                "distance_km": round(distance, 1),
                "reporting": reporting, "rated": rated,
                "observed": {"category": obs_cat if reporting else None, "stage": obs.get("primary") if reporting else None,
                             "unit": obs.get("primaryUnit"), "time": obs.get("validTime") if reporting else None},
                "forecast": {"category": fc_cat if fc_rank else None, "stage": fc.get("primary") if fc_rank else None},
                "rank": max(obs_rank, fc_rank), "observed_rank": obs_rank, "forecast_rank": fc_rank,
                "url": f"https://water.noaa.gov/gauges/{g['lid']}",
            })
        gauges.sort(key=lambda g: g["distance_km"])

        if not gauges:
            return Result(data={"gauges": [], "radius_km": radius, "total": 0, "rated": 0, "not_reporting": 0},
                          summary=f"No NWS river forecast gauges within {radius:.0f} km", status="nominal", source_url=PAGE)
        rated = [g for g in gauges if g["rated"]]
        if not rated:
            raise SourceError("no gauges reporting")

        flagged = sorted((g for g in gauges if g["rank"] >= 1), key=lambda g: (-g["rank"], g["distance_km"]))
        active = [g for g in flagged if g["rank"] >= floor]
        rest = [g for g in gauges if g not in flagged and g["reporting"]]
        listed = (flagged + rest)[: int(self.params["max_listed"])]
        silent = len(gauges) - sum(1 for g in gauges if g["reporting"])

        if active:
            def phrase(g):
                if g["observed_rank"] >= floor:
                    return f"{LABEL[g['observed_rank']]} at {g['name']}"
                return f"{g['forecast']['category']} flooding forecast at {g['name']}"
            summary = "; ".join(phrase(g) for g in active[:2]) + (f" (+{len(active) - 2} more)" if len(active) > 2 else "")
        else:
            summary = f"No gauge at {LABEL[floor]} or worse among {len(rated)} rated within {radius:.0f} km"
            if flagged:
                summary += f"; {len(flagged)} at or forecast to reach a flood category"
            if silent:
                summary += f"; {silent} not reporting"
        return Result(
            data={"gauges": listed, "radius_km": radius, "total": len(gauges), "rated": len(rated), "not_reporting": silent,
                  "active_min": threshold},
            summary=summary,
            status="active" if active else "nominal",
            source_url=(active or listed or gauges)[0]["url"],
        )
