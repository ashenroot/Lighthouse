"""NOAA SWPC planetary K-index (services.swpc.noaa.gov, no key)."""
from __future__ import annotations

from datetime import datetime, timezone

from .base import Result, Source


def g_scale(kp: float) -> int:
    """NOAA geomagnetic storm scale: Kp 5 is G1 ... Kp 9 is G5. Kp 4.67 counts as 5."""
    return max(0, min(5, round(kp) - 4))


def _rows(payload: list) -> list[dict]:
    # SWPC has served this product both as a header row plus arrays and as objects.
    if payload and isinstance(payload[0], list):
        header = payload[0]
        return [dict(zip(header, row)) for row in payload[1:]]
    return [dict(row) for row in payload]


def _time(tag: str) -> str:
    dt = datetime.fromisoformat(tag.replace(" ", "T").replace("Z", ""))
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


class SwpcKp(Source):
    id = "swpc_kp"
    page = "https://www.swpc.noaa.gov/products/planetary-k-index"  # shown as the link until the first fetch succeeds
    title = "Planetary K-index (SWPC)"
    panel = "space"
    tier = "primary"
    default_interval = 300
    default_params = {"active_g": 1, "recent": 8}

    async def fetch(self) -> Result:
        resp = await self.ctx.client.get(
            "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
        )
        resp.raise_for_status()
        rows = _rows(resp.json())
        recent = []
        for row in rows[-int(self.params["recent"]):]:
            kp = float(row["Kp"])
            recent.append({"time": _time(row["time_tag"]), "kp": kp, "g_scale": g_scale(kp)})
        current = recent[-1]  # IndexError on an empty feed counts as a bad response
        status = "active" if current["g_scale"] >= int(self.params["active_g"]) else "nominal"
        label = f"G{current['g_scale']}" if current["g_scale"] else "below G1"
        return Result(
            data={"current": current, "recent": recent},
            summary=f"Kp {current['kp']:.2f} ({label})",
            status=status,
            source_url="https://www.swpc.noaa.gov/products/planetary-k-index",
        )
