#!/usr/bin/env python3
"""Generate a complete, deterministic synthetic dataset for the SVFD warehouse.

The point is to be able to publish this repo with **zero real department records**: no
real person names, no real incident numbers, no addresses taken from call records, no
real coordinates. Everything here is invented by a seeded RNG, so two runs produce
byte-identical files and every published number can be regenerated and audited.

What is NOT invented, and why:

  * the OSM street/place reference (data/raw/osm_streets.csv, osm_places.csv) stays
    shared with the real build. Those files are public map data, already in the repo,
    and a synthetic address has to resolve against the same reference the geocoder uses
    or the geo step proves nothing. Street *names* therefore come from OSM; the house
    numbers, the calls, the units and the people do not.
  * station names (Elkhorn / Greenhorn / Griffin Butte) and municipal names are public
    geography, and the loaders' identity maps key on them. Renaming them would test a
    different pipeline than the real one.

A small share of addresses deliberately uses invented street names ("Marmot Hollow
Road") that do not exist in OSM, to reproduce the real unmatched tail the geocoder
reports in data/clean/geo_unmatched.csv.

Shape of the data (all of it tunable in the KNOBS block below):

  * NFIRS era 2020-2025 in elite_incident_fact.csv, 429 -> 675 incidents a year.
  * NERIS era 2026 Jan-Oct in elite_neris_incident_fact.csv, with the real pull's
    reporting-lag month shape and its constant 'Fire NERIS' type column.
  * alarm -> arrival p50 ~7 min, p90 ~13 min; ~90% of incidents in the performance
    basket (no mutual aid, not wildland, arrival <= 25 min).
  * two staffed stations: Elkhorn daily, Greenhorn ~55% of days in 2025 / ~78% in 2026,
    paramedic-qualified on ~4 of 5 staffed station-days.
  * staffing calendar 2025-01-01 .. 2026-12-31 (the tail is published schedule, so it
    loads with is_actual = 0 exactly like the real pull).

Usage:

    python3 etl/synthetic.py                       # writes data/synthetic/
    python3 etl/synthetic.py --out /tmp/syn        # somewhere else
    SVFD_RAW=data/synthetic/raw SVFD_CURATED=data/synthetic/curated \
      SVFD_DB=data/synthetic/warehouse.db python3 etl/build.py
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import zlib
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "data", "synthetic")
SEED = 20260101

# --------------------------------------------------------------------------- knobs
YEAR_COUNTS = {2020: 429, 2021: 470, 2022: 512, 2023: 553, 2024: 596, 2025: 675}
# 2026 stops at the analysis cut-off. The month shape copies the real pull: reports are
# written after the fact, so Jan-Apr look like a collapse and May onward catch up.
NERIS_MONTH_COUNTS = {1: 20, 2: 14, 3: 12, 4: 11, 5: 58, 6: 79, 7: 104, 8: 102, 9: 72, 10: 95}
NERIS_YEAR = 2026
ASOF = date(2026, 10, 31)

STAFF_START = date(2025, 1, 1)
STAFF_END = date(2026, 12, 31)
GREENHORN_STAFF_P = {2025: 0.55, 2026: 0.78}   # share of days the second station is staffed

MUTUAL_AID_P = 0.045      # rows flagged "22. Mutual Aid Responses Given"
SLOW_ARRIVAL_P = 0.032    # dispatch -> arrival over 25 min (remote calls, mutual aid runs)
NO_ADDRESS_P = 0.030      # incidents with no usable address at all
FICTIONAL_STREET_P = 0.070  # invented street names -> the geo_unmatched tail
REPORTED_POINT_P = 0.040  # incidents that carry a coordinate, like the real 4.5%

NFIRS_UNIT_SHARE = 0.99   # share of NFIRS incidents that have unit rows
NERIS_UNIT_SHARE = 0.05   # mirrors the real pull: apparatus rows effectively stop at the
                          # NFIRS era, so unit-hours and time on scene are published only
                          # through 2025 and the synthetic copy says the same
NERIS_TYPED_CODE_SHARE = 0.024  # real pull: 18 of 737 rows carry a dispatch code. The
                                # NERIS-era type is recovered from Elite's incident list
                                # instead, exactly as the real pipeline does it, so the
                                # synthetic export reproduces the same gap.

# response time = lognormal with this median and sigma -> p50 7.0, p90 13.0 before modifiers
RT_MEDIAN, RT_SIGMA = 6.4, 0.483
SCENE_MEDIAN, SCENE_SIGMA = 16.0, 0.62

# --------------------------------------------------------------------------- headers
# Field names are copied verbatim from the real pulls: the loaders resolve columns by
# fuzzy name matching (etl/load_elite.col) and by exact NERIS field ids, so the synthetic
# export has to speak the same dialect to test the same code path.
INCIDENT_HEADER = [
    "Basic Incident Alarm Date Time (FD1.26)",
    "Basic Incident Alarm To Arrival In Minutes",
    "Basic Incident Alarm To Controlled In Minutes",
    "Basic Incident Alarm To First Unit Arrived In Minutes",
    "Basic Incident Alarm To In Quarters In Minutes",
    "Basic Incident Alarm To In Service In Minutes",
    "Basic Incident Alarm To Last Unit Arrived In Minutes",
    "Basic Incident Alarm To Last Unit Cleared In Minutes",
    "Basic Incident Arrival Date Time (FD1.27)",
    "Basic Incident City Name (FD1.16)",
    "Basic Incident Controlled Date Time (FD1.28)",
    "Basic Incident Date Original (FD1.3)",
    "Basic Incident District (FD1.68)",
    "Basic Incident First Unit Arrived Dispatch To Arrival In HHMMSS",
    "Basic Incident First Unit Arrived Dispatch To En Route In HHMMSS",
    "Basic Incident First Unit Arrived En Route To Arrival In HHMMSS",
    "Basic Incident In Service Date Time (FD1.29.1)",
    "Basic Incident Latitude (FD1.69)",
    "Basic Incident Longitude (FD1.69)",
    "Basic Incident Nfpa Mutual Aid Given",
    "Basic Incident Number (FD1)",
    "Basic Incident PSAP To Alarm In Minutes",
    "Basic Incident Street Name (FD1.12)",
    "Basic Incident Street Number (FD1.10)",
    "Basic Incident Type (FD1.21)",
    "Basic Incident Type Code (FD1.21)",
    "Basic Incident Zone/District Number (FD1.32)",
    "Basic Primary Station Name (FD1.4)",
    "Basic Secondary Station Names List (FD1.4.1)",
]

APPARATUS_HEADER = [
    "Apparatus Resource Arrival Date Time (FD18.4)",
    "Apparatus Resource Arrived To Cleared In Minutes",
    "Apparatus Resource Arrived To In Service In Minutes",
    "Apparatus Resource Clear Date Time (FD18.5)",
    "Apparatus Resource Cleared To In Quarters In Minutes",
    "Apparatus Resource Cleared To In Service In Minutes",
    "Apparatus Resource Dispatch Date Time (FD18.3)",
    "Apparatus Resource Dispatch To Arrival In Minutes",
    "Apparatus Resource ID (FD18.1)",
    "Apparatus Resource In Service Date Time (FD18.5.1)",
    "Apparatus Resource Type (FD18.2)",
    "Basic Incident Alarm Date Time (FD1.26)",
    "Basic Incident District (FD1.68)",
    "Basic Incident Number (FD1)",
    "Basic Incident Type (FD1.21)",
    "Basic Incident Type Code (FD1.21)",
    "Basic Primary Station Name (FD1.4)",
]

NERIS_HEADER = [
    "Dispatch Alarm Date Time (itfiDispatch.001)",
    "Dispatch Arrival Date Time (itfiDispatch.002)",
    "Dispatch Arrival Time to In Service Time in Seconds",
    "Dispatch City (fiDispatch.11)",
    "Dispatch Incident Clear Date Time (fiDispatch.01)",
    "Dispatch Incident Code (fiDispatch.05)",
    "Dispatch Incident Number (fiDispatch.03)",
    "Incident Aid Given Count",
    "Incident Aid Received Count",
    "Incident Location District (itfiIncident.014)",
    "Incident Location Zone Number (itfiIncident.008)",
    "Incident Primary Station (itfiIncident.013)",
    "Incident Type",
    "Incident Alarm Date Time (itfiIncident.002)",
    "Incident Alarm Time to Arrival Time in Seconds",
    "Incident Arrival Date Time (itfiIncident.003)",
    "Incident Date Time (itfiIncident.004)",
    "Incident In Service Date Time (itfiIncident.005)",
]

# --------------------------------------------------------------------------- people
# Invented names. The roster is explicit rather than random so that crew size, rotation
# and paramedic coverage are all reproducible and easy to reason about.
# (first, last, rank, qualifier, medic_since, medic_basis)
ELK_PLATOONS = {
    "A": [
        ("Desmond", "Marchetti", "Captain", "Company Officer", "2019-06-01", "certification"),
        ("Dana", "Kestrel", "Firefighter", "Engineer", None, None),
        ("Tobias", "Rennick", "Firefighter", "Firefighter", None, None),
        ("Priya", "Sandoval", "Firefighter", "Firefighter", None, None),
    ],
    "B": [
        ("Maejor", "Falk", "Captain", "Company Officer", None, None),
        ("Colin", "Amberly", "Firefighter", "Engineer", "2021-03-15", "certification"),
        ("Hattie", "Vance", "Firefighter", "Firefighter", None, None),
        ("Ruben", "Otanez", "Firefighter", "Firefighter", None, None),
    ],
    "C": [
        ("Soren", "Bellamy", "Lieutenant", "Company Officer", None, None),
        ("Ida", "Wexley", "Firefighter", "Engineer", None, None),
        ("Grant", "Mullane", "Firefighter", "Firefighter", None, None),
        # became a medic mid-2026, which is what lifts Elkhorn's coverage that year
        ("Nona", "Pfaff", "Firefighter", "Firefighter", "2026-05-01", "roster_confirmed"),
    ],
}
ELK_ORDER = ("A", "B", "C")

GH_PLATOONS = {
    "D": [
        ("Corinne", "Ashby", "Captain", "Company Officer", "2022-02-01", "certification"),
        ("Marcus", "Delacroix", "Firefighter", "Engineer", None, None),
        ("Fenna", "Groat", "Firefighter", "Firefighter", None, None),
    ],
    "E": [
        ("Eli", "Thornquist", "Captain", "Company Officer", None, None),
        ("Wren", "Sable", "Firefighter", "Engineer", "2025-10-01", "certification"),
        ("Wendell", "Bryer", "Firefighter", "Firefighter", None, None),
    ],
}
GH_ORDER = ("D", "E")

# command and support staff: scheduled, never counted as station coverage
COMMAND = [
    ("Barrett", "Quill", "Chief", "Fire Chief", "Chief"),
    ("Nadia", "Farrow", "Assistant Chief", "Assistant Chief", "Assistant Chief"),
    ("Perry", "Lattimore", "Division Chief", "Division Chief", "Div Chief - Prevention"),
    ("Camille", "Bostock", "District Chief", "District Chief", "District Chief"),
    ("Yolanda", "Frith", "", "", "Administrative Assistant"),
]

# paid-on-call pool; one carries an unknown medic start date so the 'estimated' basis
# (never counted as coverage) stays exercised
POC = [
    ("Tess", "Halloway", None, None),
    ("Ivan", "Prosser", None, None),
    ("Delia", "Vankirk", None, None),
    ("Milo", "Fenno", None, None),
    ("Sasha", "Renard", None, None),
    ("Bram", "Okafor", None, None),
    ("Otto", "Blasingame", None, None),
    ("Kira", "Wintle", "1900-01-01", "estimated"),
]

CERT_NAMES = [
    "Idaho EMT",
    "National Registry of Emergency Medical Technicians- Basic",
    "Basic Life Support Provider",
    "Incident Command System 100",
    "Incident Command System 200",
    "Incident Command System 700",
    "Firefighter I",
    "Firefighter II",
    "Hazardous Materials Operations",
    "Rope Rescue Technician",
    "Introduction to Wildland Fire Behavior",
    "AIARE Level 1",
    "Snowmobile Operator Level 2",
    "Swiftwater Rescue Technician",
]

# --------------------------------------------------------------------------- call types
# (type code, description, weight). Weights are tuned so the mix reads Fire / EMS /
# service / MVA / wildland like a mountain-valley department, with EMS the bulk.
CALL_TYPES = [
    ("320", "Emergency medical service, other", 1200),
    ("321", "EMS call, excluding vehicle accident with injury", 600),
    ("311", "Medical assist, assist EMS crew", 350),
    ("300", "Rescue, EMS incident, other", 200),
    ("111", "Building fire", 130),
    ("113", "Cooking fire, confined to container", 60),
    ("131", "Passenger vehicle fire", 30),
    ("651", "Smoke scare, odor of smoke, other", 120),
    ("652", "Smoke detector activation, no fire - unintentional", 80),
    ("733", "Smoke detector activation due to malfunction", 90),
    ("736", "CO detector activation due to malfunction", 60),
    ("400", "Hazardous condition, other", 60),
    ("410", "Combustible and flammable gas or liquid spill or leak", 45),
    ("700", "False alarm or false call, other", 300),
    ("900", "Special type of incident, other", 120),
    ("600", "Good intent call, other", 90),
    ("611", "Dispatched and cancelled en route", 380),
    ("622", "No incident found on arrival at dispatch address", 60),
    ("510", "Person in distress, other", 70),
    ("511", "Lock-out", 50),
    ("553", "Public service assistance, other", 90),
    ("571", "Cover assignment, standby, moveup", 50),
    ("800", "Natural disaster, other", 10),
    ("322", "Motor vehicle accident with injuries", 110),
    ("324", "Motor vehicle accident with no injuries", 90),
    ("350", "Extrication of victim(s) from vehicle", 30),
    ("463", "Vehicle accident, general cleanup", 40),
    ("141", "Forest, woods or wildland fire", 40),
    ("142", "Brush or brush-and-grass mixture fire", 90),
    ("143", "Grass fire", 30),
    ("340", "Search, other", 30),
    ("363", "Water and ice related rescue", 15),
]

# NERIS type codes are not NFIRS codes; the real pull barely uses the field at all.
NERIS_NOISE_CODES = ["52B01H", "08D05U", "32B02H", "61C04H", "45B01H"]

STATION_SPELLINGS = {
    "ELK": [("Elkhorn Station", 62), ("Elkhorn Station - HQ", 30), ("Elkhorn", 8)],
    "GH": [("Greenhorn", 72), ("Station 2", 28)],      # Station 2 is the obsolete naming
    "GB": [("Griffin Butte", 72), ("Station 3", 28)],  # Station 3 likewise
}

STATION_WEIGHTS = [("ELK", 55), ("GH", 26), ("GB", 2), ("", 17)]

# apparatus per station: (resource type, [raw spellings the crews use]). The loaders fold
# every spelling onto one code, so the same rig must appear under several of them - that
# duplicate-identity problem is half the point of the warehouse.
APPARATUS = {
    "ELK": [("Engine", ["E60", "ENG60", "Engine 60", "E 60"]),
            ("Engine", ["E61", "ENG61", "Engine 61"]),
            ("Tanker or tender", ["T61", "Tanker 61", "TANK61"]),
            ("Medic", ["M60", "Medic 60"]),
            ("Squad", ["SQ1", "SQD1"]),
            ("Chief Officer", ["C1", "Chief 1"])],
    "GH": [("Engine", ["E62", "ENG62", "Engine 62"]),
           ("Tanker or tender", ["T62", "Tanker 62"]),
           ("Medic", ["M62", "Medic 62"])],
    "GB": [("Engine", ["E64", "ENG64", "Engine 64"])],
}

CITY_CENTRES = {
    "SUN VALLEY": (43.6971, -114.3514),
    "KETCHUM": (43.6807, -114.3637),
    "HAILEY": (43.5196, -114.3153),
}

# invented street names: these are the calls the geocoder cannot place, on purpose
FICTIONAL_STREETS = [
    "Marmot Hollow Road",
    "Pine Marten Lane",
    "Silver Tip Court",
    "Quarry Bench Trail",
    "Two Elk Draw",
    "Aspen Glade Way",
    "Cold Spring Gulch Road",
    "Lost Horse Meadow Road",
    "Beargrass Hollow Lane",
    "Flintlock Pass Road",
    "Sage Hen Bench Road",
    "Timberline Stair Way",
]

SUFFIX_ABBREV = {
    "Road": "RD", "Street": "ST", "Drive": "DR", "Lane": "LN", "Court": "CT",
    "Avenue": "AVE", "Boulevard": "BLVD", "Way": "WAY", "Trail": "TRL",
    "Circle": "CIR", "Place": "PL", "Highway": "HWY", "Parkway": "PKWY",
}

NOTES = [
    "Idaho Paramedic issue date",
    "NREMT-P issue date",
    "roster_confirmed by the training officer",
    "renewal; original certification earlier",
    "no cert on file; medic_since unknown",
]


# --------------------------------------------------------------------------- helpers
def dt_str(dt: datetime) -> str:
    return dt.strftime("%m/%d/%Y %H:%M:%S")


def day_str(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def km(a, b) -> float:
    """Cheap equirectangular distance in km - only used to pick the nearest town."""
    lat1, lon1 = a
    lat2, lon2 = b
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6371.0 * math.sqrt(x * x + y * y)


def pick(rng: random.Random, weighted):
    """Weighted choice over [(value, weight), ...]."""
    values = [v for v, _w in weighted]
    weights = [w for _v, w in weighted]
    return rng.choices(values, weights=weights, k=1)[0]


def month_split(total: int, weights: list[float]) -> list[int]:
    """Largest-remainder split of `total` over 12 months."""
    s = sum(weights)
    raw = [total * w / s for w in weights]
    out = [int(x) for x in raw]
    rest = total - sum(out)
    order = sorted(range(12), key=lambda i: (raw[i] - out[i]), reverse=True)
    for i in order[:rest]:
        out[i] += 1
    return out


# --------------------------------------------------------------------------- streets
def load_streets(osm_path: str):
    """Street names from the shared OSM reference, weighted by how much road they cover.

    Only roads in the valley core are used - a fire department does not get 40 calls a
    year on a Forest Service road 60 km up the drainage, and the centroid of one would
    drag every map point into the mountains.
    """
    agg: dict[str, dict] = {}
    with open(osm_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="|"):
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
            except (TypeError, ValueError):
                continue
            name = (row.get("name") or "").strip()
            if not name:
                continue
            if min(km((lat, lon), c) for c in CITY_CENTRES.values()) > 30.0:
                continue
            e = agg.setdefault(name, {"w": 0, "pts": []})
            e["w"] += 1
            e["pts"].append((lat, lon))
    streets = []
    for name in sorted(agg):
        pts = agg[name]["pts"]
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
        city = min(CITY_CENTRES, key=lambda c: km((lat, lon), CITY_CENTRES[c]))
        streets.append({"name": name, "w": agg[name]["w"], "pts": pts, "city": city})
    return streets


def address_variant(rng: random.Random, name: str) -> str:
    """Write a street the way crews actually type it: sometimes abbreviated, sometimes
    shouted. The normaliser in load_geo has to fold all of it back together."""
    out = name
    if rng.random() < 0.3:
        head, _, last = name.rpartition(" ")
        if last in SUFFIX_ABBREV:
            out = f"{head} {SUFFIX_ABBREV[last]}"
    if rng.random() < 0.18:
        out = out.upper()
    return out


# --------------------------------------------------------------------------- staffing
def build_people(rng: random.Random):
    people = []
    for platoon in ELK_ORDER:
        for first, last, rank, qual, since, basis in ELK_PLATOONS[platoon]:
            people.append({"first": first, "last": last, "rank": rank, "qual": qual,
                           "medic_since": since, "basis": basis, "station": "ELK",
                           "platoon": platoon, "kind": "career"})
    for platoon in GH_ORDER:
        for first, last, rank, qual, since, basis in GH_PLATOONS[platoon]:
            people.append({"first": first, "last": last, "rank": rank, "qual": qual,
                           "medic_since": since, "basis": basis, "station": "GH",
                           "platoon": platoon, "kind": "career"})
    for first, last, rank, qual, assignment in COMMAND:
        people.append({"first": first, "last": last, "rank": rank, "qual": qual,
                       "medic_since": None, "basis": None, "station": None,
                       "platoon": None, "kind": "command", "assignment": assignment,
                       "role": rank or "Administrative Assistant"})
    for first, last, since, basis in POC:
        people.append({"first": first, "last": last, "rank": "POC-Firefighter",
                       "qual": "POC-Firefighter", "medic_since": since, "basis": basis,
                       "station": "GH", "platoon": None, "kind": "poc"})
    return people


def build_schedule(rng: random.Random, people):
    """One row per person-day-assignment, plus the set of staffed (day, station) pairs."""
    crew = {p["platoon"]: p for p in people if p["platoon"]}
    platoons = {k: [p for p in people if p["platoon"] == k] for k in crew}
    by_name = {(p["first"], p["last"]): p for p in people}
    command = {p["role"]: p for p in people if p["kind"] == "command"}

    rows = []
    staffed: dict[tuple[str, str], int] = {}

    def add(p, d, assignment, work_type, qual=None, rank=None):
        rows.append({"s__work_date": day_str(d),
                     "s__first_name": p["first"], "s__last_name": p["last"],
                     "s__qualifier_name": (qual if qual is not None else p["qual"]) or None,
                     "s__work_type_name": work_type,
                     "s__personnel_rank": (rank if rank is not None else p["rank"]) or None,
                     "s__assignment_name": assignment})

    d = STAFF_START
    ordinal = 0
    while d <= STAFF_END:
        # --- Elkhorn: 24h shift, three platoons, staffed every single day
        platoon = ELK_ORDER[ordinal % len(ELK_ORDER)]
        for p in platoons[platoon]:
            add(p, d, "Elkhorn", "Regular")
        staffed[(day_str(d), "ELK")] = len(platoons[platoon])

        # --- Greenhorn: second station, part-time coverage that improves in 2026
        if rng.random() < GREENHORN_STAFF_P[d.year]:
            gplat = GH_ORDER[ordinal % len(GH_ORDER)]
            for p in platoons[gplat]:
                add(p, d, "Greenhorn", "Regular")
            staffed[(day_str(d), "GH")] = len(platoons[gplat])
            # paid-on-call fill-in, always on a POC Shift row
            if rng.random() < 0.22:
                poc = rng.choice([p for p in people if p["kind"] == "poc"])
                add(poc, d, "Greenhorn", "POC Shift")

        # --- command and support, on most days
        add(command["Chief"], d, "Chief", "Regular")
        add(command["Assistant Chief"], d, "Assistant Chief", "Regular")
        if rng.random() < 0.52:
            add(command["Division Chief"], d, "Div Chief - Prevention", "Regular")
        if rng.random() < 0.18:
            add(command["District Chief"], d, "District Chief", "Regular")
        if rng.random() < 0.5:
            add(command["Administrative Assistant"], d, "Administrative Assistant", "Regular")
        if rng.random() < 0.34:
            chief = command[rng.choice(["Assistant Chief", "Division Chief", "District Chief"])]
            add(chief, d, "On Call Chief", "On Call Chief", qual="On Call Chief")

        # --- the messy real world: trades, overtime, training, non-station work
        career = [p for p in people if p["kind"] == "career"]
        if rng.random() < 0.07:
            p = rng.choice(career)
            add(p, d, "Elkhorn" if p["station"] == "ELK" else "Greenhorn",
                rng.choice(["Trade ON", "Overtime"]))
        if rng.random() < 0.04:
            p = rng.choice(career)
            add(p, d, "Elkhorn" if p["station"] == "ELK" else "Greenhorn", "Training")
        if rng.random() < 0.04:
            add(rng.choice(career), d, "Event Staffing", "Special Assignment")
        if rng.random() < 0.025:
            add(rng.choice(career), d, "EMS Standby", "Special Assignment")
        if rng.random() < 0.02:
            add(rng.choice(career), d, "Wildland Deployment", "Special Assignment")

        d += timedelta(days=1)
        ordinal += 1

    # the real export repeats identical rows; keep one duplicate so the loader's dedupe
    # path is exercised
    if rows:
        rows.append(dict(rows[0]))
    return rows, staffed, by_name


def build_certifications(rng: random.Random, people):
    rows = []
    for p in people:
        name = f"{p['last']}, {p['first']}"
        picks = rng.sample(CERT_NAMES, k=rng.randint(2, 5))
        for cert in picks:
            issue = date(2018, 1, 1) + timedelta(days=rng.randint(0, 2400))
            tag = sum(ord(c) for c in cert) % 9000
            rows.append({"tc_ui__personnel_last_first_name": name,
                         "ts__certification_name": cert,
                         "ts__training_certification_id": f"SYN-{tag:04d} | {cert}",
                         "ts__issue_date": day_str(issue),
                         "ts__expiration_date": day_str(issue + timedelta(days=730))})
        if p["medic_since"] and p["medic_since"] != "1900-01-01":
            issue = p["medic_since"]
            rows.append({"tc_ui__personnel_last_first_name": name,
                         "ts__certification_name": "Idaho Paramedic",
                         "ts__training_certification_id": "IDPM | Idaho Paramedic",
                         "ts__issue_date": issue,
                         "ts__expiration_date": day_str(
                             date.fromisoformat(issue) + timedelta(days=730))})
    # one row with no person at all, like the real pull's blank name row
    rows.append({"tc_ui__personnel_last_first_name": None,
                 "ts__certification_name": "Idaho EMT",
                 "ts__training_certification_id": "IDEMT | Idaho EMT",
                 "ts__issue_date": "2024-10-03", "ts__expiration_date": "2028-03-31"})
    rows.sort(key=lambda r: (r["tc_ui__personnel_last_first_name"] or "",
                             r["ts__certification_name"]))
    return rows


def build_groups(people):
    def members(pred):
        return [{"id": 400000 + i, "name": f"{p['first']} {p['last']}"}
                for i, p in enumerate(p for p in people if pred(p))]

    groups = [
        {"id": 30001, "name": "All Fire", "shortcode": "ALL", "members": members(lambda p: True)},
        {"id": 30002, "name": "Duty Staff", "shortcode": "DTY",
         "members": members(lambda p: p["kind"] == "career")},
        {"id": 30003, "name": "Paid on Call", "shortcode": "POC",
         "members": members(lambda p: p["kind"] == "poc")},
        {"id": 30004, "name": "Paramedic", "shortcode": "MEDIC",
         "members": members(lambda p: p["basis"] in ("certification", "roster_confirmed")
                            and p["medic_since"] and p["medic_since"] != "1900-01-01")},
        {"id": 30005, "name": "EMT", "shortcode": "EMT",
         "members": members(lambda p: p["kind"] in ("career", "poc"))},
        {"id": 30006, "name": "Technical Rescue Team", "shortcode": "TRT",
         "members": members(lambda p: p["kind"] == "career" and p["rank"] == "Firefighter")},
        {"id": 30007, "name": "District Residents", "shortcode": "RES",
         "members": members(lambda p: p["kind"] == "poc")},
    ]
    return groups


def build_medic_csv(people):
    rows = []
    for p in people:
        if not p["medic_since"]:
            continue
        basis = p["basis"] or "estimated"
        note = ("Idaho Paramedic issue date" if basis == "certification"
                else "roster_confirmed by the training officer" if basis == "roster_confirmed"
                else "no cert on file; medic_since unknown")
        rows.append({"person": f"{p['first']} {p['last']}",
                     "medic_since": p["medic_since"],
                     "basis": basis,
                     "first_scheduled": "2025-01-02" if basis != "estimated" else "",
                     "last_scheduled": "2026-12-31" if basis != "estimated" else "",
                     "note": note})
    rows.sort(key=lambda r: r["person"])
    return rows


# --------------------------------------------------------------------------- incidents
def pick_call_type(rng: random.Random):
    """Return (code, description). Kept separate from the generic pick() because a string
    value unpacks into characters - which silently turned every code into its first digit."""
    values = [(t, d) for t, d, _w in CALL_TYPES]
    weights = [w for _t, _d, w in CALL_TYPES]
    return rng.choices(values, weights=weights, k=1)[0]


def pick_time(rng: random.Random, d: date) -> datetime:
    """Diurnal shape: quiet 02:00-06:00, busy late morning and evening."""
    hours = list(range(24))
    hw = [1.6, 1.2, 0.9, 0.8, 0.7, 0.8, 1.1, 1.5, 2.0, 2.3, 2.2, 2.1,
          1.9, 1.8, 1.9, 2.0, 2.1, 2.0, 1.8, 1.6, 1.5, 1.4, 1.4, 1.6]
    h = rng.choices(hours, weights=hw, k=1)[0]
    return datetime(d.year, d.month, d.day, h, rng.randrange(60), rng.randrange(60))


def build_incidents(rng: random.Random, streets, staffed, people):
    street_names = [s["name"] for s in streets]
    street_w = [s["w"] for s in streets]
    street_by_name = {s["name"]: s for s in streets}

    incidents = []

    # ---------------- NFIRS era 2020-2025
    # Numbering is per year and follows the clock, like the real export: the incident
    # number is assigned after the month's rows are sorted by alarm time.
    month_w = [0.86, 0.84, 0.94, 0.96, 1.02, 1.12, 1.18, 1.14, 1.04, 0.96, 0.90, 0.94]
    for year, count in YEAR_COUNTS.items():
        year_incs = []
        for m, n in enumerate(month_split(count, month_w), start=1):
            for _ in range(n):
                d = date(year, m, rng.randint(1, 28))
                year_incs.append(_make_incident(
                    rng, "nfirs", pick_time(rng, d), "",
                    streets, street_names, street_w, street_by_name, staffed))
        year_incs.sort(key=lambda i: i["dt"])
        for seq, inc in enumerate(year_incs, start=1):
            inc["key"] = f"{year % 100:02d}-{seq:03d}"
        incidents.extend(year_incs)

    # ---------------- NERIS era 2026 (Jan-Oct, reporting lag shape)
    neris = []
    for m, n in NERIS_MONTH_COUNTS.items():
        for _ in range(n):
            d = date(NERIS_YEAR, m, rng.randint(1, 28))
            neris.append(_make_incident(
                rng, "neris", pick_time(rng, d), "",
                streets, street_names, street_w, street_by_name, staffed))
    neris.sort(key=lambda i: i["dt"])
    for seq, inc in enumerate(neris, start=1):
        key, scheme = _neris_key(rng, seq)
        inc["key"] = key
        # an unprefixed number carries no jurisdiction, so the zone is the only signal
        if scheme == "dash":
            inc["zone"] = pick(rng, [("Sun Valley", 45), ("BCSO North", 20),
                                     ("BCSO Central", 12), ("", 23)])
    incidents.extend(neris)

    incidents.sort(key=lambda i: (i["dt"], i["key"]))
    return incidents


def _neris_key(rng: random.Random, seq: int):
    """The three numbering schemes the department actually mixes, and whether the number
    starts with an agency tag (the only authoritative jurisdiction signal)."""
    roll = rng.random()
    if roll < 0.40:
        return f"SVFD{NERIS_YEAR}{seq:05d}", "prefixed"
    if roll < 0.70:
        return f"NBCFD{NERIS_YEAR}{seq:05d}", "prefixed"
    return f"{NERIS_YEAR % 100:02d}-{seq:03d}", "dash"


def _make_incident(rng, era, dt, key, streets, street_names, street_w, street_by_name,
                   staffed):
    code, desc = pick_call_type(rng)

    # station mix: Elkhorn (HQ) carries most of it, Griffin Butte is a reporting location
    station = pick(rng, STATION_WEIGHTS)
    if station == "":
        station_raw = "City Hall" if rng.random() < 0.05 else ""
    else:
        station_raw = pick(rng, STATION_SPELLINGS[station])

    staffed_day = bool(station) and staffed.get((day_str(dt), station)) is not None

    rt_base = rng.lognormvariate(math.log(RT_MEDIAN), RT_SIGMA)
    mod = {"ELK": 1.0, "GH": 1.06, "GB": 1.15, "": 1.03}[station]
    if not staffed_day and station:
        mod *= 1.22                      # answered by the next station over
    mutual_aid = "22. Mutual Aid Responses Given" if rng.random() < MUTUAL_AID_P else ""
    if mutual_aid:
        mod *= 1.35                      # mutual aid means driving out of the district
    if rng.random() < SLOW_ARRIVAL_P:
        rt = 26.0 + rng.random() * 54.0  # remote trailhead, highway closure, long travel
    else:
        rt = rt_base * mod
    rt = round(min(90.0, max(0.5, rt)), 2)

    scene = min(400.0, rng.lognormvariate(math.log(SCENE_MEDIAN), SCENE_SIGMA))
    if rng.random() < 0.03:
        scene = rng.uniform(240, 400)    # extrication / investigation
    scene = round(scene, 1)
    controlled = round(scene * rng.uniform(0.35, 0.75), 1)

    # ---- address
    roll = rng.random()
    street = number = city = None
    lat = lon = None
    if roll < NO_ADDRESS_P:
        pass
    elif roll < NO_ADDRESS_P + FICTIONAL_STREET_P:
        street = address_variant(rng, rng.choice(FICTIONAL_STREETS))
        number = str(rng.randint(1, 900))
        city = rng.choice(["SUN VALLEY", "KETCHUM", "HAILEY"])
    else:
        name = rng.choices(street_names, weights=street_w, k=1)[0]
        s = street_by_name[name]
        street = address_variant(rng, name)
        number = str(rng.randint(1, 900) if rng.random() < 0.88 else rng.randint(1000, 14000))
        city = s["city"]
        if rng.random() < REPORTED_POINT_P:
            plat, plon = rng.choice(s["pts"])
            lat = round(plat + rng.uniform(-0.0009, 0.0009), 7)
            lon = round(plon + rng.uniform(-0.0009, 0.0009), 7)

    # ---- zone / district, mirroring how patchy the real field is
    zone = pick(rng, [("Not Applicable", 38), ("Sun Valley", 24), ("1", 15),
                      ("BCSO North", 9), ("2", 5), ("", 9)])
    district = pick(rng, [("", 62), ("1", 20), ("2", 12), ("3", 6)])

    return {"era": era, "key": key, "dt": dt, "station": station or None,
            "station_raw": station_raw, "zone": zone, "district": district,
            "city": city, "street": street, "number": number,
            "type_code": code, "type_desc": desc, "mutual_aid": mutual_aid,
            "rt": rt, "scene": scene, "controlled": controlled,
            "lat": lat, "lon": lon, "staffed": staffed_day}


def build_units(rng: random.Random, incidents):
    """One row per apparatus per incident, with timings consistent with the incident."""
    rows = []
    for inc in incidents:
        share = NFIRS_UNIT_SHARE if inc["era"] == "nfirs" else NERIS_UNIT_SHARE
        if inc["era"] == "neris" and "SVFD" not in inc["key"] and "NBCFD" not in inc["key"]:
            share *= 0.6                 # dispatch-numbered runs are the thinnest records
        if rng.random() > share:
            continue
        family = inc["type_code"][0]
        n = {"3": rng.choice([1, 1, 2, 2, 3]), "1": rng.choice([2, 2, 3, 3]),
             "4": rng.choice([1, 2]), "5": rng.choice([1, 1, 2]),
             "6": rng.choice([1, 1, 2]), "7": rng.choice([1, 2]),
             "8": rng.choice([2, 3])}.get(family, rng.choice([1, 2]))
        station = inc["station"] or "ELK"
        pool = APPARATUS.get(station, APPARATUS["ELK"])
        picks = rng.sample(pool, k=min(n, len(pool)))
        for rtype, spellings in picks:
            raw_id = rng.choice(spellings)
            dispatch = inc["dt"] + timedelta(minutes=rng.uniform(0.0, 0.6))
            travel = max(0.4, inc["rt"] + rng.gauss(0, 1.4) if inc["rt"] is not None else 6.0)
            arrival = dispatch + timedelta(minutes=travel)
            scene = inc["scene"] * rng.uniform(0.85, 1.15)
            cleared = arrival + timedelta(minutes=scene)
            in_service = cleared + timedelta(minutes=rng.uniform(0, 4))
            rows.append({
                "incident_key": inc["key"], "raw_id": raw_id,
                "resource_type": rtype,
                "dispatch": dispatch, "arrival": arrival, "cleared": cleared,
                "in_service": in_service,
                "dispatch_to_arrival": round(travel, 2),
                "arrived_to_cleared": round(scene, 2),
                "arrived_to_service": round(scene + (in_service - cleared).total_seconds() / 60, 2),
                "unit_hours": round((cleared - dispatch).total_seconds() / 3600, 2),
                "type_code": inc["type_code"], "type_desc": inc["type_desc"],
                "station_raw": inc["station_raw"], "district": inc["district"],
            })
    # a handful of orphan unit rows with no incident number, like the real export
    for _ in range(8):
        rtype, spellings = rng.choice(APPARATUS["ELK"])
        rig = rng.choice(spellings)
        d = STAFF_START + timedelta(days=rng.randint(0, 400), minutes=rng.randint(0, 1439))
        rows.append({"incident_key": None, "raw_id": rig, "resource_type": rtype,
                     "dispatch": d, "arrival": d + timedelta(minutes=6),
                     "cleared": d + timedelta(minutes=40),
                     "in_service": d + timedelta(minutes=42),
                     "dispatch_to_arrival": 6.0, "arrived_to_cleared": 34.0,
                     "arrived_to_service": 36.0, "unit_hours": 0.7,
                     "type_code": "611", "type_desc": "Dispatched and cancelled en route",
                     "station_raw": "Elkhorn Station", "district": ""})
    return rows


# --------------------------------------------------------------------------- writers
def write_csv(path: str, header: list[str], rows) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator="\n")
        w.writerow(header)
        n = 0
        for r in rows:
            w.writerow(r)
            n += 1
    return n


def write_json(path: str, payload) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    return 1


def nfirs_row(inc) -> list:
    alarm = inc["dt"]
    arrival = alarm + timedelta(minutes=inc["rt"] or 6)
    cleared = arrival + timedelta(minutes=inc["scene"])
    controlled = arrival + timedelta(minutes=inc["controlled"])
    in_service = cleared + timedelta(minutes=2)

    def hhmmss(minutes: float) -> str:
        s = int(round(minutes * 60))
        return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"

    return [
        dt_str(alarm),
        f"{inc['rt']:.2f}",
        f"{inc['rt'] + inc['controlled']:.2f}",
        f"{inc['rt']:.2f}",
        "",
        "",
        f"{max(inc['rt'] + 1.5, 1):.2f}",
        f"{inc['rt'] + inc['scene']:.2f}",
        dt_str(arrival),
        (inc["city"] or "Sun Valley").title(),
        dt_str(controlled) if inc["controlled"] and rng_keep(inc) else "",
        alarm.strftime("%m/%d/%Y"),
        inc["district"],
        hhmmss(inc["rt"] or 6),
        "",
        "",
        dt_str(in_service),
        f"{inc['lat']}" if inc["lat"] is not None else "",
        f"{inc['lon']}" if inc["lon"] is not None else "",
        inc["mutual_aid"],
        inc["key"],
        "",                      # PSAP To Alarm is 91% null in the real export
        inc["street"] or "",
        inc["number"] or "",
        inc["type_desc"],
        inc["type_code"],
        inc["zone"],
        inc["station_raw"],
        "",
    ]


def stable_hash(key: str, mod: int) -> int:
    """Deterministic per-row number. sum(ord(...)) looked fine and was not: the incident
    numbers share long prefixes, so it clustered every row of a year into one bucket."""
    return zlib.crc32(key.encode("utf-8")) % mod


def rng_keep(inc) -> bool:
    """Deterministic per-row coin from the incident key: ~20% of rows carry a controlled
    time, matching the real export's 81%-null lifecycle field."""
    return stable_hash(inc["key"], 5) == 0


