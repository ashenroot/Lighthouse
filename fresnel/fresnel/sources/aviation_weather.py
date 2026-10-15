"""Airport weather: METAR flight category at the airports you list (aviationweather.gov, no key).

Flight category is the standard summary of whether pilots can fly by sight: VFR, MVFR, IFR or LIFR,
from visibility and the lowest broken or overcast cloud layer. This complements the FAA delay feed,
because fog and low cloud cause delays before the FAA posts them.
"""
from __future__ import annotations

import re

from .base import Result, Source

URL = "https://aviationweather.gov/api/data/metar"
PAGE = "https://aviationweather.gov/data/metar/"
ORDER = ["VFR", "MVFR", "IFR", "LIFR"]


def icao(code: str) -> str:
    """FAA code to ICAO. Three letters in the contiguous US take a K. Anything else is taken as given."""
    code = code.strip().upper()
    return "K" + code if re.fullmatch(r"[A-Z]{3}", code) else code


def ceiling_ft(clouds: list[dict]) -> int | None:
    bases = [c.get("base") for c in clouds or [] if c.get("cover") in ("BKN", "OVC", "OVX") and c.get("base") is not None]
    return min(bases) if bases else None


class AviationWeather(Source):
    id = "aviation_weather"
    title = "Airport weather (METAR)"
    panel = "transport"
    page = PAGE
    tier = "primary"
    default_interval = 600
    needs = ("airports",)
    inherits = {"airports": "faa_airports"}  # the same airports as the FAA delay feed, unless set here
    # active_min: the worst flight category that counts as active. max_age_minutes: older reports are ignored.
    default_params = {"airports": [], "active_min": "IFR", "max_age_minutes": 180}

    async def fetch(self) -> Result:
        wanted = [str(a).upper() for a in self.params["airports"]]
        threshold = str(self.params["active_min"]).upper()
        if threshold not in ORDER:
            raise ValueError("active_min must be one of " + ", ".join(ORDER))
        resp = await self.ctx.client.get(URL, params={"ids": ",".join(icao(a) for a in wanted), "format": "json"})
        resp.raise_for_status()
        reports = resp.json() if resp.content else []
        if not isinstance(reports, list):
            raise ValueError("reports is not a list")
        by_id = {r.get("icaoId"): r for r in reports}

        now = self.ctx.now().timestamp()
        limit = int(self.params["max_age_minutes"]) * 60
        rows, fresh = [], 0
        for code in wanted:
            report = by_id.get(icao(code))
            age = now - report["obsTime"] if report and report.get("obsTime") else None
            if not report or age is None or age > limit or report.get("fltCat") not in ORDER:
                rows.append({"airport": code, "icao": icao(code), "category": None, "active": False})
                continue
            fresh += 1
            category = report["fltCat"]
            rows.append({
                "airport": code, "icao": icao(code), "name": report.get("name"), "category": category,
                "active": ORDER.index(category) >= ORDER.index(threshold),
                "visibility_sm": report.get("visib"), "ceiling_ft": ceiling_ft(report.get("clouds")),
                "wind_kt": report.get("wspd"), "gust_kt": report.get("wgst"), "weather": report.get("wxString"),
                "age_min": int(age // 60),
            })
        if not fresh:
            # Nothing usable came back. That is a failure to read weather, never calm weather.
            raise ValueError("no current observations")

        bad = [r for r in rows if r["active"]]
        missing = [r["airport"] for r in rows if r["category"] is None]
        if bad:
            summary = "; ".join(
                f"{r['airport']} {r['category']}" + (f" (ceiling {r['ceiling_ft']} ft)" if r["ceiling_ft"] is not None and r["ceiling_ft"] < 3000 else "")
                for r in bad[:4])
        else:
            summary = "No " + threshold + " or worse at " + ", ".join(r["airport"] for r in rows if r["category"])
        if missing:
            summary += "; no report for " + ", ".join(missing)
        return Result(data={"airports": rows, "active_min": threshold}, summary=summary,
                      status="active" if bad else "nominal", source_url=PAGE)
