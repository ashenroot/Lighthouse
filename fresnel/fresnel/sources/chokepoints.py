"""Shipping chokepoint traffic from the IMF's PortWatch (ArcGIS feature service, no key).

PortWatch counts daily vessel transits through major chokepoints from satellite ship-tracking data. Each
chokepoint is judged against its own history: the latest week's daily average against the 90 days before it
and against the year before it. A large drop against either is a supply-chain signal. The year window keeps a
long disruption visible after the 90-day average has already absorbed it. The data lags by a few days, and the
summary says how old it is.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from statistics import fmean

from .base import Result, Source, SourceError

API = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query"
PAGE = "https://portwatch.imf.org/"
RATE_WAIT = 65  # seconds; the service meters per minute and says "retry after 60"
RATE_TRIES = 4
FLAKE_WAIT = 4  # seconds; the service's gateway sometimes answers a valid query with a 400 or 499 and then accepts it
DEFAULT_NAMES = ["Panama Canal", "Suez Canal", "Bab el-Mandeb Strait", "Strait of Hormuz", "Malacca Strait", "Taiwan Strait"]


class Chokepoints(Source):
    id = "chokepoints"
    title = "Shipping chokepoints (IMF PortWatch)"
    panel = "supply"
    page = PAGE
    tier = "primary"
    default_interval = 21600
    # names: PortWatch chokepoint names. drop_pct: a drop of the latest week below the 90-day or the one-year
    # average by at least this much turns the panel active. max_age_days: data older than this is a failure.
    # min_baseline: chokepoints averaging fewer daily transits are shown but not rated, because small counts are noisy.
    default_params = {"names": DEFAULT_NAMES, "drop_pct": 30, "recent_days": 7, "baseline_days": 90,
                      "year_days": 365, "max_age_days": 10, "min_baseline": 5}

    async def _query(self, where: str, stats: list[dict], group: bool) -> list[dict]:
        import json
        params = {"where": where, "outStatistics": json.dumps(stats), "f": "json"}
        if group:
            params["groupByFieldsForStatistics"] = "portname"
        for attempt in range(RATE_TRIES):
            resp = await self.ctx.client.get(API, params=params)
            resp.raise_for_status()
            body = resp.json()
            err = body.get("error")
            if isinstance(err, dict) and attempt < RATE_TRIES - 1:
                # a 429 means the per-minute quota is spent, so one long wait clears it; other codes are brief gateway flakes
                await asyncio.sleep(RATE_WAIT if err.get("code") == 429 else FLAKE_WAIT)
                continue
            break
        if "error" in body:
            code = body["error"].get("code") if isinstance(body["error"], dict) else None
            raise SourceError("upstream is rate limiting requests, will retry" if code == 429
                              else f"upstream refused the query (code {code})" if code else "upstream query error")
        return [f["attributes"] for f in body.get("features", [])]

    async def fetch(self) -> Result:
        names = [str(n) for n in self.params["names"]]
        if not names:
            raise SourceError("no chokepoints named")
        recent_n, base_n, year_n = int(self.params["recent_days"]), int(self.params["baseline_days"]), int(self.params["year_days"])
        today = self.ctx.now().date()
        quoted = ",".join("'" + n.replace("'", "''") + "'" for n in names)
        # The service holds a row per chokepoint per day. Only averages are needed, so the service does the
        # summing: a handful of small grouped queries instead of paging a year of daily rows.
        stats = [{"statisticType": "max", "onStatisticField": "date", "outStatisticFieldName": "d"}]
        newest_rows = await self._query(f"portname IN ({quoted}) AND date >= '{(today - timedelta(days=recent_n + max(base_n, year_n))).isoformat()}'", stats, group=True)
        newest_days = [str(r["d"])[:10] for r in newest_rows if r.get("d")]
        if not newest_days:
            raise SourceError("no data returned")
        newest = date.fromisoformat(max(newest_days))
        age = (today - newest).days
        if age > int(self.params["max_age_days"]):
            raise SourceError(f"upstream data is {age} days old")
        windows = {
            "recent": (newest - timedelta(days=recent_n - 1), newest),
            "base": (newest - timedelta(days=recent_n + base_n - 1), newest - timedelta(days=recent_n)),
            "year": (newest - timedelta(days=recent_n + year_n - 1), newest - timedelta(days=recent_n)),
        }
        sums = [{"statisticType": "sum", "onStatisticField": "n_total", "outStatisticFieldName": "s"},
                {"statisticType": "count", "onStatisticField": "n_total", "outStatisticFieldName": "n"}]
        got: dict[str, dict[str, tuple[int, int]]] = {}
        for label, (lo, hi) in windows.items():
            rows_ = await self._query(f"portname IN ({quoted}) AND date >= '{lo.isoformat()}' AND date <= '{hi.isoformat()}'", sums, group=True)
            got[label] = {r["portname"]: (int(r.get("s") or 0), int(r.get("n") or 0)) for r in rows_}
        if not any(got["recent"].values()):
            raise SourceError("no data returned")

        floor, min_base = float(self.params["drop_pct"]), float(self.params["min_baseline"])
        rows = []
        for name in names:
            (rs, rn), (bs, bn), (ys, yn) = (got[w].get(name, (0, 0)) for w in ("recent", "base", "year"))
            if rn < recent_n - 2 or bn < base_n * 2 // 3:
                rows.append({"name": name, "rated": False, "reason": "not enough data"})
                continue
            r_avg, b_avg = rs / rn, bs / bn
            y_avg = ys / yn if yn >= year_n * 2 // 3 else None
            if max(b_avg, y_avg or 0) < min_base:
                rows.append({"name": name, "rated": False, "reason": "low traffic", "recent_avg": round(r_avg, 1), "baseline_avg": round(b_avg, 1)})
                continue
            change = (r_avg - b_avg) / b_avg * 100 if b_avg else 0.0
            year_change = (r_avg - y_avg) / y_avg * 100 if y_avg else None
            worst = min(change, year_change if year_change is not None else change)
            rows.append({"name": name, "rated": True, "recent_avg": round(r_avg, 1), "baseline_avg": round(b_avg, 1),
                         "change_pct": round(change, 1), "year_avg": round(y_avg, 1) if y_avg else None,
                         "year_change_pct": round(year_change, 1) if year_change is not None else None,
                         "worst_pct": round(worst, 1), "active": worst <= -floor})
        if not any(r["rated"] for r in rows):
            raise SourceError("not enough data to rate any chokepoint")

        down = [r for r in rows if r.get("active")]
        unrated = [r["name"] for r in rows if not r["rated"]]
        if down:
            def phrase(r):
                basis = "1-year" if r["year_change_pct"] is not None and r["year_change_pct"] <= r["change_pct"] else "90-day"
                return f"{r['name']} {r['worst_pct']:.0f}% vs {basis} normal"
            summary = "; ".join(phrase(r) for r in sorted(down, key=lambda r: r["worst_pct"])[:3])
        else:
            summary = f"All {sum(1 for r in rows if r['rated'])} rated chokepoints within {floor:.0f}% of their normal"
        if unrated:
            summary += f"; not rated: {', '.join(unrated)}"
        summary += f" (data through {newest.isoformat()})"
        return Result(
            data={"chokepoints": rows, "through": newest.isoformat(), "age_days": age, "drop_pct": floor,
                  "recent_days": recent_n, "baseline_days": base_n, "year_days": year_n},
            summary=summary, status="active" if down else "nominal", source_url=PAGE,
        )