def neris_row(inc) -> list:
    """NERIS populates one of two timestamp families per record, never both - 58% dispatch
    side, 24% incident side, 15% date only, 3% nothing at all."""
    k = stable_hash(inc["key"], 100)
    mode = ("dispatch" if k < 58 else "incident" if k < 82
            else "date" if k < 97 else "none")
    alarm = inc["dt"]
    arrival = alarm + timedelta(minutes=inc["rt"] or 6)
    cleared = arrival + timedelta(minutes=inc["scene"])
    in_service = cleared + timedelta(minutes=2)

    d_alarm = dt_str(alarm) if mode == "dispatch" else ""
    d_arr = dt_str(arrival) if mode == "dispatch" else ""
    i_alarm = dt_str(alarm) if mode == "incident" else ""
    i_arr = dt_str(arrival) if mode == "incident" else ""
    i_date = alarm.strftime("%m/%d/%Y") if mode in ("date", "none", "dispatch", "incident") else ""
    if mode == "none":
        i_date = ""
    code = rng_code(inc) if k < int(NERIS_TYPED_CODE_SHARE * 100) + 1 else ""
    aid = 1 if inc["mutual_aid"] else 0

    return [
        d_alarm,
        d_arr,
        f"{int((cleared - alarm).total_seconds()):,}" if mode == "dispatch" else "",
        (inc["city"] or "SUN VALLEY").title(),
        dt_str(cleared) if mode == "dispatch" else "",
        code,
        inc["key"],
        str(aid) if inc["mutual_aid"] else "",
        str(rng_aid_recv(inc)) if inc["mutual_aid"] else "",
        inc["district"],
        inc["zone"],
        inc["station_raw"],
        "Fire NERIS",            # constant on every row of the real pull
        i_alarm,
        f"{inc['rt'] * 60:.0f}" if (mode == "incident" and inc["rt"]) else "",
        i_arr,
        i_date,
        dt_str(in_service) if mode in ("dispatch", "incident") else "",
    ]


