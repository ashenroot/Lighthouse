"""Hospital load and respiratory admissions for the home state (CDC NHSN, data.cdc.gov, no key).

Weekly state totals from hospitals reporting to the National Healthcare Safety Network: how full inpatient
and ICU beds are, and how many patients are in hospital with COVID-19, influenza and RSV. The number of
reporting hospitals shifts from week to week, so occupancy uses the reported percentages and the pathogen
trends compare patient counts only against the same state's earlier weeks.
"""
from __future__ import annotations

from datetime import date, datetime

from ..geo import STATE_NAMES
from .base import Result, Source, SourceError

API = "https://data.cdc.gov/resource/ua7e-t2fy.json"
PAGE = "https://www.cdc.gov/nhsn/psc/hospital-respiratory-reporting.html"
PATHOGENS = {"totalconfc19hosppats": "COVID-19", "totalconffluhosppats": "Influenza", "totalconfrsvhosppats": "RSV"}
FIELDS = ["weekendingdate", "pctinptbedsocc", "pcticubedsocc", *PATHOGENS]


def _num(row: dict, key: str) -> float | None:
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        return None


class CdcHospitalLoad(Source):
    id = "cdc_hospital_load"
    title = "Hospital load (CDC)"
    panel = "health"
    page = PAGE
    tier = "primary"
    default_interval = 21600  # the data updates weekly
    # occupancy_pct, icu_pct: statewide inpatient or ICU bed occupancy at this percent turns the panel active.
    # rise_pct, weeks, min_patients: a pathogen's hospitalized patients up this percent over this many weeks, with at
    # least min_patients in hospital now, turns it active. max_age_days: older data is a failure.
    default_params = {"occupancy_pct": 85, "icu_pct": 85, "rise_pct": 50, "weeks": 2, "min_patients": 50, "max_age_days": 21}

    async def fetch(self) -> Result:
        state = self.ctx.location.state.upper()
        if state not in STATE_NAMES:
            raise SourceError("set location.state: the hospital data is reported by state")
        weeks = int(self.params["weeks"])
        resp = await self.ctx.client.get(API, params={"jurisdiction": state, "$order": "weekendingdate DESC", "$limit": weeks + 3,
                                                       "$select": ",".join(FIELDS)})
        resp.raise_for_status()
        rows = [r for r in resp.json() if r.get("weekendingdate")]
        if not rows:
            raise SourceError(f"no hospital data for {state}")
        latest = rows[0]
        week = datetime.fromisoformat(latest["weekendingdate"]).date()
        age = (self.ctx.now().date() - week).days
        if age > int(self.params["max_age_days"]):
            raise SourceError(f"upstream data is {age} days old")
        beds, icu = _num(latest, "pctinptbedsocc"), _num(latest, "pcticubedsocc")
        if beds is None and icu is None and all(_num(latest, k) is None for k in PATHOGENS):
            raise SourceError("the latest week has no values")

        past = rows[weeks] if len(rows) > weeks else None
        if past and (week - datetime.fromisoformat(past["weekendingdate"]).date()).days != 7 * weeks:
            past = None  # a missing week would make the comparison misleading
        pathogens = []
        for key, label in PATHOGENS.items():
            now, then = _num(latest, key), _num(past, key) if past else None
            change = round((now - then) / then * 100, 1) if now is not None and then else None
            rising = change is not None and change >= float(self.params["rise_pct"]) and now >= int(self.params["min_patients"])
            pathogens.append({"pathogen": label, "patients": None if now is None else int(now), "change_pct": change, "rising": rising})
        full = beds is not None and beds >= float(self.params["occupancy_pct"])
        icu_full = icu is not None and icu >= float(self.params["icu_pct"])

        notes = []
        if full:
            notes.append(f"inpatient beds {beds:.0f}% full")
        if icu_full:
            notes.append(f"ICU beds {icu:.0f}% full")
        notes += [f"{p['pathogen']} patients up {p['change_pct']:.0f}% in {weeks} weeks to {p['patients']}" for p in pathogens if p["rising"]]
        if notes:
            summary = f"{state}: " + "; ".join(notes)
        else:
            counts = ", ".join(f"{p['pathogen']} {p['patients']}" for p in pathogens if p["patients"] is not None)
            summary = f"{state} hospitals {beds:.0f}% full" if beds is not None else f"{state} hospitals"
            summary += (f", ICU {icu:.0f}%" if icu is not None else "") + (f"; in hospital: {counts}" if counts else "")
        active = bool(notes)
        return Result(
            data={"state": state, "week_end": week.isoformat(), "beds_pct": beds, "icu_pct": icu, "weeks": weeks,
                  "beds_active": full, "icu_active": icu_full, "pathogens": pathogens},
            summary=summary, status="active" if active else "nominal", source_url=PAGE,
        )
