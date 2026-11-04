#!/usr/bin/env python3
"""Enrich NERIS-era incidents from Elite's own NERIS incident list.

The NERIS-era CSV export carries neither an address nor a usable incident type: its type
column is the constant string "Fire NERIS" on every row. Both fields do exist in Elite -
the NERIS Fire incident list shows `NerisFullAddress` and `Primary Incident Type` - so
`etl/pull/elite_neris_incidents.py` scrapes them once into
`data/raw/elite_neris_incidents.csv` and this loader applies them.

Two things are written back onto fact_incident:

  street_name / street_number / city   from the address, cleaned of the noise the field
                                       carries ("... City of", "MM 143 STATE HIGHWAY 75",
                                       "A Road / B Road" intersections)
  incident_class / incident_subtype    from the NERIS type vocabulary (etl/taxonomy.py),
                                       replacing 'Untyped' where a type is known

Runs after the staffing loader and before the geo loader, so geocoding can use the
addresses this step supplies.
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3

from taxonomy import classify_neris

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.environ.get("SVFD_RAW") or os.path.join(ROOT, "data", "raw")
DB = os.environ.get("SVFD_DB") or os.path.join(ROOT, "warehouse.db")
SRC = os.path.join(RAW, "elite_neris_incidents.csv")

CITIES = ("SUN VALLEY", "HAILEY", "KETCHUM", "BELLEVUE", "CAREY", "PICABO", "STANLEY",
          "BLAINE COUNTY")


def split_address(full: str) -> tuple[str | None, str | None, str | None]:
    """'124 West CHANNEL Lane HAILEY' -> ('West CHANNEL Lane', '124', 'HAILEY')."""
    text = " ".join((full or "").split())
    text = re.sub(r"\s*\bCity of\b\s*$", "", text, flags=re.I)
    text = re.sub(r"^\s*\bMM\s*\d+\s*", "", text, flags=re.I)
    if " / " in text:
        text = text.split(" / ")[0].strip()
    city = None
    for c in sorted(CITIES, key=len, reverse=True):
        if text.upper().endswith(" " + c):
            city = c.title()
            text = text[: -(len(c) + 1)].strip()
            break
    number = None
    head, _, rest = text.partition(" ")
    if head.isdigit():
        number, text = head, rest.strip()
    return (text or None), number, city


def main() -> None:
    if not os.path.exists(DB):
        raise SystemExit("warehouse.db missing - run etl/build.py")
    if not os.path.exists(SRC):
        print(f"  no {os.path.basename(SRC)} - NERIS era keeps its export defaults")
        return

    con = sqlite3.connect(DB)
    addr_n = type_n = 0
    still_untyped = 0
    with open(SRC, newline="") as f:
        for row in csv.DictReader(f):
            key = (row["incident_number"] or "").strip()
            if not key:
                continue
            addr = (row["full_address"] or "").strip()
            if addr:
                street, number, city = split_address(addr)
                addr_n += con.execute(
                    "UPDATE fact_incident SET street_name=?, street_number=?,"
                    " city=COALESCE(city, ?)"
                    " WHERE incident_key=? AND (street_name IS NULL OR street_name='')",
                    (street, number, city, key)).rowcount
            text = (row["primary_incident_type"] or "").strip()
            if text:
                family, subtype = classify_neris(text)
                if family != "Untyped":
                    type_n += con.execute(
                        "UPDATE fact_incident SET incident_class=?, incident_subtype=?,"
                        " type_desc=COALESCE(NULLIF(type_desc, 'Fire NERIS'), ?)"
                        " WHERE incident_key=? AND (incident_class IS NULL"
                        " OR incident_class='Untyped')",
                        (family, subtype, text, key)).rowcount
    con.commit()

    still_untyped = con.execute(
        "SELECT COUNT(*) FROM fact_incident WHERE is_actual=1 AND incident_class='Untyped'"
    ).fetchone()[0]
    typed_total = con.execute(
        "SELECT COUNT(*) FROM fact_incident WHERE is_actual=1 AND schema_era='neris'"
    ).fetchone()[0]
    print(f"  NERIS enrichment: {addr_n} addresses, {type_n} types applied "
          f"({typed_total} NERIS-era incidents, {still_untyped} still Untyped)")
    con.close()


if __name__ == "__main__":
    main()
