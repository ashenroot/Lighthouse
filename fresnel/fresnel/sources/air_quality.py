"""Air quality at the home point: US AQI from Open-Meteo's air quality API (no key).

These are model estimates (Copernicus CAMS), not readings from a monitor, so a local source such as smoke
or an industrial plume can be missed. The AQI scale and categories are the EPA's.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .base import Result, Source, SourceError

API = "https://air-quality-api.open-meteo.com/v1/air-quality"
PAGE = "https://open-meteo.com/en/docs/air-quality-api"
BANDS = [(50, "Good"), (100, "Moderate"), (150, "Unhealthy for sensitive groups"), (200, "Unhealthy"),
         (300, "Very unhealthy"), (10_000, "Hazardous")]
POLLUTANTS = {"us_aqi_pm2_5": "PM2.5", "us_aqi_pm10": "PM10", "us_aqi_ozone": "ozone"}


def category(aqi: float) -> str:
    return next(name for top, name in BANDS if aqi <= top)


class AirQuality(Source):
    id = "air_quality"
    title = "Air quality (US AQI, modeled)"
    panel = "environment"
    page = PAGE
    tier = "primary"
    default_interval = 3600
    # active_aqi: the US AQI at which the panel turns active. 101 is "unhealthy for sensitive groups".
    # max_age_minutes: a model run older than this is a failure, not a reading.
    default_params = {"active_aqi": 101, "max_age_minutes": 180}

    async def fetch(self) -> Result:
        loc = self.ctx.location
        resp = await self.ctx.client.get(API, params={
            "latitude": f"{loc.lat:.4f}", "longitude": f"{loc.lon:.4f}", "timezone": "UTC",
            "current": "us_aqi,pm2_5,pm10,ozone," + ",".join(POLLUTANTS),
        })
        resp.raise_for_status()
        cur = resp.json()["current"]
        when = datetime.fromisoformat(cur["time"]).replace(tzinfo=timezone.utc)
        age_min = (self.ctx.now() - when).total_seconds() / 60
        if age_min > float(self.params["max_age_minutes"]):
            raise SourceError(f"upstream data is {int(age_min // 60)} hours old")
        if cur.get("us_aqi") is None:
            raise SourceError("no AQI value returned")
        aqi = round(float(cur["us_aqi"]))
        parts = {name: cur.get(key) for key, name in POLLUTANTS.items() if cur.get(key) is not None}
        dominant = max(parts, key=parts.get) if parts else None
        active = aqi >= int(self.params["active_aqi"])
        summary = f"US AQI {aqi}, {category(aqi).lower()}" + (f" ({dominant} leads)" if dominant and aqi > 50 else "")
        return Result(
            data={"aqi": aqi, "category": category(aqi), "dominant": dominant, "pm2_5": cur.get("pm2_5"),
                  "pm10": cur.get("pm10"), "ozone": cur.get("ozone"), "time": when.isoformat(), "active_aqi": int(self.params["active_aqi"])},
            summary=summary, status="active" if active else "nominal", source_url=PAGE,
        )
