#!/usr/bin/env python3
"""Geocode incident addresses against a local OSM street reference.

No address leaves the machine: the street reference was pulled once from Overpass (see
etl/pull/osm_streets.py) and every match here is a string comparison against that file.

What gets written:

  dim_geo_street          one row per matched street - centroid, spread, segment count
  fact_incident.geo_lat   populated for matched incidents
  fact_incident.geo_lon
  fact_incident.geo_source  reported_point | street_centroid | street_centroid_broad | NULL
  data/clean/geo_unmatched.csv   streets that could not be matched, with volumes

Precision is stated, never implied: `reported_point` is a coordinate the crew actually
recorded (248 incidents), `street_centroid` is the middle of the matched street, and
`street_centroid_broad` is used where a street is longer than 2 km - the centroid of a
valley-long highway is a fair marker for "somewhere on US-75" and nothing more.
"""
from __future__ import annotations

import csv
import math
import os
import re
import sqlite3
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.abspath(os.environ.get("SVFD_DB") or os.path.join(ROOT, "warehouse.db"))
# The OSM reference is public map data, not department material, so it is shared by the
# real and synthetic builds. SVFD_OSM points at the directory holding osm_streets.csv and
# osm_places.csv (default data/raw), while the incident address list comes from SVFD_RAW.
OSM_DIR = os.path.abspath(os.environ.get("SVFD_OSM") or os.path.join(ROOT, "data", "raw"))
RAW = os.path.abspath(os.environ.get("SVFD_RAW") or os.path.join(ROOT, "data", "raw"))
CLEAN = os.path.abspath(os.environ.get("SVFD_CLEAN")
                        or os.path.join(os.path.dirname(RAW), "clean"))
OSM = os.path.join(OSM_DIR, "osm_streets.csv")
# the NERIS-era report never carried an address, but Elite's own NERIS incident list
# does (column `NerisFullAddress`). It is scraped once into this file so those rows can
# be geocoded like every other one.
ADDR = os.path.join(RAW, "elite_neris_addresses.csv")
PLACES = os.path.join(OSM_DIR, "osm_places.csv")
REVIEW = os.path.join(CLEAN, "geo_unmatched.csv")

BROAD_KM = 2.0      # a street wider than this has no meaningful single centroid

SUFFIXES = {
    "LN", "LANE", "RD", "ROAD", "ST", "STREET", "DR", "DRIVE", "AVE", "AVENUE", "BLVD",
    "BOULEVARD", "CT", "COURT", "CIR", "CIRCLE", "WAY", "TRL", "TRAIL", "PL", "PLACE",
    "LOOP", "HWY", "HIGHWAY", "PKWY", "PARKWAY", "TER", "TERRACE", "ALY", "ALLEY",
    "SQ", "PATH", "PT", "POINT", "RUN", "PASS", "RIDGE", "VIEW", "CV", "COVE",
    "HOLLOW", "HOLW", "SPUR", "ROW",
}
DIRECTIONS = {"N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST"}
ROUTE_WORDS = {"HWY", "HIGHWAY", "US", "SR", "ID", "IDAHO", "ROUTE", "RT", "STATE"}
# place-style suffixes: "New Villager Condo" and "Horseman Center" are buildings the
# address field names instead of a street, and OSM carries them without the suffix
PLACE_SUFFIXES = {
    "CONDO", "CONDOS", "CONDOMINIUM", "CONDOMINIUMS", "TOWNHOME", "TOWNHOMES",
    "APARTMENT", "APARTMENTS", "SUITE", "SUITES", "CENTER", "CENTRE", "BUILDING",
    "COMPLEX", "LODGE", "INN", "RESORT", "VILLAGE", "HOTEL", "MOTEL",
}


def norm(name: str) -> str:
    """Uppercase, punctuation to spaces, collapse whitespace."""
    s = (name or "").upper().replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def strip_suffix(tokens: list[str]) -> list[str]:
    while tokens and tokens[-1] in SUFFIXES:
        tokens = tokens[:-1]
    return tokens


