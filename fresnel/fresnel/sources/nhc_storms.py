"""National Hurricane Center active storms (nhc.noaa.gov, no key)."""
from __future__ import annotations

from .base import Result, Source

BASINS = {"al": "Atlantic", "ep": "E. Pacific", "cp": "C. Pacific"}


class NhcStorms(Source):
    id = "nhc_storms"
    page = "https://www.nhc.noaa.gov/"  # shown as the link until the first fetch succeeds
    title = "Active tropical cyclones (NHC)"
    panel = "tropical"
    tier = "primary"
    default_interval = 900
    # Basins that turn the panel active. Storms elsewhere are listed but stay nominal.
    default_params = {"basins": ["al"]}

    async def fetch(self) -> Result:
        resp = await self.ctx.client.get("https://www.nhc.noaa.gov/CurrentStorms.json")
        resp.raise_for_status()
        storms = []
        for s in resp.json().get("activeStorms") or []:
            sid = str(s.get("id") or "").lower()
            advisory = s.get("publicAdvisory") or {}
            storms.append(
                {
                    "id": sid,
                    "basin": sid[:2],
                    "name": s.get("name"),
                    "classification": s.get("classification"),
                    "intensity_kt": s.get("intensity"),
                    "pressure_mb": s.get("pressure"),
                    "lat": s.get("latitudeNumeric"),
                    "lon": s.get("longitudeNumeric"),
                    "movement": f"{s.get('movementDir')}° at {s.get('movementSpeed')} mph"
                    if s.get("movementDir") is not None and s.get("movementSpeed") is not None
                    else None,
                    "last_update": s.get("lastUpdate"),
                    "advisory_url": advisory.get("url"),
                }
            )
        watched = {b.lower() for b in self.params["basins"]}
        active = any(s["basin"] in watched for s in storms)

        if not storms:
            summary = "No active tropical cyclones"
        else:
            parts = [
                f"{s['classification'] or 'TC'} {s['name'] or s['id']} ({BASINS.get(s['basin'], s['basin'])})"
                for s in storms[:4]
            ]
            summary = ", ".join(parts) + ("" if len(storms) <= 4 else f" and {len(storms) - 4} more")

        return Result(
            data={"storms": storms, "watched_basins": sorted(watched)},
            summary=summary,
            status="active" if active else "nominal",
            source_url="https://www.nhc.noaa.gov/",
        )
