#!/usr/bin/env python3
"""Call-type taxonomy: NFIRS code -> (family, subtype).

The department thinks about call types in six families, and every family drills down
into the subtypes the crews actually talk about:

  Fire                Structure fire | Vehicle fire | Alarms | HazMat | Other fire
  Wildland            Forest / timber | Brush / grass | Vegetation, other
  EMS                 EMS call | Medical assist | Rescue / EMS, other
  Motor vehicle accident   With injury | No injury | Vehicle extrication | Cleanup / other
  Backcountry rescue  Search | Water / ice | Other rescue
  Service             Event coverage / standby | Public assist |
                      Cancelled / no incident found | Weather & disaster | Other service

Motor vehicle accidents get their own family, and a vehicle extrication is an MVA with
entrapment rather than a rescue - so it belongs with the accidents, not with backcountry
rescue.

Two things this replaced, deliberately:

1. **Keyword matching on free-text descriptions.** `\\bconfined\\b` matched "Cooking fire,
   confined to container" and `\\bwater\\b` matched "Water problem, other", so both were
   being classified as backcountry rescues. The NFIRS type code is standardised and
   complete in this export (three blank rows across 4,828), so the code decides and the
   description is only a fallback.
2. **Mutual aid as a class.** 382 of the 405 rows previously in `wildland_mutualaid` were
   there because mutual aid was *given*, and only 23 for a wildland keyword - a
   structure fire run for a neighbouring agency was filed as wildland. Mutual aid is a
   flag (it is already `fact_incident.mutual_aid_given`), not a type of call.

Records with no NFIRS code - the whole NERIS era, whose "Incident Type" column returns
the constant string "Fire NERIS" - are typed `Untyped` rather than being swept into a
real family, so the gap is visible in every chart instead of silently inflating Fire.
"""
from __future__ import annotations

import re

FAMILIES = ("Fire", "Wildland", "EMS", "Motor vehicle accident",
            "Backcountry rescue", "Service", "Untyped")

# --------------------------------------------------------------------- explicit codes
# NFIRS 5.0 series: 1xx fire, 2xx overpressure, 3xx rescue & EMS, 4xx hazardous
# condition, 5xx service call, 6xx good intent, 7xx false alarm, 8xx natural disaster.
CODE_MAP: dict[str, tuple[str, str]] = {}


def _put(codes: str, family: str, subtype: str) -> None:
    for c in codes.split():
        CODE_MAP[c] = (family, subtype)


# fire
_put("111 112 113 114 116 121", "Fire", "Structure fire")
_put("122 131 132 138", "Fire", "Vehicle fire")
_put("100 118 150 151 154 160 163", "Fire", "Other fire")
_put("651 652 653", "Fire", "Alarms")          # smoke scares - no fire found
_put("700 721 730 731 733 734 735 736 740 743 744 745 746", "Fire", "Alarms")
_put("400 410 411 412 413 420 422 424", "Fire", "HazMat")   # spills, gas, CO

# wildland - its own family, not a subtype of fire
_put("141", "Wildland", "Forest / timber")
_put("142 143", "Wildland", "Brush / grass")
_put("140", "Wildland", "Vegetation, other")

# EMS - backcountry rescue is excluded because it is its own family
_put("311", "EMS", "Medical assist")
_put("320 321", "EMS", "EMS call")
_put("300", "EMS", "Rescue / EMS, other")

# motor vehicle accidents, mutual-aid runs included.
# 350/351/352 are extrications from a vehicle, i.e. an MVA with entrapment.
_put("322 323", "Motor vehicle accident", "With injury")
_put("324", "Motor vehicle accident", "No injury")
_put("350 351 352", "Motor vehicle accident", "Vehicle extrication")
_put("463", "Motor vehicle accident", "Cleanup / other")

# rescue
_put("340", "Backcountry rescue", "Search")
_put("363", "Backcountry rescue", "Water / ice")
_put("331 353", "Backcountry rescue", "Other rescue")

# service
_put("381 462 571", "Service", "Event coverage / standby")
_put("480 510 511 520 522 531 540 542 550 551 553 554 555 561",
     "Service", "Public assist")
_put("600 611 622 641 650", "Service", "Cancelled / no incident found")
_put("800 812 813", "Service", "Weather & disaster")
_put("221 251 440 441 444 445 460 500 671 900", "Service", "Other service")

