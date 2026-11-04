#!/usr/bin/env python3
"""One-time pull: OSM reference layers for the Sun Valley service area.

Writes two immutable references so the warehouse can geocode incident addresses without
sending a single address to a public geocoding service:

  data/raw/osm_streets.csv   lat|lon|name|ref   highway centrelines (ref carries the
                                                route number, e.g. "US 75" - the road's
                                                name in OSM is "Idaho Medal of Honor
                                                Highway", so name matching alone misses it)
  data/raw/osm_places.csv    lat|lon|name|kind  named buildings and amenities - the
                                                department's address field often carries a
                                                place ("Horseman Center", "New Villager
                                                Condo") instead of a street

Overpass allows anonymous read access; identify yourself and do not hammer it.
Re-run only when the reference needs refreshing.

  python3 etl/pull/osm_streets.py
"""
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(ROOT, "data", "raw")

# Blaine County plus a margin for mutual-aid runs (Sun Valley, Ketchum, Hailey, Bellevue,
# Carey, and the highway corridor north toward Galena).
BBOX = (43.00, -115.20, 44.30, -113.40)   # south, west, north, east
# places are dense (every named building), so they are pulled only for the populated
# corridor - the far north of the county has no named buildings worth matching
PLACE_BBOX = (43.25, -114.75, 43.95, -113.85)
# The 3x3 grid over the whole corridor returns only a couple of hundred named features;
# the village core (Sun Valley Village, Ketchum, Warm Springs) is where the department's
# place-style addresses live ("New Villager Condo", "Horseman Center"), so it gets its own
# dense pass. Without it, every condo-style address stays unmatched.
PLACE_CORE_BBOX = (43.63, -114.43, 43.73, -114.29)

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

STREET_QUERY = """[out:csv(::lat,::lon,name,ref;false;"|")][timeout:300];
way["highway"]["name"]({s},{w},{n},{e});
out center;"""

PLACE_QUERY = """[out:csv(::lat,::lon,name;false;"|")][timeout:180];
(
  node["name"]["amenity"]({s},{w},{n},{e});
  way["name"]["amenity"]({s},{w},{n},{e});
  node["name"]["tourism"]({s},{w},{n},{e});
  way["name"]["tourism"]({s},{w},{n},{e});
  way["name"]["building"]({s},{w},{n},{e});
);
out center;"""

TILES = 3     # one big bbox times out; tiling keeps each query cheap


def fetch(query: str, attempts: int = 2) -> str:
    body = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for _ in range(attempts):
        for ep in ENDPOINTS:
            try:
                req = urllib.request.Request(
                    ep, data=body,
                    headers={"User-Agent": "svfd-capstone/1.0 (offline geocoding reference)"},
                )
                with urllib.request.urlopen(req, timeout=300) as r:
                    return r.read().decode("utf-8", "replace")
            except Exception as exc:
                last = exc
                print(f"  {ep} failed ({exc})", file=sys.stderr)
        time.sleep(5)
    raise SystemExit(f"all Overpass endpoints failed: {last}")


def tiles(bbox: tuple[float, float, float, float], n: int) -> list[tuple[float, float, float, float]]:
    s, w, nn, e = bbox
    dlat, dlon = (nn - s) / n, (e - w) / n
    out = []
    for i in range(n):
        for j in range(n):
            out.append((s + i * dlat, w + j * dlon, s + (i + 1) * dlat, w + (j + 1) * dlon))
    return out


def write(path: str, header: str, text: str) -> int:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        f.write(header + "\n")
        f.write("\n".join(lines) + "\n")
    return len(lines)


def collect(template: str, bbox, n: int, label: str) -> list[str]:
    lines: list[str] = []
    for idx, (s, w, nn, e) in enumerate(tiles(bbox, n), 1):
        q = template.format(s=s, w=w, n=nn, e=e)
        text = fetch(q)
        got = [ln for ln in text.splitlines() if ln.strip()]
        lines.extend(got)
        print(f"  {label} tile {idx}/{n * n}: {len(got)} rows", flush=True)
        time.sleep(2)
    return lines


def main() -> None:
    street_lines = collect(STREET_QUERY, BBOX, TILES, "street")
    with open(os.path.join(RAW, "osm_streets.csv"), "w", newline="") as f:
        f.write("lat|lon|name|ref\n")
        f.write("\n".join(dict.fromkeys(street_lines)) + "\n")
    print(f"osm streets: {len(street_lines)} segments")

    place_lines = collect(PLACE_QUERY, PLACE_BBOX, TILES, "place")
    cs, cw, cn, ce = PLACE_CORE_BBOX
    core = fetch(PLACE_QUERY.format(s=cs, w=cw, n=cn, e=ce))
    core_lines = [ln for ln in core.splitlines() if ln.strip()]
    place_lines.extend(core_lines)
    print(f"  place village core: {len(core_lines)} rows", flush=True)
    with open(os.path.join(RAW, "osm_places.csv"), "w", newline="") as f:
        f.write("lat|lon|name\n")
        f.write("\n".join(dict.fromkeys(place_lines)) + "\n")
    print(f"osm places : {len(place_lines)} named features")


if __name__ == "__main__":
    main()
