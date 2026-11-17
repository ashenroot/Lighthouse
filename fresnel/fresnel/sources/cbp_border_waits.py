"""Commercial truck waits at US land border crossings (CBP Border Wait Times feed, no key).

CBP publishes the wait for the standard commercial lanes at about 85 crossings on the Canadian and Mexican borders,
refreshed hourly. Many crossings report "N/A" (no commercial lanes), "Update Pending" or "Lanes Closed" at any moment, and closed
commercial lanes are routine at small ports, so only a long wait or a closed port turns the panel active. The
crossings with no number are counted by reason and left out of the ranking, not treated as calm.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

from .base import Result, Source, SourceError

URL = "https://bwt.cbp.gov/xml/bwt.xml"
PAGE = "https://bwt.cbp.gov/"
EASTERN = ZoneInfo("America/New_York")  # CBP stamps the feed in Eastern time


def _int(text: str | None) -> int | None:
    text = (text or "").strip()
    return int(text) if text.isdigit() else None


class CbpBorderWaits(Source):
    id = "cbp_border_waits"
    title = "Border truck waits (CBP)"
    panel = "supply"
    page = PAGE
    tier = "primary"
    default_interval = 1800
    # delay_min: a commercial wait of at least this many minutes at any crossing turns the panel active.
    # borders: which borders to watch (Canadian Border, Mexican Border). ports: if set, only these port names.
    # max_listed: crossings listed, longest wait first. max_age_minutes: a feed older than this is a failure.
    default_params = {"delay_min": 60, "borders": ["Canadian Border", "Mexican Border"], "ports": [], "max_listed": 5,
                      "max_age_minutes": 240}

    async def fetch(self) -> Result:
        resp = await self.ctx.client.get(URL, headers={"Accept": "application/xml"})
        resp.raise_for_status()
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            raise SourceError("the border wait feed is not valid XML") from exc
        stamp = f"{root.findtext('last_updated_date') or ''} {root.findtext('last_updated_time') or ''}".strip()
        try:
            updated = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=EASTERN)
        except ValueError as exc:
            raise SourceError("the border wait feed has no update time") from exc
        age = int((self.ctx.now() - updated).total_seconds() // 60)
        if age > int(self.params["max_age_minutes"]):
            raise SourceError(f"upstream feed is {age // 60} h {age % 60} min old")

        borders = {str(b).lower() for b in self.params["borders"]}
        wanted = {str(p).lower() for p in self.params["ports"]}
        ports = root.findall("port")
        if not ports:
            raise SourceError("the border wait feed lists no crossings")
        crossings, closed, pending, lanes_closed = [], [], 0, 0
        for p in ports:
            name = (p.findtext("port_name") or "").strip()
            if (borders and (p.findtext("border") or "").strip().lower() not in borders) or (wanted and name.lower() not in wanted):
                continue
            lane = p.find("commercial_vehicle_lanes/standard_lanes")
            crossing = (p.findtext("crossing_name") or "").strip()
            label = f"{name} ({crossing})" if crossing else name
            if (p.findtext("port_status") or "").strip().lower() == "closed":
                closed.append(label)
                continue
            delay = _int(lane.findtext("delay_minutes")) if lane is not None else None
            if delay is None:
                state = (lane.findtext("operational_status") or "").strip().lower() if lane is not None else ""
                if state == "lanes closed":
                    lanes_closed += 1  # routine at small ports, so it is counted and never rated
                elif state not in ("n/a", ""):
                    pending += 1  # "Update Pending": the crossing has commercial lanes but no number yet
                continue
            crossings.append({"port": name, "crossing": crossing, "label": label, "border": (p.findtext("border") or "").replace(" Border", ""),
                              "delay_min": delay, "lanes_open": _int(lane.findtext("lanes_open"))})
        if not crossings and not closed:
            raise SourceError("no crossing reported a commercial wait")

        crossings.sort(key=lambda c: -c["delay_min"])
        floor = int(self.params["delay_min"])
        long_waits = [c for c in crossings if c["delay_min"] >= floor]
        delayed = [c for c in crossings if c["delay_min"] > 0]
        listed = crossings[: int(self.params["max_listed"])]
        active = bool(long_waits or closed)

        notes = []
        if long_waits:
            top = long_waits[0]
            notes.append(f"longest truck wait {top['delay_min']} min at {top['label']}")
            if len(long_waits) > 1:
                notes.append(f"{len(long_waits)} crossings at {floor} min or more")
        if closed:
            notes.append(f"port closed: {', '.join(closed[:3])}")
        if notes:
            summary = "; ".join(notes)
        else:
            top = crossings[0]
            summary = f"No truck wait over {floor} min" + (f"; longest {top['delay_min']} min at {top['label']}" if top["delay_min"] else "")
        return Result(
            data={"updated": updated.isoformat(), "age_min": age, "delay_min": floor, "listed": listed, "crossings": crossings, "reporting": len(crossings),
                  "pending": pending, "lanes_closed": lanes_closed, "delayed": len(delayed), "long_waits": len(long_waits), "closed": closed},
            summary=summary, status="active" if active else "nominal", source_url=PAGE,
        )
