"""Shared EIA Open Data (api.eia.gov v2) request. Both EIA sources use the same free key (`fresnel key set eia`)."""
from __future__ import annotations

from .base import Source

BASE = "https://api.eia.gov/v2"


async def eia_data(src: Source, route: str, facets: dict[str, list[str]], *, frequency: str, start: str | None = None,
                   length: int = 1000, offset: int = 0) -> tuple[list[dict], int]:
    """One page of rows, newest first, and the total number of rows that match. The API returns at most 5000 a page."""
    params: list[tuple[str, str]] = [
        ("api_key", src.api_key()), ("frequency", frequency), ("data[0]", "value"),
        ("sort[0][column]", "period"), ("sort[0][direction]", "desc"), ("length", str(length)), ("offset", str(offset)),
    ]
    if start:
        params.append(("start", start))
    for name, values in facets.items():
        params.extend((f"facets[{name}][]", v) for v in values)
    resp = await src.ctx.client.get(f"{BASE}/{route}/data/", params=params)
    src.check_key(resp)
    resp.raise_for_status()
    body = resp.json()["response"]
    rows = body["data"]
    if not isinstance(rows, list):
        raise ValueError("data is not a list")
    return rows, int(body["total"])
