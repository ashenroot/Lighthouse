"""Electric grid demand for a balancing authority, from the EIA's hourly grid monitor (api.eia.gov, free key).

Demand is judged against that balancing authority's own history: how close the latest hour, and the
day-ahead forecast, are to its peak over the past year. The panel goes active when either is near the peak,
which is when the grid is under strain and a heat wave or cold snap can turn into outages. The peak is the
99.5th percentile of the year's hourly demand, so a few bad data points in the EIA feed cannot set it.
Readings are about one to two hours behind. Pick your balancing authority code (for example SOCO for the
Southern Company, which covers Georgia) from the EIA grid monitor.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil

from .base import Result, Source, SourceError
from .eia import eia_data

ROUTE = "electricity/rto/region-data"
PAGE = "https://www.eia.gov/electricity/gridmonitor/"
HOUR = "%Y-%m-%dT%H"


def _hour(text: str) -> datetime:
    return datetime.strptime(text, HOUR).replace(tzinfo=timezone.utc)


class EiaGridDemand(Source):
    id = "eia_grid_demand"
    title = "Grid demand (EIA)"
    panel = "grid"
    page = PAGE
    tier = "primary"
    default_interval = 1800
    key = "eia"
    needs = ("balancing_authority",)
    # balancing_authority: an EIA balancing authority code, such as SOCO, PJM, MISO or ERCO. peak_pct: demand, or the
    # day-ahead forecast, at this percent of the one-year peak turns the panel active. max_age_hours: an older latest
    # reading is a failure. baseline_days: how far back the peak is measured.
    default_params = {"balancing_authority": "", "peak_pct": 90, "max_age_hours": 12, "baseline_days": 365}

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self._peaks: dict[str, tuple[datetime, float]] = {}

    async def _peak(self, ba: str, now: datetime) -> float:
        cached = self._peaks.get(ba)
        if cached and now - cached[0] < timedelta(hours=24):
            return cached[1]
        start = (now - timedelta(days=int(self.params["baseline_days"]))).strftime(HOUR)
        values: list[float] = []
        offset = 0
        for _ in range(6):  # at most 30,000 rows: a year of hourly readings is about 8,800
            rows, total = await eia_data(self, ROUTE, {"respondent": [ba], "type": ["D"]}, frequency="hourly", start=start, length=5000, offset=offset)
            values.extend(float(r["value"]) for r in rows if r.get("value") not in (None, "") and float(r["value"]) > 0)
            offset += 5000
            if not rows or offset >= total:
                break
        if len(values) < 1000:
            raise SourceError("not enough history to judge demand")
        values.sort()
        peak = values[min(len(values) - 1, ceil(0.995 * len(values)) - 1)]
        self._peaks[ba] = (now, peak)
        return peak

    async def fetch(self) -> Result:
        ba = str(self.params["balancing_authority"]).strip().upper()
        now = self.ctx.now().astimezone(timezone.utc)
        rows, _ = await eia_data(self, ROUTE, {"respondent": [ba], "type": ["D", "DF"]}, frequency="hourly",
                                 start=(now - timedelta(hours=72)).strftime(HOUR), length=500)
        if not rows:
            raise SourceError(f"no data for balancing authority {ba} (check the code)")
        actual = {_hour(r["period"]): float(r["value"]) for r in rows if r["type"] == "D" and r.get("value") not in (None, "")}
        forecast = {_hour(r["period"]): float(r["value"]) for r in rows if r["type"] == "DF" and r.get("value") not in (None, "")}
        if not actual:
            raise SourceError("no demand readings")
        when = max(actual)
        age = (now - when).total_seconds() / 3600
        if age > float(self.params["max_age_hours"]):
            raise SourceError(f"latest demand reading is {int(age)} hours old")
        peak = await self._peak(ba, now)
        limit = float(self.params["peak_pct"])

        demand = actual[when]
        pct = demand / peak * 100
        ahead = {t: v for t, v in forecast.items() if now - timedelta(hours=1) <= t <= now + timedelta(hours=24)}
        fc = None
        if ahead:
            at = max(ahead, key=ahead.get)
            fc = {"max_mwh": ahead[at], "at": at.isoformat(), "pct_of_peak": ahead[at] / peak * 100}
        hot_now, hot_fc = pct >= limit, bool(fc and fc["pct_of_peak"] >= limit)

        parts = []
        if hot_now:
            parts.append(f"{ba} demand at {pct:.0f}% of its one-year peak")
        if hot_fc:
            parts.append(f"{ba} day-ahead forecast reaches {fc['pct_of_peak']:.0f}% of peak around {_hour(fc['at'][:13]).strftime('%H')}Z")
        if not parts:
            parts.append(f"{ba} demand {demand / 1000:.1f} GW ({pct:.0f}% of its one-year peak)" + (
                f"; forecast peak {fc['max_mwh'] / 1000:.1f} GW ({fc['pct_of_peak']:.0f}%)" if fc else ""))
        name = next((r.get("respondent-name") for r in rows if r.get("respondent-name")), ba)
        return Result(
            data={"ba": ba, "name": name, "demand_mwh": demand, "period": when.isoformat(), "age_hours": round(age, 1), "pct_of_peak": pct,
                  "peak_mwh": peak, "forecast": fc, "peak_pct": limit},
            summary="; ".join(parts), status="active" if hot_now or hot_fc else "nominal", source_url=PAGE,
        )
