"""ZIP Code to place, from a table bundled with Fresnel (zipdata/zcta.tsv.gz, built by tools/build_zcta.py).

No network and no key: the Census Bureau's ZIP Code Tabulation Areas, each with an interior point, and the county
covering most of its land. A ZIP can span counties and its point can sit miles from a given address, so anything
set explicitly in the config wins over what this returns.
"""
from __future__ import annotations

import gzip
from functools import lru_cache
from importlib import resources

STATE_BY_FIPS = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY", "60": "AS", "66": "GU", "69": "MP", "72": "PR", "78": "VI",
}


@lru_cache(maxsize=1)
def _table() -> dict[str, tuple[float, float, str, str]]:
    out: dict[str, tuple[float, float, str, str]] = {}
    with resources.files("fresnel").joinpath("zipdata/zcta.tsv.gz").open("rb") as raw, gzip.open(raw, "rt", encoding="utf-8") as f:
        for line in f:
            z, lat, lon, fips, county = line.rstrip("\n").split("\t")
            out[z] = (float(lat), float(lon), fips, county)
    return out


def normalize(value: object) -> str | None:
    """'30213', 30213, ' 30213-1234 ' and '3021' (a leading zero lost by YAML or a form) all give a 5-digit ZIP, or None."""
    text = str(value).strip().split("-")[0].strip()
    if not text.isdigit() or len(text) > 5:
        return None
    return text.zfill(5)


def lookup(value: object) -> dict | None:
    """The place for a ZIP, or None when it is not a known ZIP Code Tabulation Area (PO-box-only and unassigned ZIPs are not)."""
    z = normalize(value)
    hit = _table().get(z) if z else None
    if not hit:
        return None
    lat, lon, fips, county = hit
    state = STATE_BY_FIPS.get(fips[:2], "")
    return {"zip": z, "lat": lat, "lon": lon, "county_fips": fips, "county_name": county, "state": state,
            "name": f"{county}, {state}" if state else county}
