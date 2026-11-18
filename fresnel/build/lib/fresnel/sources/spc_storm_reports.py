"""Preliminary local storm reports from NOAA's Storm Prediction Center (spc.noaa.gov, no key).

Tornado, wind and hail reports come from spotters, the public and emergency managers, entered by National
Weather Service offices. They are preliminary and unverified, and an empty list means none were
reported to the SPC, not that none occurred. The report day runs 12Z to 12Z.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from ..geo import haversine_km
from .base import Result, Source, SourceError

URL = "https://www.spc.noaa.gov/climo/reports/today.csv"
PAGE = "https://www.spc.noaa.gov/climo/reports/today.html"
KINDS = {"F_Scale": "tornado", "Speed": "wind", "Size": "hail"}


def _report_time(hhmm: str, now: datetime) -> datetime:
    """Times are HHMM in UTC within a 12Z-to-12Z day: 1200-2359 fall on the day it began, 0000-1159 on the next."""
    start = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if now < start:
        start -= timedelta(days=1)
    t = start.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:4]))
    return t + timedelta(days=1) if int(hhmm[:2]) < 12 else t


class SpcStormReports(Source):
    id = "spc_storm_reports"
    title = "Storm reports (SPC)"
    panel = "local"
    page = PAGE
    tier = "primary"
    default_interval = 600
    # radius_km: reports within this distance are listed. active_hours: a significant report this recent turns it active.
    # Significant: any tornado, wind damage or 58+ mph wind, hail of an inch or more (min_hail_in).
    # max_file_age_hours: a file not regenerated for this long means the feed is stuck.
    default_params = {"radius_km": 150, "active_hours": 6, "min_wind_mph": 58, "min_hail_in": 1.0, "max_items": 8, "max_file_age_hours": 24}

    async def fetch(self) -> Result:
        resp = await self.ctx.client.get(URL)
        resp.raise_for_status()
        now = self.ctx.now().astimezone(timezone.utc)
        modified = resp.headers.get("last-modified")
        if modified:
            age = (now - parsedate_to_datetime(modified)).total_seconds() / 3600
            if age > float(self.params["max_file_age_hours"]):
                raise SourceError(f"report file is {int(age)} hours old")

        loc = self.ctx.location
        radius = float(self.params["radius_km"])
        kind = None
        seen_headers = 0
        total = 0
        reports = []
        for row in csv.reader(io.StringIO(resp.text)):
            if len(row) == 8 and row[1] in KINDS and row[0] == "Time":
                kind, seen_headers = KINDS[row[1]], seen_headers + 1
                continue
            if kind is None or len(row) != 8:
                continue
            total += 1
            when = _report_time(row[0].zfill(4), now)
            distance = haversine_km(loc.lat, loc.lon, float(row[5]), float(row[6]))
            if distance > radius:
                continue
            magnitude = row[1]
            if kind == "hail":
                significant = int(magnitude) / 100 >= float(self.params["min_hail_in"])
                shown = f"{int(magnitude) / 100:.2f} in hail"
            elif kind == "wind":
                significant = magnitude == "UNK" or int(magnitude) >= int(self.params["min_wind_mph"])
                shown = "wind damage" if magnitude == "UNK" else f"{magnitude} mph wind"
            else:
                significant = True
                shown = "tornado" + ("" if magnitude == "UNK" else f" {magnitude}")
            reports.append({
                "kind": kind, "detail": shown, "time": when.isoformat(), "age_min": int((now - when).total_seconds() // 60),
                "location": f"{row[2]}, {row[3]} Co., {row[4]}", "distance_km": round(distance, 1),
                "significant": significant, "comments": row[7][:120],
            })
        if seen_headers < 3:
            raise SourceError("unexpected report file format")
        reports.sort(key=lambda r: r["age_min"])
        cutoff = float(self.params["active_hours"]) * 60
        current = sorted((r for r in reports if r["significant"] and r["age_min"] <= cutoff),
                         key=lambda r: (r["kind"] != "tornado", r["age_min"]))

        if current:
            top = current[0]
            summary = f"{top['detail'].capitalize()} report {top['distance_km']:.0f} km away, {top['age_min']} min ago" + (
                f" (+{len(current) - 1} more)" if len(current) > 1 else "")
        elif reports:
            summary = f"{len(reports)} storm report{'s' if len(reports) > 1 else ''} within {radius:.0f} km in this report day, none significant in the last {int(self.params['active_hours'])} h"
        else:
            summary = f"No storm reports within {radius:.0f} km in this report day"
        return Result(
            data={"reports": reports[: int(self.params["max_items"])], "in_range": len(reports), "nationwide": total, "radius_km": radius,
                  "active_hours": float(self.params["active_hours"])},
            summary=summary, status="active" if current else "nominal", source_url=PAGE,
        )