def strip_direction(tokens: list[str]) -> list[str]:
    while tokens and tokens[0] in DIRECTIONS:
        tokens = tokens[1:]
    while tokens and tokens[-1] in DIRECTIONS:
        tokens = tokens[:-1]
    return tokens


def keys(name: str) -> set[str]:
    """Every equivalence class a street name could belong to."""
    n = norm(name)
    if not n:
        return set()
    toks = n.split()
    # some rows carry the house number in the street field ("113 ANGANI WAY")
    while toks and toks[0].isdigit():
        toks = toks[1:]
    if not toks:
        return set()
    out = {" ".join(toks)}
    place = list(toks)
    while place and place[-1] in PLACE_SUFFIXES:
        place = place[:-1]
    for variant in (strip_suffix(toks), strip_direction(toks),
                    strip_direction(strip_suffix(toks)),
                    strip_suffix(strip_direction(toks)), place):
        if variant:
            out.add(" ".join(variant))
    # highways: "US 75", "ID-75", "Highway 75" all collapse to one key
    if any(w in ROUTE_WORDS for w in toks):
        digits = [w for w in toks if w.isdigit()]
        if digits:
            out.add(f"ROUTE{digits[0]}")
    return out


def ordered_keys(name: str) -> list[str]:
    """Candidate keys in match-priority order: full name, then variants, then route."""
    n = norm(name)
    toks = n.split()
    # drop a leading house number, but never the whole name ("75" is a street here)
    if len(toks) > 1 and toks[0].isdigit():
        toks = toks[1:]
    out: list[str] = []
    if toks:
        place = list(toks)
        while place and place[-1] in PLACE_SUFFIXES:
            place = place[:-1]
        for variant in (toks, strip_suffix(toks), strip_direction(toks),
                        strip_direction(strip_suffix(toks)),
                        strip_suffix(strip_direction(toks)), place,
                        strip_direction(place)):
            if variant and " ".join(variant) not in out:
                out.append(" ".join(variant))
    if any(w in ROUTE_WORDS for w in toks) or (len(toks) == 1 and toks[0].isdigit()):
        digits = [w for w in toks if w.isdigit()]
        if digits and f"ROUTE{digits[0]}" not in out:
            out.append(f"ROUTE{digits[0]}")
    return out


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def main() -> None:
    if not os.path.exists(OSM):
        raise SystemExit(f"no street reference at {OSM} - run etl/pull/osm_streets.py first")
    con = sqlite3.connect(DB)

    # ------------------------------------------------------------------ street index
    segs: dict[str, list[tuple[float, float]]] = defaultdict(list)
    display: dict[str, str] = {}
    kinds: dict[str, str] = {}
    with open(OSM, newline="") as f:
        for row in csv.DictReader(f, delimiter="|"):
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
            except (TypeError, ValueError):
                continue
            name = (row["name"] or "").strip()
            if not name:
                continue
            for k in keys(name):
                segs[k].append((lat, lon))
                display.setdefault(k, name)
                kinds.setdefault(k, "street")
            # the road's OSM name may be an honorary one ("Idaho Medal of Honor Highway")
            # while `ref` carries the route number the crews actually radio
            ref = (row.get("ref") or "").strip()
            if ref:
                for k in keys(ref):
                    segs[k].append((lat, lon))
                    display.setdefault(k, f"{name} ({ref})")
                    kinds.setdefault(k, "route")

    if os.path.exists(PLACES):
        with open(PLACES, newline="") as f:
            for row in csv.DictReader(f, delimiter="|"):
                try:
                    lat, lon = float(row["lat"]), float(row["lon"])
                except (TypeError, ValueError):
                    continue
                name = (row["name"] or "").strip()
                if not name:
                    continue
                for k in keys(name):
                    if k in segs:          # a real street name wins over a building
                        continue
                    segs[k].append((lat, lon))
                    display.setdefault(k, name)
                    kinds.setdefault(k, "place")

    con.executescript("""
        DROP TABLE IF EXISTS dim_geo_street;
        CREATE TABLE dim_geo_street (
            street_key   TEXT PRIMARY KEY,
            osm_name     TEXT,
            kind         TEXT,               -- street | route | place
            segments     INTEGER,
            lat          REAL,
            lon          REAL,
            spread_km    REAL
        );
    """)
    have = {r[1] for r in con.execute("PRAGMA table_info(fact_incident)")}
    for col, typ in (("geo_lat", "REAL"), ("geo_lon", "REAL"), ("geo_source", "TEXT")):
        if col not in have:
            con.execute(f"ALTER TABLE fact_incident ADD COLUMN {col} {typ}")
    con.execute("UPDATE fact_incident SET geo_lat=NULL, geo_lon=NULL, geo_source=NULL")

    centroids: dict[str, tuple[float, float, int, float]] = {}
    for k, pts in segs.items():
        n = len(pts)
        lat = sum(p[0] for p in pts) / n
        lon = sum(p[1] for p in pts) / n
        spread = max(haversine_km((lat, lon), p) for p in pts)
        centroids[k] = (lat, lon, n, spread)
        con.execute("INSERT OR REPLACE INTO dim_geo_street VALUES (?,?,?,?,?,?,?)",
                    (k, display.get(k, k), kinds.get(k, "street"), n,
                     round(lat, 6), round(lon, 6), round(spread, 2)))

    # ------------------------------------------------------------------ assignment
    rows = con.execute("""
        SELECT incident_uid, street_name, latitude, longitude
        FROM fact_incident
        WHERE is_actual = 1
    """).fetchall()

    stats = defaultdict(int)
    unmatched: dict[str, list[int]] = defaultdict(list)
    for uid, street, rlat, rlon in rows:
        if rlat is not None and rlon is not None:
            con.execute("UPDATE fact_incident SET geo_lat=?, geo_lon=?, geo_source='reported_point'"
                        " WHERE incident_uid=?", (rlat, rlon, uid))
            stats["reported_point"] += 1
            continue
        if not (street or "").strip():
            stats["no_address"] += 1
            continue
        best = None
        for k in ordered_keys(street):
            if k in centroids:
                lat, lon, _n, spread = centroids[k]
                best = (lat, lon, spread)
                break
        if best is None:
            stats["unmatched"] += 1
            unmatched[(street or "").strip().upper()].append(uid)
            continue
        lat, lon, spread = best
        source = "street_centroid_broad" if spread > BROAD_KM else "street_centroid"
        con.execute("UPDATE fact_incident SET geo_lat=?, geo_lon=?, geo_source=? WHERE incident_uid=?",
                    (round(lat, 6), round(lon, 6), source, uid))
        stats[source] += 1

    con.commit()

    # ------------------------------------------------------------------ review file
    os.makedirs(os.path.dirname(REVIEW), exist_ok=True)
    with open(REVIEW, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["street_raw", "incidents", "status"])
        for name, uids in sorted(unmatched.items(), key=lambda kv: -len(kv[1])):
            w.writerow([name, len(uids), "UNMATCHED"])

    total = len(rows)
    placed = stats["reported_point"] + stats["street_centroid"] + stats["street_centroid_broad"]
    print(f"  geo: {placed}/{total} incidents placed "
          f"({placed / max(total, 1) * 100:.0f}%)")
    print(f"       reported points   {stats['reported_point']:>5}")
    print(f"       street centroid   {stats['street_centroid']:>5}")
    print(f"       broad street      {stats['street_centroid_broad']:>5}")
    print(f"       unmatched street  {stats['unmatched']:>5}  ({len(unmatched)} distinct)")
    print(f"       no address        {stats['no_address']:>5}")
    print(f"       streets in index  {len(centroids):>5}  -> {REVIEW}")
    con.close()


if __name__ == "__main__":
    main()
