"""NOAA space weather scales: radio blackouts (R), solar radiation storms (S), geomagnetic storms (G)."""
from __future__ import annotations

from .base import Result, Source

URL = "https://services.swpc.noaa.gov/products/noaa-scales.json"


def _level(block: dict | None) -> int:
    try:
        return int((block or {}).get("Scale"))
    except (TypeError, ValueError):
        return 0


def _prob(block: dict | None, key: str):
    try:
        return int((block or {}).get(key))
    except (TypeError, ValueError):
        return None


class SwpcScales(Source):
    id = "swpc_scales"
    page = "https://www.swpc.noaa.gov/noaa-scales-explanation"  # shown as the link until the first fetch succeeds
    title = "Space weather scales (SWPC)"
    panel = "space"
    tier = "primary"
    default_interval = 300
    # R1 flares are routine, so radio blackouts default to R2 and up.
    default_params = {"active_r": 2, "active_s": 1, "active_g": 1}

    async def fetch(self) -> Result:
        resp = await self.ctx.client.get(URL)
        resp.raise_for_status()
        raw = resp.json()
        now = raw["0"]
        current = {"R": _level(now.get("R")), "S": _level(now.get("S")), "G": _level(now.get("G"))}
        forecast = []
        for day in ("1", "2", "3"):
            d = raw.get(day) or {}
            forecast.append(
                {
                    "date": d.get("DateStamp"),
                    "r_minor_prob": _prob(d.get("R"), "MinorProb"),
                    "r_major_prob": _prob(d.get("R"), "MajorProb"),
                    "s_prob": _prob(d.get("S"), "Prob"),
                    "g_scale": _level(d.get("G")),
                }
            )
        active = (
            current["R"] >= int(self.params["active_r"])
            or current["S"] >= int(self.params["active_s"])
            or current["G"] >= int(self.params["active_g"])
        )
        summary = f"R{current['R']} S{current['S']} G{current['G']}"
        return Result(
            data={"current": current, "forecast": forecast},
            summary=summary,
            status="active" if active else "nominal",
            source_url="https://www.swpc.noaa.gov/noaa-scales-explanation",
        )
