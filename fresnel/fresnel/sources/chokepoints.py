"""Shipping chokepoint traffic from the IMF's PortWatch (ArcGIS feature service, no key).

PortWatch counts daily vessel transits through major chokepoints from satellite ship-tracking data. Each
chokepoint is judged against its own history: the latest week's daily average against the 90 days before it
and against the year before it. A large drop against either is a supply-chain signal. The year window keeps a
long disruption visible after the 90-day average has already absorbed it. The data lags by a few days, and the
summary says how old it is.
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import fmean

from .base import Result, Source, SourceError

API = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query"
PAGE = "https://portwatch.imf.org/"
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

    async def fetch(self) -> Result:
        names = [str(n) for n in self.params["names"]]
        if not names:
            raise SourceError("no chokepoints named")
        recent_n, base_n, year_n = int(self.params["recent_days"]), int(self.params["baseline_days"]), int(self.params["year_days"])
        today = self.ctx.now().date()
        start = today - timedelta(days=recent_n + max(base_n, year_n) + int(self.params["max_age_days"]) + 7)
        quoted = ",".join("'" + n.replace("'", "''") + "'" for n in names)
        series: dict[str, dict[date, int]] = {}
        offset = 0
        for _ in range(20):  # the service caps rows per request, so page until it says there are no more
            resp = await self.ctx.client.get(API, params={
                "where": f"portname IN ({quoted}) AND date >= '{start.isoformat()}'",
                "outFields": "date,portname,n_total", "returnGeometry": "false",
                "orderByFields": "date,portname", "resultOffset": offset, "resultRecordCount": 1000, "f": "json",
            })
            resp.raise_for_status()
            body = resp.json()
            if "error" in body:
                raise SourceError("upstream query error")
            features = body["features"]
            for f in features:
                a = f["attributes"]
                if a.get("n_total") is None:
                    continue
                series.setdefault(a["portname"], {})[date.fromisoformat(a["date"])] = int(a["n_total"])
            if not body.get("exceededTransferLimit"):
                break
            if not features:
                raise SourceError("upstream paging stalled")
            offset += len(features)
        else:
            raise SourceError("upstream result too large")
        if not series:
            raise SourceError("no data returned")

        newest = max(max(s) for s in series.values())
        age = (today - newest).days
        if age > int(self.params["max_age_days"]):
            raise SourceError(f"upstream data is {age} days old")
        recent_days = [newest - timedelta(days=i) for i in range(recent_n)]
        base_days = [newest - timedelta(days=recent_n + i) for i in range(base_n)]
        year_days = [newest - timedelta(days=recent_n + i) for i in range(year_n)]

        floor, min_base = float(self.params["drop_pct"]), float(self.params["min_baseline"])
        rows = []
        for name in names:
            s = series.get(name, {})
            recent = [s[d] for d in recent_days if d in s]
            base = [s[d] for d in base_days if d in s]
            if len(recent) < recent_n - 2 or len(base) < base_n * 2 // 3:
                rows.append({"name": name, "rated": False, "reason": "not enough data"})
                continue
            year = [s[d] for d in year_days if d in s]
            r_avg, b_avg = fmean(recent), fmean(base)
            y_avg = fmean(year) if len(year) >= year_n * 2 // 3 else None
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
