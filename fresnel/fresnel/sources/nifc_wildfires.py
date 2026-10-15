"""Wildfires near the home point: the National Interagency Fire Center's current incident list (no key).

Incidents come from the Wildland Fire Interagency Geospatial Services (WFIGS) feed, which dispatch centers
and incident teams update. A new or small fire can take time to appear, so an empty list means none are
reported, not that none are burning.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import cos, radians

from ..geo import haversine_km
from .base import Result, Source, SourceError

API = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query"
PAGE = "https://www.nifc.gov/fire-information/nfn"
FIELDS = "IncidentName,POOState,POOCounty,IncidentSize,PercentContained,ModifiedOnDateTime_dt,InitialLatitude,InitialLongitude,IrwinID"


class NifcWildfires(Source):
    id = "nifc_wildfires"
    title = "Wildfires (NIFC)"
    panel = "environment"
    page = PAGE
    tier = "primary"
    default_interval = 1800
    # radius_km: wildfires within this distance of the home point. min_acres: the size at which an uncontained fire
    # turns the panel active. max_age_days: an incident not updated for this long is ignored.
    default_params = {"radius_km": 100, "min_acres": 100, "max_age_days": 7, "max_listed": 6}

    async def fetch(self) -> Result:
        loc = self.ctx.location
        radius = float(self.params["radius_km"])
        dlat, dlon = radius / 111.0, radius / (111.0 * max(cos(radians(loc.lat)), 0.01))
        resp = await self.ctx.client.get(API, params={
            "where": "IncidentTypeCategory='WF'", "outFields": FIELDS, "returnGeometry": "false", "f": "json",
            "geometry": f"{loc.lon - dlon:.4f},{loc.lat - dlat:.4f},{loc.lon + dlon:.4f},{loc.lat + dlat:.4f}",
            "geometryType": "esriGeometryEnvelope", "inSR": "4326", "spatialRel": "esriSpatialRelIntersects",
        })
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise SourceError("upstream query error")
        if body.get("exceededTransferLimit"):
            raise SourceError("upstream result truncated")

        oldest = (self.ctx.now() - timedelta(days=int(self.params["max_age_days"]))).astimezone(timezone.utc)
        min_acres = float(self.params["min_acres"])
        fires = []
        for f in body["features"]:
            a = f["attributes"]
            if a.get("InitialLatitude") is None or a.get("InitialLongitude") is None:
                continue
            distance = haversine_km(loc.lat, loc.lon, a["InitialLatitude"], a["InitialLongitude"])
            modified = a.get("ModifiedOnDateTime_dt")
            if distance > radius or (modified is not None and datetime.fromtimestamp(modified / 1000, timezone.utc) < oldest):
                continue
            contained = a.get("PercentContained")
            if contained is not None and contained >= 100:
                continue
            acres = a.get("IncidentSize")
            fires.append({
                "name": (a.get("IncidentName") or "Unnamed").strip().title(),
                "county": a.get("POOCounty"), "state": (a.get("POOState") or "").replace("US-", ""),
                "acres": acres, "percent_contained": contained, "distance_km": round(distance, 1),
                "big": acres is not None and acres >= min_acres,
                "url": PAGE,
            })
        fires.sort(key=lambda x: (not x["big"], x["distance_km"]))
        big = [x for x in fires if x["big"]]
        if big:
            top = big[0]
            summary = f"{top['name']} fire, {top['acres']:,.0f} acres, {top['distance_km']:.0f} km away" + (
                f" (+{len(big) - 1} more)" if len(big) > 1 else "")
        elif fires:
            summary = f"{len(fires)} small uncontained fire{'s' if len(fires) > 1 else ''} within {radius:.0f} km, none over {min_acres:,.0f} acres"
        else:
            summary = f"No uncontained wildfires reported within {radius:.0f} km"
        return Result(
            data={"fires": fires[: int(self.params["max_listed"])], "count": len(fires), "radius_km": radius, "min_acres": min_acres},
            summary=summary, status="active" if big else "nominal", source_url=PAGE,
        )
