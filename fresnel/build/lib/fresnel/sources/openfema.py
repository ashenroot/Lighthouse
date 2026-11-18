"""FEMA disaster and emergency declarations for the home county (OpenFEMA, no key)."""
from __future__ import annotations

from datetime import timedelta

from .base import Result, Source

API = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
TYPES = {"DR": "Major disaster", "EM": "Emergency", "FM": "Fire management"}


class OpenFema(Source):
    id = "openfema"
    page = "https://www.fema.gov/disaster/declarations"  # shown as the link until the first fetch succeeds
    title = "FEMA declarations, home county"
    panel = "local"
    tier = "primary"
    default_interval = 1800
    # active_days: a declaration this recent turns the panel active.
    default_params = {"lookback_days": 365, "active_days": 30, "max_items": 10}

    async def fetch(self) -> Result:
        loc = self.ctx.location
        today = self.ctx.now().date()
        since = (today - timedelta(days=int(self.params["lookback_days"]))).isoformat()
        flt = (
            f"fipsStateCode eq '{loc.state_fips}'"
            f" and (fipsCountyCode eq '{loc.county_code}' or fipsCountyCode eq '000')"
            f" and declarationDate ge '{since}T00:00:00.000z'"
        )
        resp = await self.ctx.client.get(
            API,
            params={"$filter": flt, "$orderby": "declarationDate desc", "$top": 100},
        )
        resp.raise_for_status()
        rows = resp.json()["DisasterDeclarationsSummaries"]

        seen: set[int] = set()
        declarations = []
        for r in rows:
            number = r["disasterNumber"]
            if number in seen:
                continue
            seen.add(number)
            dtype = r.get("declarationType")
            declarations.append(
                {
                    "number": number,
                    "type": dtype,
                    "type_label": TYPES.get(dtype, dtype),
                    "title": r.get("declarationTitle"),
                    "incident_type": r.get("incidentType"),
                    "declared": (r.get("declarationDate") or "")[:10],
                    "incident_begin": (r.get("incidentBeginDate") or "")[:10] or None,
                    "incident_end": (r.get("incidentEndDate") or "")[:10] or None,
                    "area": r.get("designatedArea"),
                    "programs": {
                        "individual_assistance": bool(r.get("iaProgramDeclared")),
                        "public_assistance": bool(r.get("paProgramDeclared")),
                        "hazard_mitigation": bool(r.get("hmProgramDeclared")),
                    },
                    "url": f"https://www.fema.gov/disaster/{number}",
                }
            )
        declarations = declarations[: int(self.params["max_items"])]

        active_start = (today - timedelta(days=int(self.params["active_days"]))).isoformat()
        current = [d for d in declarations if d["declared"] >= active_start]

        if current:
            top = current[0]
            summary = f"{top['type_label']} declared {top['declared']}: {top['title']}"
            if len(current) > 1:
                summary += f" (+{len(current) - 1} more)"
        elif declarations:
            summary = f"No recent declarations; latest {declarations[0]['declared']}"
        else:
            summary = "No declarations in the lookback period"

        return Result(
            data={"declarations": declarations},
            summary=summary,
            status="active" if current else "nominal",
            source_url=declarations[0]["url"] if declarations else "https://www.fema.gov/disaster/declarations",
        )
