#!/usr/bin/env python3
"""Build the whole warehouse from raw pulls, in order.

  1. etl/load_elite.py  - NFIRS era facts, unit responses, dimensions
  2. etl/load_neris.py  - NERIS era facts, mapped onto the same table
  3. etl/load_staffing.py - First Due schedule, certifications, medic dimension
  4. etl/load_neris_meta.py - NERIS address + incident type from Elite's incident list
  5. etl/load_geo.py      - offline geocoding of incident addresses against OSM streets

Run from the project root:  python3 etl/build.py

Inputs and outputs are redirected with environment variables so a synthetic build never
touches the real data:

  SVFD_RAW      input directory         (default data/raw)
  SVFD_DB       output database         (default warehouse.db)
  SVFD_CURATED  curated input directory (default data/curated)
  SVFD_OSM      OSM reference directory (default data/raw - public data, shared)

Example:
  SVFD_RAW=data/synthetic/raw SVFD_CURATED=data/synthetic/curated \
  SVFD_DB=data/synthetic/warehouse.db python3 etl/build.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.abspath(os.environ.get("SVFD_RAW") or os.path.join(ROOT, "data", "raw"))
DB = os.path.abspath(os.environ.get("SVFD_DB") or os.path.join(ROOT, "warehouse.db"))
STEPS = ["load_elite.py", "load_neris.py", "load_staffing.py",
         "load_neris_meta.py", "load_geo.py"]


def main():
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    print(f"raw: {RAW}", flush=True)
    print(f"db:  {DB}", flush=True)
    if os.path.exists(DB):
        os.remove(DB)
        print(f"removed old {os.path.basename(DB)}", flush=True)
    for step in STEPS:
        path = os.path.join(ROOT, "etl", step)
        if not os.path.exists(path):
            print(f"  skip {step} (not written yet)")
            continue
        print(f"\n== {step}")
        res = subprocess.run([sys.executable, path], cwd=ROOT)
        if res.returncode != 0:
            raise SystemExit(f"{step} failed with exit code {res.returncode}")
    print("\nbuild complete")


if __name__ == "__main__":
    main()
