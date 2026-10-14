"""USGS earthquakes near the configured point (earthquake.usgs.gov, no key)."""
from __future__ import annotations

from .base import Result, Source

PAGE = "https://earthquake.usgs.gov/earthquakes/map/"


class UsgsQuakes(Source):
    id = "usgs_quakes"
    page = "https://earthquake.usgs.gov/earthquakes/map/"  # shown as the link until the first fetch succeeds
    title = "Earthquakes near home (USGS)"
    panel = "local"
    tier = "primary"
    default_interval = 600
    # Radius in km around the point; magnitudes at or above active_mag within active_hours turn it active.
    default_params = {"radius_km": 300, "lookback_days": 7, "min_mag": 2.5, "active_mag": 4.0, "active_hours": 24, "max_items": 10}

    async def fetch(self) -> Result:
        loc, now = self.ctx.location, self.ctx.now()
        start = now.timestamp() - int(self.params["lookback_days"]) * 86400
        from datetime import datetime, timezone

        resp = await self.ctx.client.get(
            "https://earthquake.usgs.gov/fdsnws/event/1/query",
            params={
                "format": "geojson",
                "latitude": f"{loc.lat:.4f}",
                "longitude": f"{loc.lon:.4f}",
                "maxradiuskm": self.params["radius_km"],
                "minmagnitude": self.params["min_mag"],
                "starttime": datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                "orderby": "time",
                "limit": 50,
            },
        )
        resp.raise_for_status()
        quakes = []
        for f in resp.json()["features"]:
            p = f["properties"]
            quakes.append(
                {
                    "id": f.get("id"),
                    "mag": p.get("mag"),
                    "place": p.get("place"),
                    "time": datetime.fromtimestamp(p["time"] / 1000, timezone.utc).isoformat().replace("+00:00", "Z"),
                    "url": p.get("url"),
                }
            )
        cutoff = (now.timestamp() - float(self.params["active_hours"]) * 3600) * 1000
        recent_big = [
            q for f, q in zip(resp.json()["features"], quakes)
            if (q["mag"] or 0) >= float(self.params["active_mag"]) and f["properties"]["time"] >= cutoff
        ]
        top = max(quakes, key=lambda q: q["mag"] or 0, default=None)
        if not quakes:
            summary = f"No M{self.params['min_mag']}+ within {self.params['radius_km']} km in {self.params['lookback_days']} days"
        else:
            summary = f"{len(quakes)} in {self.params['lookback_days']} days; largest M{top['mag']} {top['place']}"
        return Result(
            data={"quakes": quakes[: int(self.params["max_items"])], "count": len(quakes)},
            summary=summary,
            status="active" if recent_big else "nominal",
            source_url=PAGE,
        )