def rng_code(inc) -> str:
    """The dispatch code the report would carry.

    The real NERIS pull leaves `Dispatch Incident Code (fiDispatch.05)` blank on 98% of
    rows, which is why the NERIS era classifies as Untyped. The public build writes the
    incident's own call-type code instead, so the taxonomy has something to classify and
    the call-type view is not empty. The 3% left blank keep the Untyped bucket visible.
    """
    return inc["type_code"]


def rng_aid_recv(inc) -> int:
    return 1 + stable_hash(inc["key"], 3)


def apparatus_row(u) -> list:
    return [
        dt_str(u["arrival"]),
        f"{u['arrived_to_cleared']:.2f}",
        f"{u['arrived_to_service']:.2f}",
        dt_str(u["cleared"]),
        "",
        "",
        dt_str(u["dispatch"]),
        f"{u['dispatch_to_arrival']:.2f}",
        u["raw_id"],
        dt_str(u["in_service"]),
        u["resource_type"],
        dt_str(u["dispatch"]),
        u["district"],
        u["incident_key"] or "",
        u["type_desc"],
        u["type_code"],
        u["station_raw"],
    ]


# The NERIS era records its incident type as a standardised three-part string rather than
# an NFIRS code, and Elite's incident list is where the exporter can find it. These are the
# vocabulary strings the real department uses, weighted like a fire-based EMS agency;
# roughly one incident in twenty has no type at all, which is what keeps the Untyped
# family small but nonzero.
NERIS_TYPE_VOCAB = [
    ("Medical - Injury / Trauma - Fall", 13),
    ("Medical - Illness - Chest Pain (Non - Trauma)", 6),
    ("Medical - Illness", 6),
    ("Medical - Illness - Breathing Problems", 4),
    ("Medical - Injury / Trauma - Motor Vehicle Collision", 4),
    ("Medical - Illness - Altered Mental Status", 3),
    ("Medical - Illness - Unconscious Victim", 3),
    ("Medical - Injury / Trauma - Hemorrhage / Laceration", 3),
    ("No Emergency - False Alarm - Accidental Alarm", 12),
    ("No Emergency - Cancelled", 8),
    ("Public Service - Alarms (Non Medical) - Fire / Smoke Alarm", 7),
    ("Public Service - Citizen Assist - Citizen Assist / Service Call", 6),
    ("Public Service - Other - Standby", 3),
    ("Hazardous Situation - Hazardous Materials - Gas Leak / Gas Odor", 3),
    ("Hazardous Situation - Hazardous Materials - Carbon Monoxide Release", 2),
    ("Hazardous Situation - Hazard Nonchemical - Motor Vehicle Collision", 3),
    ("Rescue - Outside - Backcountry Rescue", 3),
    ("Fire - Structure Fire - Structural Involvement", 2),
    ("Fire - Structure Fire - Confined Cooking / Appliance Fire", 1),
    ("Fire - Outside Fire - Vegetation / Grass Fire", 1),
    ("Fire - Transportation Fire - Vehicle Fire - Commercial", 1),
]
NERIS_TYPE_BLANK_P = 0.05


