"""FAA airport status: ground stops, ground delay programs, delays and closures (nasstatus.faa.gov, no key)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from .base import Result, Source

URL = "https://nasstatus.faa.gov/api/airport-status-information"
PAGE = "https://nasstatus.faa.gov/"


class FaaAirports(Source):
    id = "faa_airports"
    page = "https://nasstatus.faa.gov/"  # shown as the link until the first fetch succeeds
    title = "Airport status (FAA)"
    panel = "transport"
    tier = "primary"
    default_interval = 300
    needs = ("airports",)  # the source is skipped until airports: [MCI, ...] is configured
    # Closures are listed but do not turn the panel active: most are partial NOTAM restrictions.
    default_params = {"airports": []}

    async def fetch(self) -> Result:
        resp = await self.ctx.client.get(URL, headers={"Accept": "application/xml"})
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        watched = [str(a).upper() for a in self.params["airports"]]

        events = []
        for kind in root.iter("Delay_type"):
            kind_name = (kind.findtext("Name") or "").strip()
            for el in kind.iter():
                code = (el.findtext("ARPT") or "").strip().upper()
                if code in watched and el is not kind:
                    events.append(
                        {
                            "airport": code,
                            "kind": kind_name,
                            "reason": (el.findtext("Reason") or "").strip() or None,
                            "detail": {c.tag: (c.text or "").strip() for c in el if c.tag not in ("ARPT", "Reason") and (c.text or "").strip()},
                        }
                    )
        disruptive = [e for e in events if "closure" not in e["kind"].lower()]

        per_airport = {a: [e["kind"] for e in events if e["airport"] == a] for a in watched}
        if disruptive:
            parts = sorted({f"{e['airport']} {e['kind'].replace(' Info', '').lower()}" for e in disruptive})
            summary = "; ".join(parts[:4])
        elif events:
            summary = "No delays; closure notices posted for " + ", ".join(sorted({e["airport"] for e in events}))
        else:
            summary = "No delays at " + ", ".join(watched)
        return Result(
            data={"airports": watched, "events": events, "by_airport": per_airport},
            summary=summary,
            status="active" if disruptive else "nominal",
            source_url=PAGE,
        )
