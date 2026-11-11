"""Airport forecast: TAF flight category at the airports you list (aviationweather.gov, no key).

METAR says what the weather is now. The TAF says what the airport's forecasters expect over the next day,
so this panel can warn that fog or low cloud is coming. A TAF carries no flight category, so it is worked out
here from each forecast period's visibility and lowest broken or overcast layer, by the standard thresholds.
Periods marked TEMPO or PROB are possible, not prevailing: they are shown but do not turn the panel active.
"""
from __future__ import annotations

import re

from .aviation_weather import ORDER, icao
from .base import Result, Source

URL = "https://aviationweather.gov/api/data/taf"
PAGE = "https://aviationweather.gov/data/taf/"
POSSIBLE = {"TEMPO", "PROB"}


def _miles(text) -> float | None:
    """Visibility as the API gives it: 6+, 10+, 3, 1/2, 1 1/2, P6SM."""
    if text is None:
        return None
    parts = re.sub(r"[PM]|SM|\+", "", str(text).upper()).split()
    try:
        return sum(float(p.split("/")[0]) / float(p.split("/")[1]) if "/" in p else float(p) for p in parts) if parts else None
    except (ValueError, ZeroDivisionError):
        return None


def _ceiling(period: dict) -> int | None:
    bases = [c["base"] for c in period.get("clouds") or [] if c.get("cover") in ("BKN", "OVC", "OVX") and c.get("base") is not None]
    vv = period.get("vertVis")
    if vv is not None:  # an obscured sky counts as a ceiling; the API's unit is not documented, so accept hundreds of feet or feet
        bases.append(int(vv) * 100 if int(vv) < 100 else int(vv))
    return min(bases) if bases else None


def category(period: dict) -> str | None:
    vis, ceil = _miles(period.get("visib")), _ceiling(period)
    if vis is None and ceil is None and not period.get("clouds"):
        return None
    if (vis is not None and vis < 1) or (ceil is not None and ceil < 500):
        return "LIFR"
    if (vis is not None and vis < 3) or (ceil is not None and ceil < 1000):
        return "IFR"
    if (vis is not None and vis <= 5) or (ceil is not None and ceil <= 3000):
        return "MVFR"
    return "VFR"


def _hhz(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H") + "Z"


class AviationForecast(Source):
    id = "aviation_forecast"
    title = "Airport forecast (TAF)"
    panel = "transport"
    page = PAGE
    tier = "primary"
    default_interval = 1800
    needs = ("airports",)
    inherits = {"airports": "faa_airports"}  # the same airports as the FAA delay feed, unless set here
    # active_min: the worst forecast category that counts as active. hours: how far ahead to look.
    default_params = {"airports": [], "active_min": "IFR", "hours": 12}

    async def fetch(self) -> Result:
        wanted = [str(a).upper() for a in self.params["airports"]]
        threshold = str(self.params["active_min"]).upper()
        if threshold not in ORDER:
            raise ValueError("active_min must be one of " + ", ".join(ORDER))
        resp = await self.ctx.client.get(URL, params={"ids": ",".join(icao(a) for a in wanted), "format": "json"})
        resp.raise_for_status()
        tafs = resp.json() if resp.content else []
        if not isinstance(tafs, list):
            raise ValueError("tafs is not a list")
        by_id = {t.get("icaoId"): t for t in tafs}

        now = self.ctx.now().timestamp()
        horizon = now + float(self.params["hours"]) * 3600
        rows, usable = [], 0
        for code in wanted:
            taf = by_id.get(icao(code))
            if not taf or not taf.get("validTimeTo") or taf["validTimeTo"] < now or not taf.get("fcsts"):
                rows.append({"airport": code, "icao": icao(code), "forecast": False, "active": False})
                continue
            expected, possible = [], []
            for p in taf["fcsts"]:
                cat = category(p)
                if cat is None or p["timeTo"] <= now or p["timeFrom"] >= horizon:
                    continue
                entry = {"category": cat, "from": max(p["timeFrom"], now), "ceiling_ft": _ceiling(p)}
                (possible if p.get("fcstChange") in POSSIBLE else expected).append(entry)
            if not expected:
                rows.append({"airport": code, "icao": icao(code), "forecast": False, "active": False})
                continue
            usable += 1
            worst = max(expected, key=lambda e: (ORDER.index(e["category"]), -e["from"]))
            bad = [e for e in expected if ORDER.index(e["category"]) >= ORDER.index(threshold)]
            first_bad = min(bad, key=lambda e: e["from"]) if bad else None
            maybe = max(possible, key=lambda e: ORDER.index(e["category"]), default=None)
            rows.append({
                "airport": code, "icao": icao(code), "name": taf.get("name"), "forecast": True,
                "worst": worst["category"], "worst_ceiling_ft": worst["ceiling_ft"],
                "active": first_bad is not None,
                "bad_from": int(first_bad["from"]) if first_bad else None,
                "bad_category": max((e["category"] for e in bad), key=ORDER.index) if bad else None,
                "possible": maybe["category"] if maybe and ORDER.index(maybe["category"]) >= ORDER.index(threshold) else None,
                "valid_to": int(taf["validTimeTo"]), "hours": float(self.params["hours"]),
            })
        if not usable:
            raise ValueError("no current forecasts")

        bad = [r for r in rows if r["active"]]
        missing = [r["airport"] for r in rows if not r["forecast"]]
        if bad:
            summary = "; ".join(
                f"{r['airport']} {r['bad_category']} forecast {'now' if r['bad_from'] <= now + 60 else 'from ' + _hhz(r['bad_from'])}" for r in bad[:4])
        else:
            summary = f"No {threshold} or worse forecast in the next {int(float(self.params['hours']))} h at " + ", ".join(r["airport"] for r in rows if r["forecast"])
        if missing:
            summary += "; no forecast for " + ", ".join(missing)
        return Result(data={"airports": rows, "active_min": threshold}, summary=summary,
                      status="active" if bad else "nominal", source_url=PAGE)