def address_row(rng: random.Random, inc) -> list:
    if inc["street"] and inc["number"]:
        full = f"{inc['number']} {inc['street']} {inc['city'] or 'SUN VALLEY'}"
    else:
        full = (inc["city"] or "SUN VALLEY").title()
    type_text = "" if rng.random() < NERIS_TYPE_BLANK_P else pick(rng, NERIS_TYPE_VOCAB)
    return [inc["key"], type_text, full,
            rng.choice(["Completed", "Exported to NERIS", "In Progress"]),
            dt_str(inc["dt"] + timedelta(minutes=rng.randint(0, 12)))]


# --------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help=f"output root, raw/ + curated/ are written under it (default {DEFAULT_OUT})")
    ap.add_argument("--osm", default=os.path.join(ROOT, "data", "raw"),
                    help="directory holding the public osm_streets.csv reference")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    raw = os.path.join(out, "raw")
    curated = os.path.join(out, "curated")
    osm = os.path.join(args.osm, "osm_streets.csv")
    if not os.path.exists(osm):
        raise SystemExit(f"no OSM reference at {osm} - it is public data and shared with "
                         "the real build, not generated here")

    rng = random.Random(args.seed)
    streets = load_streets(osm)
    if not streets:
        raise SystemExit("no usable streets in the OSM reference")

    people = build_people(rng)
    schedule, staffed, _by_name = build_schedule(rng, people)
    incidents = build_incidents(rng, streets, staffed, people)
    units = build_units(rng, incidents)

    print(f"synthetic dataset -> {out}")
    print(f"  people        {len(people):>6}  (streets pool {len(streets)})")

    n = write_csv(os.path.join(raw, "elite_incident_fact.csv"), INCIDENT_HEADER,
                  (nfirs_row(i) for i in incidents if i["era"] == "nfirs"))
    print(f"  elite_incident_fact.csv        {n:>6} rows")

    n = write_csv(os.path.join(raw, "elite_neris_incident_fact.csv"), NERIS_HEADER,
                  (neris_row(i) for i in incidents if i["era"] == "neris"))
    print(f"  elite_neris_incident_fact.csv  {n:>6} rows")

    addr_rng = random.Random(args.seed + 7)
    n = write_csv(os.path.join(raw, "elite_neris_incidents.csv"),
                  ["incident_number", "primary_incident_type", "full_address", "status",
                   "call_created"],
                  (address_row(addr_rng, i) for i in incidents if i["era"] == "neris"))
    print(f"  elite_neris_incidents.csv      {n:>6} rows")

    n = write_csv(os.path.join(raw, "elite_apparatus_fact.csv"), APPARATUS_HEADER,
                  (apparatus_row(u) for u in units))
    print(f"  elite_apparatus_fact.csv       {n:>6} rows")

    write_json(os.path.join(raw, "fd_shift_coverage.json"),
               {"rows": schedule, "pages": 1, "error": None})
    print(f"  fd_shift_coverage.json         {len(schedule):>6} rows")

    certs = build_certifications(rng, people)
    write_json(os.path.join(raw, "fd_certifications.json"),
               {"rows": certs, "pages": 1, "error": None})
    print(f"  fd_certifications.json         {len(certs):>6} rows")

    write_json(os.path.join(raw, "fd_groups.json"), build_groups(people))
    write_json(os.path.join(raw, "fd_personnel_shift.json"),
               {"rows": [{"n__personnel_shift_id": "A", "col_1": "5", "comp_col_101567": "38%"},
                         {"n__personnel_shift_id": "B", "col_1": "3", "comp_col_101567": "23%"},
                         {"n__personnel_shift_id": "C", "col_1": "5", "comp_col_101567": "38%"}],
                "pages": 1,
                "total": {"n__personnel_shift_id": "Total", "col_1": "13",
                          "comp_col_101567": "100%"},
                "error": None})

    medics = build_medic_csv(people)
    write_csv(os.path.join(curated, "dim_person_medic.csv"),
              ["person", "medic_since", "basis", "first_scheduled", "last_scheduled", "note"],
              ([m["person"], m["medic_since"], m["basis"], m["first_scheduled"],
                m["last_scheduled"], m["note"]] for m in medics))
    print(f"  curated/dim_person_medic.csv   {len(medics):>6} rows")

    # ---- a short shape report so a human can see what was generated without querying
    by_year = {}
    for i in incidents:
        by_year.setdefault(i["dt"].year, []).append(i)
    print("\n  incidents by year: " + ", ".join(
        f"{y}:{len(v)}" for y, v in sorted(by_year.items())))
    in_scope = [i for i in incidents
                if not i["mutual_aid"]
                and i["type_code"] not in ("141", "142", "143")
                and (i["rt"] or 0) <= 25]
    rts = sorted(i["rt"] for i in in_scope if i["rt"] is not None)
    if rts:
        q = lambda p: rts[min(len(rts) - 1, int(len(rts) * p))]
        print(f"  in-scope share: {len(in_scope) / len(incidents) * 100:.1f}%   "
              f"p50 {q(.5):.1f}  p90 {q(.9):.1f} min")
    staff_days = len({k[0] for k in staffed})
    print(f"  station-days staffed: {len(staffed)} over {staff_days} calendar days")


if __name__ == "__main__":
    main()
