"""Add cells the project doesn't have yet, keeping every existing id intact.

build_notebook.py rebuilds from scratch (new ids), which would break the app layout that
references cells by id. This adds only what is missing, matched on label.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://app.hex.tech/api/v1"
PROJECT = "01a1a804-99cb-7342-9ee2-847df32397d8"
CONN = "01a1a802-223c-758c-ada3-70be18d99378"
HERE = Path(__file__).resolve().parent
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")

FOOTER = """### About these numbers

Every figure is computed from the department's incident and staffing records at the moment the
page runs, not stored as a value, so a definition can change without a rebuild.

**In scope** means an incident where no mutual aid was given, the call was not a wildland
deployment, and the first unit arrived within 25 minutes. Work outside that basket is reported
as burden in unit-hours. **Unit-hours** are dispatch to cleared, summed per apparatus. **Time on
scene** is arrival to last unit cleared. Percentiles are nearest-rank, so they can be quoted
exactly.

Records come from the incident reporting system and the scheduling system, joined on date and
station. No patient names, addresses or care records are used or displayed.

This build is synthetic: names, addresses and incident numbers are invented. The newest months
are always incomplete because reports are written after the fact."""


def token() -> str:
    for line in Path(os.path.expanduser("~/.hermes/.env")).read_text().splitlines():
        if line.startswith("HEX_API_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no token")


TOKEN = token()


def call(method, path, payload=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode() if payload else None, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                 "Accept": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


status, cells = call("GET", f"/cells?projectId={PROJECT}&limit=100")
existing = {c.get("label") for c in (cells or {}).get("values", [])}
print("existing:", sorted(x for x in existing if x))

want = [
    ("09 burden split", "09_burden_split.sql"),
    ("10 out of scope reasons", "10_out_of_scope_reasons.sql"),
    ("11 call types", "11_call_types.sql"),
    ("12 long calls", "12_long_calls.sql"),
]

for label, fname in want:
    if label in existing:
        print(f"  have: {label}")
        continue
    src = (HERE / "cards" / fname).read_text()
    st, body = call("POST", "/cells", {
        "projectId": PROJECT, "cellType": "SQL", "label": label,
        "contents": {"sqlCell": {"source": src}}, "dataConnectionId": CONN})
    print(f"  create {label} -> {st} conn={body.get('dataConnectionId') if isinstance(body, dict) else body}")
    time.sleep(1)

if "Notes" not in existing:
    st, body = call("POST", "/cells", {
        "projectId": PROJECT, "cellType": "MARKDOWN", "label": "Notes",
        "contents": {"markdownCell": {"source": FOOTER}}})
    print(f"  create Notes -> {st}")
