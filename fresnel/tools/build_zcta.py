"""Build fresnel/data/zcta.tsv.gz, the table behind `location: zip:`.

Two public-domain Census files go in:
  2024_Gaz_zcta_national.txt            ZCTA internal-point coordinates (Gazetteer)
  tab20_zcta520_county20_natl.txt       ZCTA to county overlap areas (2020 relationship file)
Each ZIP Code Tabulation Area is assigned the county that covers most of its land. A ZIP can span counties, so
the county is a best single guess; set `county_fips` in the config to override it.

Usage: python tools/build_zcta.py GAZETTEER.txt RELATIONSHIP.txt
"""
import csv
import gzip
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "fresnel" / "data" / "zcta.tsv.gz"


def main(gaz: str, rel: str) -> None:
    coords = {}
    with open(gaz, encoding="utf-8-sig") as f:
        rows = csv.reader(f, delimiter="\t")
        header = [h.strip() for h in next(rows)]
        i_zip, i_lat, i_lon = header.index("GEOID"), header.index("INTPTLAT"), header.index("INTPTLONG")
        for r in rows:
            coords[r[i_zip].strip()] = (float(r[i_lat]), float(r[i_lon]))
    best: dict[str, tuple[int, str, str]] = {}
    with open(rel, encoding="utf-8-sig") as f:
        rows = csv.reader(f, delimiter="|")
        header = next(rows)
        i_zip, i_fips, i_name, i_land = (header.index(h) for h in ("GEOID_ZCTA5_20", "GEOID_COUNTY_20", "NAMELSAD_COUNTY_20", "AREALAND_PART"))
        for r in rows:
            if not r[i_zip] or not r[i_fips]:
                continue  # rows for land outside any ZCTA or county
            area = int(r[i_land] or 0)
            if r[i_zip] not in best or area > best[r[i_zip]][0]:
                best[r[i_zip]] = (area, r[i_fips], r[i_name])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    with gzip.open(OUT, "wt", encoding="utf-8", newline="") as out:
        for z in sorted(coords):
            if z not in best:
                continue
            lat, lon = coords[z]
            out.write(f"{z}\t{lat:.4f}\t{lon:.4f}\t{best[z][1]}\t{best[z][2]}\n")
            kept += 1
    print(f"wrote {kept} ZIP codes to {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
