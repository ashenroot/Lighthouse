"""Retail gasoline and diesel prices for your region, from the EIA (api.eia.gov, free key).

EIA publishes weekly retail prices by region (the PADD districts) and for the U.S. A price that has risen a
long way in a few weeks is the early sign of a fuel supply problem: a refinery or pipeline outage, a hurricane
on the Gulf Coast, or a shock to world oil. The panel goes active on a rise in your region, and also shows the
change against the past year and the national price. Data is weekly, published on Mondays or Tuesdays.
"""
from __future__ import annotations

from datetime import date, timedelta

from .base import Result, Source, SourceError
from .eia import eia_data

PAGE = "https://www.eia.gov/petroleum/gasdiesel/"
PRODUCTS = {"EPMR": "Regular gasoline", "EPD2D": "Diesel"}

# EIA's retail price regions by state. R1X New England, R1Y Central Atlantic, R1Z Lower Atlantic, R20 Midwest,
# R30 Gulf Coast, R40 Rocky Mountain, R50 West Coast.
_REGIONS = {
    "R1X": "CT ME MA NH RI VT", "R1Y": "DE DC MD NJ NY PA", "R1Z": "FL GA NC SC VA WV",
    "R20": "IL IN IA KS KY MI MN MO NE ND OH OK SD TN WI", "R30": "AL AR LA MS NM TX",
    "R40": "CO ID MT UT WY", "R50": "AK AZ CA HI NV OR WA",
}
REGION_OF_STATE = {state: code for code, states in _REGIONS.items() for state in states.split()}


def _pct(now: float, then: float) -> float:
    return (now - then) / then * 100


class EiaFuelPrices(Source):
    id = "eia_fuel_prices"
    title = "Fuel prices (EIA)"
    panel = "supply"
    page = PAGE
    tier = "primary"
    default_interval = 21600
    key = "eia"
    # region: an EIA region code (R1X, R1Y, R1Z, R20, R30, R40, R50, or NUS for the whole U.S.); empty means the one your
    # state is in. rise_pct: a regional price up by at least this much over `weeks` weeks turns the panel active.
    # max_age_days: older data is a failure.
    default_params = {"region": "", "rise_pct": 10, "weeks": 4, "max_age_days": 14}

    async def fetch(self) -> Result:
        region = str(self.params["region"] or REGION_OF_STATE.get(self.ctx.location.state.upper(), "")).upper()
        if not region:
            raise SourceError("set region: no EIA price region is known for your state")
        weeks, rise = int(self.params["weeks"]), float(self.params["rise_pct"])
        today = self.ctx.now().date()
        start = (today - timedelta(weeks=weeks + 56)).isoformat()
        areas = [region] if region == "NUS" else [region, "NUS"]
        rows, _ = await eia_data(self, "petroleum/pri/gnd", {"duoarea": areas, "product": list(PRODUCTS)}, frequency="weekly", start=start, length=1000)

        series: dict[tuple[str, str], dict[str, float]] = {}
        names: dict[str, str] = {}
        for r in rows:
            if r.get("value") in (None, ""):
                continue
            series.setdefault((r["duoarea"], r["product"]), {})[r["period"]] = float(r["value"])
            names[r["duoarea"]] = r.get("area-name") or r["duoarea"]
        if not any(area == region for area, _ in series):
            raise SourceError(f"no price data for region {region}")
        newest = date.fromisoformat(max(p for s in series.values() for p in s))
        if (today - newest).days > int(self.params["max_age_days"]):
            raise SourceError(f"upstream data is {(today - newest).days} days old")

        def stats(area: str, product: str) -> dict | None:
            s = series.get((area, product))
            if not s:
                return None
            periods = sorted(s, reverse=True)
            if len(periods) <= weeks or date.fromisoformat(periods[0]) < newest - timedelta(days=10):
                return None  # too little history, or this series stopped publishing
            now = s[periods[0]]
            past_year = [s[p] for p in periods[1:53]]
            return {"price": now, "change_week_pct": _pct(now, s[periods[1]]), "change_weeks_pct": _pct(now, s[periods[weeks]]),
                    "vs_year_pct": _pct(now, sum(past_year) / len(past_year)) if len(past_year) >= 26 else None}

        products = []
        for code, label in PRODUCTS.items():
            mine = stats(region, code)
            if mine is None:
                products.append({"product": code, "label": label, "rated": False})
                continue
            nat = stats("NUS", code) if region != "NUS" else None
            products.append({"product": code, "label": label, "rated": True, **mine, "active": mine["change_weeks_pct"] >= rise,
                             "national": nat})
        rated = [p for p in products if p["rated"]]
        if not rated:
            raise SourceError("not enough price history")

        area = names.get(region, region)
        hot = [p for p in rated if p["active"]]
        if hot:
            summary = "; ".join(f"{p['label']} up {p['change_weeks_pct']:.0f}% in {weeks} weeks to ${p['price']:.2f}/gal ({area})" for p in hot)
        else:
            summary = f"{area}: " + ", ".join(f"{p['label'].lower()} ${p['price']:.2f} ({p['change_weeks_pct']:+.0f}% in {weeks} wk)" for p in rated)
        missing = [p["label"].lower() for p in products if not p["rated"]]
        if missing:
            summary += "; no recent data for " + ", ".join(missing)
        return Result(
            data={"region": {"code": region, "name": area}, "week": newest.isoformat(), "weeks": weeks, "rise_pct": rise, "products": products},
            summary=summary, status="active" if hot else "nominal", source_url=PAGE,
        )