# ------------------------------------------------------------- fallback for new codes
# A code the map has not seen yet falls back to its series, so a new NFIRS code cannot
# silently become "Untyped" and disappear from the chart.
SERIES_FALLBACK = {
    "1": ("Fire", "Other fire"),
    "2": ("Service", "Other service"),
    "3": ("EMS", "EMS call"),
    "4": ("Fire", "HazMat"),
    "5": ("Service", "Public assist"),
    "6": ("Service", "Cancelled / no incident found"),
    "7": ("Fire", "Alarms"),
    "8": ("Service", "Weather & disaster"),
    "9": ("Service", "Other service"),
}

WILDLAND = re.compile(r"\b(wildland|brush|grass|forest|wood|timber|crop|vegetation)\b",
                      re.I)
# the NERIS export puts this literal string in the type column on every row
PLACEHOLDER = re.compile(r"^fire\s+neris$", re.I)

UNTYPED = ("Untyped", "No type code on the report")


def classify(type_code: str | None, type_desc: str | None,
             mutual_aid_given: str | None = None) -> tuple[str, str]:
    """Return (family, subtype). The mutual-aid flag is accepted for signature
    compatibility and deliberately does not change the answer."""
    code = (type_code or "").strip()
    if code in CODE_MAP:
        return CODE_MAP[code]
    if code[:1].isdigit():
        hit = SERIES_FALLBACK.get(code[:1])
        if hit:
            return hit

    desc = (type_desc or "").strip()
    if desc and not PLACEHOLDER.match(desc) and WILDLAND.search(desc):
        return ("Wildland", "Vegetation, other")
    return UNTYPED


# ------------------------------------------------------------ NERIS type vocabulary
# The NERIS era carries no NFIRS code, but Elite does record a standardised three-part
# NERIS type ("Medical - Injury / Trauma - Fall"). That is a fixed vocabulary, so it can
# be mapped by category rather than by guessing at free text - the failure mode that made
# the original keyword classifier file cooking fires as rescues.
#
# Rules are (required token group, family, subtype) and are tested in order against the
# upper-cased type string, so a more specific rule must come before its category.
NERIS_RULES: list[tuple[tuple[str, ...], str, str]] = [
    # fire, by the part that says what burned
    (("FIRE", "STRUCTURE"), "Fire", "Structure fire"),
    (("FIRE", "OUTSIDE", "VEGETATION"), "Wildland", "Brush / grass"),
    (("FIRE", "OUTSIDE", "FOREST"), "Wildland", "Forest / timber"),
    (("FIRE", "TRANSPORTATION"), "Fire", "Vehicle fire"),
    (("FIRE",), "Fire", "Other fire"),
    # medical
    (("MEDICAL",), "EMS", "EMS call"),
    # rescue: an extrication is an accident with entrapment, not a backcountry rescue
    (("RESCUE", "TRANSPORTATION"), "Motor vehicle accident", "Vehicle extrication"),
    (("RESCUE", "SEARCH"), "Backcountry rescue", "Search"),
    (("RESCUE",), "Backcountry rescue", "Other rescue"),
    # hazardous situation
    (("HAZARDOUS", "MOTOR VEHICLE"), "Motor vehicle accident", "No injury"),
    (("HAZARDOUS", "INVESTIGATION"), "Fire", "Alarms"),
    (("HAZARDOUS", "ELECTRICAL"), "Service", "Other service"),
    (("HAZARDOUS", "POWER LINE"), "Service", "Other service"),
    (("HAZARDOUS",), "Fire", "HazMat"),
    # public service
    (("PUBLIC SERVICE", "ALARMS"), "Fire", "Alarms"),
    (("PUBLIC SERVICE", "DISASTER"), "Service", "Weather & disaster"),
    (("PUBLIC SERVICE", "WEATHER"), "Service", "Weather & disaster"),
    (("PUBLIC SERVICE", "STANDBY"), "Service", "Event coverage / standby"),
    (("PUBLIC SERVICE",), "Service", "Public assist"),
    # no emergency
    (("NO EMERGENCY", "FALSE ALARM"), "Fire", "Alarms"),
    (("NO EMERGENCY", "CANCELLED"), "Service", "Cancelled / no incident found"),
    (("NO EMERGENCY", "GOOD INTENT", "BURNING"), "Service", "Public assist"),
    (("NO EMERGENCY",), "Service", "Cancelled / no incident found"),
    # law enforcement support is a public assist in practice
    (("LAW ENFORCEMENT",), "Service", "Public assist"),
]


def classify_neris(type_text: str | None) -> tuple[str, str]:
    """Map a NERIS three-part incident type onto (family, subtype)."""
    t = " ".join((type_text or "").upper().split())
    if not t:
        return UNTYPED
    for tokens, family, subtype in NERIS_RULES:
        if all(tok in t for tok in tokens):
            return (family, subtype)
    return UNTYPED
