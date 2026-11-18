"""DOE emergency orders under section 202(c) of the Federal Power Act (energy.gov, no key).

DOE publishes one page per year with each order as a paragraph. This source reads that page.
It only trusts what it can read: orders are counted against the order numbers on the page,
and a page it cannot make sense of is a failure, never an empty list.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .base import Result, Source

URL = "https://www.energy.gov/ceser/{year}-doe-202c-orders"
LEAD = re.compile(r"On (\w+ \d+, \d{4}) , the U\.S\. Department of Energy \(DOE\) (\w+)")
ORDER_ID = re.compile(r"No\.\s*(202-2\d-\d+[A-Z]?)")
TO = re.compile(r"\bto\s+(?:the\s+)?([A-Z][^,.]{3,70}?)(?:\s*\(|,|\.)")
THRU = re.compile(r"(?:effective|in effect)[^.]{0,140}?through\s+([A-Z]\w+ \d+, \d{4})", re.I)
# Orders that keep a plant open are routine renewals, so they are never news. Orders that let operators
# dispatch units or run past permit limits are event driven. Anything else is "unclassified", and a NEW
# unclassified order counts as news, because an unknown order should not be assumed routine.
CHARACTER = [
    ("retirement deferral", r"remains? available|shall not (?:be )?(?:retire|deactivat)"
                            r"|defer(?:ral|ring)? of (?:the )?(?:planned )?(?:retirement|deactivation)"
                            r"|ensure (?:that )?.{0,160}? (?:is|are|remains?) available"
                            r"|to run Unit \d+ at the [A-Z][\w ]+Generating Station"),
    ("event dispatch", r"dispatch specified (?:generation )?units|order their operation as needed"
                       r"|maximum generation output|backup generation|Energy Emergency Alert|notwithstanding air quality"
                       r"|In the event that .{0,100}? determines"),
    ("capacity assurance", r"operate specified (?:generation )?units"),
]


def _text(html: str) -> str:
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    for a, b in (("&amp;", "&"), ("&nbsp;", " "), ("&#39;", "'"), ("&quot;", '"'),
                 ("&rsquo;", "'"), ("&ldquo;", '"'), ("&rdquo;", '"'), ("&mdash;", "-")):
        txt = txt.replace(a, b)
    return re.sub(r"\s+", " ", txt)


def _day(text: str | None) -> date | None:
    try:
        return datetime.strptime((text or "").strip(), "%B %d, %Y").date()
    except ValueError:
        return None


def parse(html: str) -> tuple[list[dict], int]:
    """Return (orders, number of order ids that appear on the page)."""
    txt = _text(html)
    on_page = len(set(re.findall(r"202-2\d-\d+[A-Z]?", txt)))
    orders: dict[str, dict] = {}
    for part in re.split(r"(?=On \w+ \d+, \d{4} , the U\.S\. Department of Energy)", txt):
        lead = LEAD.match(part)
        oid = ORDER_ID.search(part) if lead else None
        day = _day(lead.group(1)) if lead else None
        if not (lead and oid and day):
            continue
        body = part[:1400]
        character = "unclassified"
        for label, pattern in CHARACTER:
            if re.search(pattern, body, re.I):
                character = label
                break
        to, thru = TO.search(part), THRU.search(part)
        through = _day(thru.group(1)) if thru else None
        orders[oid.group(1)] = {
            "id": oid.group(1),
            "date": day.isoformat(),
            "recipient": to.group(1).strip() if to else None,
            "through": through.isoformat() if through else None,
            "character": character,
        }
    return sorted(orders.values(), key=lambda o: (o["date"], o["id"]), reverse=True), on_page


class Doe202c(Source):
    id = "doe_202c"
    title = "DOE emergency orders (202c)"
    panel = "grid"
    page = URL.format(year=date.today().year)
    tier = "fragile"  # reads a web page, not a data feed
    default_interval = 21600
    # active_days: how recent a new order counts as active. Plant-retention renewals never do.
    default_params = {"active_days": 14, "max_items": 5}

    async def fetch(self) -> Result:
        today = self.ctx.now().date()
        year = today.year
        resp = await self.ctx.client.get(URL.format(year=year))
        if resp.status_code == 404:  # early January, before DOE starts the new year's page
            year -= 1
            resp = await self.ctx.client.get(URL.format(year=year))
        resp.raise_for_status()
        orders, on_page = parse(resp.text)
        if not orders or len(orders) * 2 < on_page:
            raise ValueError("register page not recognised")

        start = (today - timedelta(days=int(self.params["active_days"]))).isoformat()
        in_force = [o for o in orders if o["through"] and o["through"] >= today.isoformat()]
        fresh = [o for o in orders if o["date"] >= start and o["character"] != "retirement deferral"]
        shown = (fresh + [o for o in orders if o not in fresh])[: int(self.params["max_items"])]
        newest = orders[0]
        return Result(
            data={
                "year": year,
                "orders": shown,
                "fresh": [o["id"] for o in fresh],
                "in_force": len(in_force),
                "read": len(orders),
                "on_page": on_page,
            },
            summary=f"{len(in_force)} in force; newest {newest['date']}"
            + (f"; {len(fresh)} new in {self.params['active_days']} days" if fresh else ""),
            status="active" if fresh else "nominal",
            source_url=URL.format(year=year),
        )
