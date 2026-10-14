"""NWS active alerts for the configured point (api.weather.gov, no key)."""
from __future__ import annotations

from .base import Result, Source

SEVERITY = {"Unknown": 0, "Minor": 1, "Moderate": 2, "Severe": 3, "Extreme": 4}


class NwsAlerts(Source):
    id = "nws_alerts"
    page = "https://www.weather.gov/"  # shown as the link until the first fetch succeeds
    title = "NWS alerts, home point"
    panel = "local"
    tier = "primary"
    default_interval = 300
    default_params = {"active_min_severity": "Moderate", "max_items": 20}

    async def fetch(self) -> Result:
        loc = self.ctx.location
        resp = await self.ctx.client.get(
            "https://api.weather.gov/alerts/active",
            params={"point": f"{loc.lat:.4f},{loc.lon:.4f}"},
            headers={"Accept": "application/geo+json"},
        )
        resp.raise_for_status()
        features = resp.json().get("features") or []

        alerts = []
        for feature in features:
            p = feature.get("properties") or {}
            alerts.append(
                {
                    "id": p.get("id"),
                    "event": p.get("event") or "Alert",
                    "severity": p.get("severity") or "Unknown",
                    "urgency": p.get("urgency"),
                    "headline": p.get("headline"),
                    "area": p.get("areaDesc"),
                    "effective": p.get("effective"),
                    "expires": p.get("expires"),
                }
            )
        alerts.sort(key=lambda a: (-SEVERITY.get(a["severity"], 0), a["expires"] or ""))
        alerts = alerts[: int(self.params["max_items"])]

        threshold = SEVERITY.get(self.params["active_min_severity"], 2)
        active = any(SEVERITY.get(a["severity"], 0) >= threshold for a in alerts)

        if not alerts:
            summary = "No active alerts"
        else:
            names = ", ".join(a["event"] for a in alerts[:3])
            more = "" if len(alerts) <= 3 else f" and {len(alerts) - 3} more"
            summary = f"{len(alerts)} active: {names}{more}"

        url = f"https://forecast.weather.gov/MapClick.php?lat={loc.public_lat}&lon={loc.public_lon}"
        return Result(
            data={"alerts": alerts},
            summary=summary,
            status="active" if active else "nominal",
            source_url=url,
        )
