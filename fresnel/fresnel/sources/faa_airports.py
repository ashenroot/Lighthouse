"""FAA airport status: ground stops, ground delay programs, delays and closures (nasstatus.faa.gov, no key).

Two views of one feed: the airports you list, and a nationwide picture (how many airports have ground stops or long
delays, and where the cause is equipment) that is reported whether or not your own airports are affected.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .base import Result, Source

URL = "https://nasstatus.faa.gov/api/airport-status-information"
PAGE = "https://nasstatus.faa.gov/"
EQUIPMENT_WORDS = ("equip", "outage", "communic", "radar", "frequency")


def minutes(text: str | None) -> int | None:
    """'2 hours and 31 minutes' -> 151."""
    h, m = re.search(r"(\d+)\s*hour", text or ""), re.search(r"(\d+)\s*minute", text or "")
    if not (h or m):
        return None
    return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)


def is_equipment(reason: str | None) -> bool:
    """FAA delay reasons like 'EQ:FAA' and 'TM Initiatives:STOP:EQ' mark equipment problems."""
    low = (reason or "").lower()
    return "eq" in set(re.split(r"[^a-z0-9]+", low)) or any(w in low for w in EQUIPMENT_WORDS)


class FaaAirports(Source):
    id = "faa_airports"
    page = "https://nasstatus.faa.gov/"  # shown as the link until the first fetch succeeds
    title = "Airport status (FAA)"
    panel = "transport"
    tier = "primary"
    default_interval = 300
    # airports: the airports you want watched (closures are listed but do not turn the panel active: most are partial
    # NOTAM restrictions). The nationwide view works without any. equip_active: this many airports with equipment-caused
    # delays or stops turns the panel active. stops_active: so does this many ground stops. long_delay_min: what counts
    # as a long delay in the nationwide counts.
    default_params = {"airports": [], "equip_active": 2, "stops_active": 5, "long_delay_min": 60, "max_listed": 6}

    async def fetch(self) -> Result:
        resp = await self.ctx.client.get(URL, headers={"Accept": "application/xml"})
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        watched = [str(a).upper() for a in self.params["airports"]]

        events, everything = [], []
        for kind in root.iter("Delay_type"):
            kind_name = (kind.findtext("Name") or "").strip()
            for el in kind.iter():
                code = (el.findtext("ARPT") or "").strip().upper()
                if not code or el is kind:
                    continue
                reason = (el.findtext("Reason") or "").strip() or None
                detail = {c.tag: (c.text or "").strip() for c in el if c.tag not in ("ARPT", "Reason") and (c.text or "").strip()}
                everything.append({"airport": code, "kind": kind_name, "reason": reason, "el": el})
                if code in watched:
                    events.append({"airport": code, "kind": kind_name, "reason": reason, "detail": detail})
        disruptive = [e for e in events if "closure" not in e["kind"].lower()]
        national = self.national(everything)

        per_airport = {a: [e["kind"] for e in events if e["airport"] == a] for a in watched}
        parts = sorted({f"{e['airport']} {e['kind'].replace(' Info', '').lower()}" for e in disruptive})[:4]
        if national["active"]:
            parts.append("Nationwide: " + "; ".join(national["reasons"]))
        if parts:
            summary = "; ".join(parts)
        else:
            c = national["counts"]
            tail = f"; nationwide {c['stops']} ground stop{'s' if c['stops'] != 1 else ''}, {c['long_delays']} long delay{'s' if c['long_delays'] != 1 else ''}"
            if watched:
                summary = ("No delays; closure notices posted for " + ", ".join(sorted({e["airport"] for e in events})) if events else "No delays at " + ", ".join(watched)) + tail
            else:
                summary = f"Nationwide: {c['stops']} ground stop{'s' if c['stops'] != 1 else ''}, {c['long_delays']} long delay{'s' if c['long_delays'] != 1 else ''}"
        return Result(
            data={"airports": watched, "events": events, "by_airport": per_airport, "national": national},
            summary=summary,
            status="active" if (disruptive or national["active"]) else "nominal",
            source_url=PAGE,
        )

    def national(self, everything: list[dict]) -> dict:
        """Counts across every airport in the feed, not just the watched ones."""
        long_min, limit = int(self.params["long_delay_min"]), int(self.params["max_listed"])
        stops, gdps, delays, equipment = [], 0, [], {}
        for e in everything:
            name = e["kind"].lower()
            if "closure" in name:
                continue
            mins, trend = None, None
            if "ground stop" in name:
                stops.append({"airport": e["airport"], "reason": e["reason"]})
            elif "ground delay" in name:
                gdps += 1
                mins = minutes(e["el"].findtext("Avg"))
            else:
                for ad in e["el"].iter("Arrival_Departure"):
                    m = minutes(ad.findtext("Max")) or minutes(ad.findtext("Min"))
                    if m is not None and (mins is None or m > mins):
                        mins, trend = m, (ad.findtext("Trend") or "").strip() or None
            if mins is not None and mins >= long_min:
                delays.append({"airport": e["airport"], "kind": e["kind"], "minutes": mins, "trend": trend, "reason": e["reason"]})
            if is_equipment(e["reason"]):
                equipment.setdefault(e["airport"], {"airport": e["airport"], "kind": e["kind"], "reason": e["reason"]})
        delays.sort(key=lambda d: -d["minutes"])
        reasons = []
        if len(equipment) >= int(self.params["equip_active"]):
            reasons.append("equipment problems at " + ", ".join(sorted(equipment)[:6]))
        if len(stops) >= int(self.params["stops_active"]):
            reasons.append(f"{len(stops)} ground stops")
        return {
            "active": bool(reasons), "reasons": reasons, "stops": stops[:12], "equipment": sorted(equipment.values(), key=lambda x: x["airport"]),
            "long_delays": delays[:limit],
            "counts": {"stops": len(stops), "ground_delay_programs": gdps, "long_delays": len(delays), "equipment_airports": len(equipment)},
            "thresholds": {"equip_active": int(self.params["equip_active"]), "stops_active": int(self.params["stops_active"]), "long_delay_min": long_min},
        }
