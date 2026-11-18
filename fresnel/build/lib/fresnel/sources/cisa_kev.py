"""CISA Known Exploited Vulnerabilities catalog (cisa.gov, no key)."""
from __future__ import annotations

from datetime import timedelta

from .base import Result, Source

URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
PAGE = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"


class CisaKev(Source):
    id = "cisa_kev"
    page = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"  # shown as the link until the first fetch succeeds
    title = "CISA Known Exploited Vulnerabilities"
    panel = "cyber"
    tier = "primary"
    default_interval = 1800
    # window_days: how far back the panel lists. active_days: how recent counts as active.
    default_params = {"window_days": 7, "active_days": 1, "max_items": 25}

    async def fetch(self) -> Result:
        resp = await self.ctx.client.get(URL)
        resp.raise_for_status()
        catalog = resp.json()
        entries = catalog["vulnerabilities"]

        today = self.ctx.now().date()
        window_start = today - timedelta(days=int(self.params["window_days"]))
        active_start = today - timedelta(days=int(self.params["active_days"]))

        recent = []
        for v in entries:
            added = v.get("dateAdded")
            if not added or added < window_start.isoformat():
                continue
            recent.append(
                {
                    "cve": v.get("cveID"),
                    "vendor": v.get("vendorProject"),
                    "product": v.get("product"),
                    "name": v.get("vulnerabilityName"),
                    "date_added": added,
                    "due_date": v.get("dueDate"),
                    "ransomware_use": v.get("knownRansomwareCampaignUse"),
                    "url": f"https://nvd.nist.gov/vuln/detail/{v.get('cveID')}",
                }
            )
        recent.sort(key=lambda v: (v["date_added"], v["cve"] or ""), reverse=True)
        newest_count = sum(1 for v in recent if v["date_added"] >= active_start.isoformat())

        return Result(
            data={
                "catalog_version": catalog.get("catalogVersion"),
                "total": len(entries),
                "recent": recent[: int(self.params["max_items"])],
                "recent_count": len(recent),
                "newest_count": newest_count,
            },
            summary=f"{len(recent)} added in the last {self.params['window_days']} days"
            f" ({newest_count} since {active_start.isoformat()})",
            status="active" if newest_count else "nominal",
            source_url=PAGE,
        )
