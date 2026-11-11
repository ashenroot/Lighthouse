"""Current US drug shortages from the FDA's shortage database (openFDA, no key).

The database lists one record per product presentation, so records are grouped by generic drug name. The panel
shows how many drugs are in shortage and how many of them are unavailable or limited, which drugs were newly
listed recently, and the state of any drugs on your own watch list. openFDA says its data is unvalidated and
not for medical decisions; this is situational awareness.
"""
from __future__ import annotations

from datetime import date, datetime

from .base import Result, Source, SourceError

API = "https://api.fda.gov/drug/shortages.json"
PAGE = "https://dps.fda.gov/drugshortages"
PAGE_SIZE = 1000
MAX_PAGES = 5
RANK = {"Available": 0, "Limited Availability": 1, "Unavailable": 2}


def _availability(text: str | None) -> str:
    t = (text or "").strip().lower()
    return "Unavailable" if t.startswith("un") else "Limited Availability" if t.startswith("limited") else "Available"


def _day(text: str | None) -> date | None:
    try:
        return datetime.strptime(text or "", "%m/%d/%Y").date()
    except ValueError:
        return None


class FdaDrugShortages(Source):
    id = "fda_drug_shortages"
    title = "Drug shortages (FDA)"
    panel = "health"
    page = PAGE
    tier = "primary"
    default_interval = 21600
    # watch: drug names (any part of the generic name, any case) to track, such as ["amoxicillin", "insulin"].
    # new_days, new_active: this many drugs newly listed within this many days turns the panel active.
    # max_age_days: a database last updated longer ago than this is a failure.
    default_params = {"watch": [], "new_days": 30, "new_active": 2, "max_age_days": 14, "max_listed": 6}

    async def fetch(self) -> Result:
        records, skip, updated = [], 0, None
        for _ in range(MAX_PAGES):
            resp = await self.ctx.client.get(API, params={"search": 'status:"Current"', "limit": PAGE_SIZE, "skip": skip})
            resp.raise_for_status()
            body = resp.json()
            records += body.get("results") or []
            updated = updated or (body.get("meta") or {}).get("last_updated")
            skip += PAGE_SIZE
            if skip >= int((body.get("meta") or {}).get("results", {}).get("total", 0)) or not body.get("results"):
                break
        if not records:
            raise SourceError("no shortage records returned")
        today = self.ctx.now().date()
        if updated:
            age = (today - date.fromisoformat(updated)).days
            if age > int(self.params["max_age_days"]):
                raise SourceError(f"upstream data is {age} days old")

        drugs: dict[str, dict] = {}
        for r in records:
            name = (r.get("generic_name") or "").strip()
            if not name:
                continue
            d = drugs.setdefault(name, {"name": name, "availability": "Available", "presentations": 0, "posted": None,
                                        "categories": set(), "reason": None})
            avail = _availability(r.get("availability"))
            if RANK[avail] > RANK[d["availability"]]:
                d["availability"] = avail
            d["presentations"] += 1
            posted = _day(r.get("initial_posting_date"))
            if posted and (d["posted"] is None or posted > d["posted"]):
                d["posted"] = posted
            d["categories"].update(r.get("therapeutic_category") or [])
            d["reason"] = d["reason"] or r.get("shortage_reason")
        if not drugs:
            raise SourceError("shortage records had no drug names")

        def view(d: dict) -> dict:
            return {"name": d["name"], "availability": d["availability"], "presentations": d["presentations"],
                    "posted": d["posted"].isoformat() if d["posted"] else None, "reason": d["reason"], "categories": sorted(d["categories"])}

        new_days, limit = int(self.params["new_days"]), int(self.params["max_listed"])
        new = sorted((d for d in drugs.values() if d["posted"] and 0 <= (today - d["posted"]).days <= new_days),
                     key=lambda d: d["posted"], reverse=True)
        watch = []
        for term in [str(w).strip() for w in self.params["watch"] or [] if str(w).strip()]:
            hits = sorted((d for d in drugs.values() if term.lower() in d["name"].lower()), key=lambda d: -RANK[d["availability"]])
            watch.append({"term": term, "listed": bool(hits), "matches": [view(d) for d in hits[:limit]],
                          "active": any(d["availability"] != "Available" for d in hits)})
        counts = {a: sum(1 for d in drugs.values() if d["availability"] == a) for a in RANK}
        new_active = len(new) >= int(self.params["new_active"])
        hit = [w["term"] for w in watch if w["active"]]

        parts = [f"{len(drugs)} drugs on the shortage list ({counts['Unavailable']} with an unavailable product, {counts['Limited Availability']} limited)"]
        parts.append(f"{len(new)} newly listed in {new_days} days")
        if watch:
            parts.append("watching " + ", ".join(f"{w['term']} {'short' if w['active'] else 'listed' if w['listed'] else 'not listed'}" for w in watch))
        if hit:
            summary = "Watched drug short: " + ", ".join(hit) + "; " + parts[0]
        else:
            summary = "; ".join(parts)
        return Result(
            data={"total": len(drugs), "counts": counts, "new_days": new_days, "new": [view(d) for d in new[:limit]], "new_total": len(new),
                  "new_active": new_active, "watch": watch, "updated": updated},
            summary=summary, status="active" if (hit or new_active) else "nominal", source_url=PAGE,
        )
