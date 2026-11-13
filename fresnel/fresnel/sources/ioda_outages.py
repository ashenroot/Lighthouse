"""Internet outages by country from IODA, Georgia Tech's Internet Outage Detection and Analysis (no key).

IODA fuses several independent measurements (BGP routing, active probing, a network telescope, Google
traffic) and groups anomalies into outage events. The watch list is yours to set. A watched country with an
event still running turns the panel active. Scores have no fixed scale, so they are shown as given and never
rated. Duration is shown because a multi-day event is usually a chronic condition (a filtered service, say),
while a fresh one in a country that is normally steady is the signal worth reading.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from .base import Result, Source, SourceError

API = "https://api.ioda.inetintel.cc.gatech.edu/v2/outages/events"
PAGE = "https://ioda.inetintel.cc.gatech.edu/"
DEFAULT_COUNTRIES = ["US", "UA", "RU", "IR", "IL", "TW", "VE"]
DATASOURCES = {"bgp": "BGP routing", "ping-slash24": "active probing", "merit-nt": "network telescope", "gtr": "Google traffic"}
NAMES = {
    "UA": "Ukraine", "RU": "Russia", "IR": "Iran", "IL": "Israel", "TW": "Taiwan", "VE": "Venezuela", "CN": "China", "KP": "North Korea",
    "BY": "Belarus", "SY": "Syria", "IQ": "Iraq", "LB": "Lebanon", "YE": "Yemen", "SA": "Saudi Arabia", "AE": "United Arab Emirates",
    "QA": "Qatar", "TR": "Turkey", "EG": "Egypt", "PK": "Pakistan", "IN": "India", "AF": "Afghanistan", "KR": "South Korea",
    "JP": "Japan", "PH": "Philippines", "CU": "Cuba", "MX": "Mexico", "CA": "Canada", "US": "United States", "SD": "Sudan",
    "ET": "Ethiopia", "LY": "Libya", "MM": "Myanmar", "KZ": "Kazakhstan", "PS": "Palestine", "JO": "Jordan", "GB": "United Kingdom",
}


def _span(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 90:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} h"
    return f"{minutes // 1440} days"


class IodaOutages(Source):
    id = "ioda_outages"
    title = "Internet outages (IODA)"
    panel = "geo"
    page = PAGE
    tier = "fragile"
    default_interval = 1800
    # countries: ISO two-letter codes to watch. window_hours: how far back events are looked for.
    # ongoing_slack_min: an event whose end falls within this many minutes of now counts as still running.
    # min_minutes: shorter events are listed but do not turn the panel active.
    default_params = {"countries": DEFAULT_COUNTRIES, "window_hours": 24, "ongoing_slack_min": 30, "min_minutes": 30, "max_listed": 8}

    async def _one(self, code: str, start: int, end: int) -> list[dict]:
        resp = await self.ctx.client.get(API, params={"from": start, "until": end, "entityType": "country", "entityCode": code, "limit": 20})
        resp.raise_for_status()
        body = resp.json()
        if body.get("error"):
            raise SourceError(f"IODA reported an error for {code}: {str(body['error'])[:60]}")
        events = body["data"]
        if not isinstance(events, list):
            raise ValueError("data is not a list")
        out = []
        for ev in events:
            began = int(ev["start"])
            length = int(ev["duration"])
            out.append({
                "code": code, "country": ev.get("location_name") or code,
                "start": datetime.fromtimestamp(began, timezone.utc).isoformat(), "duration_s": length,
                "end_ts": began + length, "source": DATASOURCES.get(ev.get("datasource"), ev.get("datasource")),
                "method": ev.get("method"), "score": float(ev["score"]),
            })
        return out

    async def fetch(self) -> Result:
        codes = [str(c).strip().upper() for c in self.params["countries"]]
        if not codes or not all(len(c) == 2 and c.isalpha() for c in codes):
            raise SourceError("countries must be two-letter codes")
        now = self.ctx.now()
        end = int(now.timestamp())
        start = int((now - timedelta(hours=float(self.params["window_hours"]))).timestamp())
        results = await asyncio.gather(*(self._one(c, start, end) for c in codes), return_exceptions=True)
        unchecked, events = [], []
        for code, res in zip(codes, results):
            if isinstance(res, SourceError):
                raise res
            if isinstance(res, BaseException):
                if not isinstance(res, (httpx.HTTPError, KeyError, ValueError, TypeError)):
                    raise res
                unchecked.append(code)
            else:
                events.extend(res)
        if len(unchecked) == len(codes):
            raise SourceError("IODA did not answer for any watched country")

        slack = float(self.params["ongoing_slack_min"]) * 60
        floor = float(self.params["min_minutes"]) * 60
        for ev in events:
            ev["ongoing"] = ev["end_ts"] >= end - slack
        for ev in events:
            ev["active"] = ev["ongoing"] and ev["duration_s"] >= floor
        current = [ev for ev in events if ev["active"]]
        events.sort(key=lambda ev: (not ev["ongoing"], -ev["score"]))
        current.sort(key=lambda ev: -ev["score"])

        by_country = []
        for code in codes:
            if code in unchecked:
                by_country.append({"code": code, "name": NAMES.get(code, code), "state": "unchecked"})
                continue
            mine = sorted((ev for ev in events if ev["code"] == code), key=lambda ev: (not ev["active"], not ev["ongoing"], -ev["score"]))
            if not mine:
                by_country.append({"code": code, "name": NAMES.get(code, code), "state": "clear"})
                continue
            top = mine[0]
            state = "outage" if top["active"] else "running" if top["ongoing"] else "ended"
            by_country.append({"code": code, "name": top["country"], "state": state, "duration_s": top["duration_s"],
                               "ended_ago_s": max(0, end - top["end_ts"]), "source": top["source"], "more": len(mine) - 1})

        if current:
            top = current[0]
            summary = f"{top['country']}: outage running {_span(top['duration_s'])} ({top['source']})" + (
                f" (+{len(current) - 1} more)" if len(current) > 1 else "")
        elif events:
            summary = f"{len(events)} recent outage event{'s' if len(events) > 1 else ''} in watched countries, none running now"
        else:
            summary = f"No outage events in the last {int(float(self.params['window_hours']))} h across {len(codes) - len(unchecked)} watched countr{'y' if len(codes) - len(unchecked) == 1 else 'ies'}"
        if unchecked:
            summary += f"; {', '.join(unchecked)} not checked"
        return Result(
            data={"countries": by_country, "events": events[: int(self.params["max_listed"])], "watched": codes, "unchecked": unchecked,
                  "window_hours": float(self.params["window_hours"]), "ongoing": len(current)},
            summary=summary, status="active" if current else "nominal", source_url=PAGE,
        )
