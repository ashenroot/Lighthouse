"""Measured air quality near the home point, from EPA AirNow monitors (airnowapi.org, free key).

Replaces the modeled Open-Meteo `air_quality` source while the AirNow key is set. AirNow reports the closest
monitor reading for each pollutant within 50 miles, as an hourly NowCast AQI. The AQI scale and categories are the EPA's.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .air_quality import category
from .base import Result, Source, SourceError

API = "https://www.airnowapi.org/aq/observation/current/ziplatlong/"
PAGE = "https://www.airnow.gov/"


class AirNowAqi(Source):
    id = "airnow_aqi"
    title = "Air quality (US AQI, monitors)"
    panel = "airfire"
    page = PAGE
    tier = "primary"
    default_interval = 1800
    key = "airnow"
    supersedes = ("air_quality",)
    # active_aqi: the US AQI at which the panel turns active; 101 is "unhealthy for sensitive groups".
    # max_age_minutes: a reading older than this is a failure. AirNow posts each hour's value a little after the hour.
    default_params = {"active_aqi": 101, "max_age_minutes": 240}

    async def fetch(self) -> Result:
        loc = self.ctx.location
        resp = await self.ctx.client.get(API, params={"format": "application/json", "latitude": f"{loc.lat:.4f}",
                                                       "longitude": f"{loc.lon:.4f}", "API_KEY": self.api_key()})
        self.check_key(resp)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict):  # errors, and empty results, arrive as HTTP 200 with a WebServiceError envelope
            msg = (body.get("WebServiceError") or [{}])[0].get("Message") or "unexpected response"
            raise SourceError(f"AirNow: {msg}"[:160])
        readings = []
        for r in body:
            if r.get("nowcastAQI") is None or r["nowcastAQI"] < 0:
                continue  # -999 and similar mean the monitor has no current value
            tz = timezone(timedelta(hours=_OFFSETS.get(r.get("localTimeZone"), 0)))
            when = datetime.fromisoformat(f"{r['dateObserved']}T{r['hourObserved']}").replace(tzinfo=tz)
            readings.append({"pollutant": r["parameterName"], "aqi": int(r["nowcastAQI"]), "category": category(r["nowcastAQI"]),
                             "site": r.get("siteName"), "area": r.get("reportingAreaName"), "time": when.astimezone(timezone.utc).isoformat(),
                             "_when": when})
        if not readings:
            raise SourceError("AirNow returned no current readings")
        newest = max(r["_when"] for r in readings)
        age_min = (self.ctx.now() - newest).total_seconds() / 60
        if age_min > float(self.params["max_age_minutes"]):
            raise SourceError(f"upstream data is {int(age_min // 60)} hours old")
        for r in readings:
            del r["_when"]
        readings.sort(key=lambda r: -r["aqi"])
        top = readings[0]
        aqi = top["aqi"]
        active = aqi >= int(self.params["active_aqi"])
        area = top["area"] or "your area"
        summary = f"US AQI {aqi}, {category(aqi).lower()} ({top['pollutant']} leads, {area} monitors)" if aqi > 50 else f"US AQI {aqi}, good ({area} monitors)"
        return Result(data={"aqi": aqi, "category": category(aqi), "dominant": top["pollutant"], "area": area, "readings": readings,
                            "time": newest.astimezone(timezone.utc).isoformat(), "active_aqi": int(self.params["active_aqi"])},
                      summary=summary, status="active" if active else "nominal", source_url=PAGE)


# AirNow reports local standard or daylight time by abbreviation.
_OFFSETS = {"EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6, "PST": -8, "PDT": -7, "AKST": -9, "AKDT": -8, "HST": -10, "AST": -4}
