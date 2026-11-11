"""Forecast outlooks at your location from NOAA's mapservices (no key): SPC severe storms and WPC flash flooding.

These sit ahead of the warnings. An outlook says which category of risk the forecasters see for your point
today, tomorrow and the day after, so there is time to prepare before a watch or warning exists. Each day is
looked up by point and also checked against the service's own valid and expire times, so a product that has
stopped updating shows as not available instead of as a calm day. A point outside every risk area means the
forecasters outlined no risk there. If the whole layer is empty, no risk areas were outlined anywhere.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from .base import Result, Source, SourceError

SERVICES = "https://mapservices.weather.noaa.gov/vector/rest/services"
WHEN = ["today", "tomorrow", "in 3 days", "in 4 days", "in 5 days"]


class _Outlook(Source):
    """Shared by both outlooks. A subclass sets the service, its layer ids (one per day) and its category scale."""

    service = ""
    layers: list[int] = []
    scale: dict[str, int] = {}
    risk_noun = ""
    short_noun = ""

    def _category(self, attrs: dict) -> tuple[str, int]:
        raise NotImplementedError

    def _window(self, attrs: dict) -> tuple[datetime, datetime]:
        raise NotImplementedError

    async def _query(self, layer: int, point: bool, fields: str) -> list[dict]:
        params = {"outFields": fields, "returnGeometry": "false", "f": "json"}
        if point:
            loc = self.ctx.location
            params.update({"geometry": f"{loc.lon},{loc.lat}", "geometryType": "esriGeometryPoint", "inSR": "4326",
                           "spatialRel": "esriSpatialRelIntersects"})
        else:
            params["where"] = "1=1"
        resp = await self.ctx.client.get(f"{SERVICES}/{self.service}/MapServer/{layer}/query", params=params)
        resp.raise_for_status()
        body = resp.json()
        if body.get("error"):
            raise SourceError("the outlook service returned an error")
        feats = body["features"]
        if not isinstance(feats, list):
            raise ValueError("features is not a list")
        return [f["attributes"] for f in feats]

    async def _day(self, index: int) -> dict:
        layer = self.layers[index]
        here, everything = await asyncio.gather(self._query(layer, True, self.fields), self._query(layer, False, self.fields))
        now = self.ctx.now()
        if everything:
            newest = max(self._window(a)[1] for a in everything)
            if newest < now:
                age = int((now - newest).total_seconds() // 3600)
                raise SourceError(f"expired {age} h ago")
        best = max((self._category(a) for a in here), key=lambda c: c[1], default=None)
        return {"day": index + 1, "when": WHEN[index], "category": best[0] if best else None, "rank": best[1] if best else 0,
                "areas_nationwide": bool(everything)}

    async def fetch(self) -> Result:
        floor_name = str(self.params["active_min"]).lower()
        floors = {name.lower(): rank for name, rank in self.scale.items() if rank >= self.first_alert_rank}
        if floor_name not in floors:
            raise SourceError("active_min must be one of " + ", ".join(sorted(floors, key=floors.get)))
        floor = floors[floor_name]
        days = max(1, min(int(self.params["days"]), len(self.layers)))
        results = await asyncio.gather(*(self._day(i) for i in range(days)), return_exceptions=True)
        rows, problems = [], []
        for i, res in enumerate(results):
            if isinstance(res, SourceError):
                problems.append(f"day {i + 1}: {res}")
                rows.append({"day": i + 1, "when": WHEN[i], "category": None, "rank": 0, "unchecked": True})
            elif isinstance(res, (httpx.HTTPError, KeyError, ValueError, TypeError)):
                problems.append(f"day {i + 1} not available")
                rows.append({"day": i + 1, "when": WHEN[i], "category": None, "rank": 0, "unchecked": True})
            elif isinstance(res, BaseException):
                raise res
            else:
                res["active"] = res["rank"] >= floor
                rows.append(res)
        if rows[0].get("unchecked"):  # the first day is the one that matters most, so it cannot be skipped quietly
            raise SourceError("today's outlook unavailable: " + problems[0].removeprefix("day 1: ")[:100])
        if len(problems) == days:
            raise SourceError("no outlook available: " + "; ".join(problems)[:120])

        active = [r for r in rows if r.get("active")]
        if active:
            first = active[0]
            summary = f"{first['category'].capitalize()} {self.risk_noun} {first['when']}" + "".join(
                f"; {r['category']} {r['when']}" for r in active[1:3])
        else:
            parts = [f"{r['when']}: {r['category'] or 'none'}" for r in rows if not r.get("unchecked")]
            summary = f"No {self.short_noun} at or above {floor_name} " + f"({'; '.join(parts)})"
        if problems:
            summary += "; " + "; ".join(problems)
        return Result(
            data={"days": rows, "active_min": floor_name},
            summary=summary, status="active" if active else "nominal", source_url=self.page,
        )


def _spc_time(text: str) -> datetime:
    return datetime.strptime(text, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)


class SpcOutlook(_Outlook):
    id = "spc_outlook"
    title = "Severe storm outlook (SPC)"
    panel = "local"
    page = "https://www.spc.noaa.gov/products/outlook/"
    tier = "primary"
    default_interval = 1800
    service = "outlooks/SPC_wx_outlks"
    layers = [1, 9, 17]  # Day 1, 2 and 3 categorical outlooks
    scale = {"tstm": 0, "mrgl": 1, "slgt": 2, "enh": 3, "mdt": 4, "high": 5}
    first_alert_rank = 1
    risk_noun = "risk of severe storms"
    short_noun = "severe-storm risk"
    fields = "label,valid,expire,dn"
    # active_min: the lowest category that turns the panel active (mrgl, slgt, enh, mdt or high). days: 1 to 3.
    default_params = {"active_min": "enh", "days": 3}
    NAMES = {"TSTM": "general thunderstorms", "MRGL": "marginal", "SLGT": "slight", "ENH": "enhanced", "MDT": "moderate", "HIGH": "high"}

    def _category(self, attrs):
        code = str(attrs["label"]).upper()
        if code not in self.NAMES:
            raise ValueError("unknown outlook category")
        return self.NAMES[code], self.scale[code.lower()]

    def _window(self, attrs):
        return _spc_time(attrs["valid"]), _spc_time(attrs["expire"])


class WpcRainfallOutlook(_Outlook):
    id = "wpc_rainfall_outlook"
    title = "Flash flood outlook (WPC)"
    panel = "environment"
    page = "https://www.wpc.ncep.noaa.gov/qpf/excess_rain.shtml"
    tier = "primary"
    default_interval = 1800
    service = "hazards/wpc_precip_hazards"
    layers = [0, 1, 2]  # Excessive Rainfall Day 1, 2 and 3
    scale = {"marginal": 1, "slight": 2, "moderate": 3, "high": 4}
    first_alert_rank = 1
    risk_noun = "risk of flash flooding"
    short_noun = "flash-flood risk"
    fields = "outlook,start_time,end_time"
    # active_min: the lowest category that turns the panel active (marginal, slight, moderate or high). days: 1 to 3.
    default_params = {"active_min": "moderate", "days": 3}

    def _category(self, attrs):
        word = str(attrs["outlook"]).split()[0].lower()
        if word not in self.scale:
            raise ValueError("unknown outlook category")
        return word, self.scale[word]

    def _window(self, attrs):
        parse = lambda t: datetime.strptime(t, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return parse(attrs["start_time"]), parse(attrs["end_time"])
