"""Create the KPI strip: one query, four metric tiles.

Tiles read columns from a single SQL result so they cannot disagree with the charts below them.
Metric cells are display-only; every number still comes from SQL.
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import yaml

BASE = "https://app.hex.tech/api/v1"
PROJECT = "01a1a804-99cb-7342-9ee2-847df32397d8"
CONN = "01a1a802-223c-758c-ada3-70be18d99378"
CARD = Path("/home/jimbotron/Documents/SVFD_Capstone/hex/cards/14_kpi_strip.sql")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
TOKEN = [l.split("=", 1)[1].strip().strip('"').strip("'")
         for l in Path(os.path.expanduser("~/.hermes/.env")).read_text().splitlines()
         if l.startswith("HEX_API_TOKEN=")][0]

KPIS = [
    ("Calls", "incidents", "kpi_calls", 0, "Calls in the selected period."),
    ("Median response", "p50_min", "kpi_p50", 1, "Alarm to first arrival, in-scope incidents, nearest-rank."),
    ("90th percentile", "p90_min", "kpi_p90", 1, "Alarm to first arrival, in-scope incidents, nearest-rank."),
    ("In scope", "pct_in_scope", "kpi_in_scope", 1, "Share of calls inside the performance basket."),
]


def call(method, path, payload=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                 "Accept": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


_, listed = call("GET", f"/cells?projectId={PROJECT}&limit=100")
existing = {c.get("label"): c for c in listed["values"]}

label = "14 kpi strip"
if label in existing:
    st, _ = call("PATCH", f"/cells/{existing[label]['id']}",
                 {"contents": {"sqlCell": {"source": CARD.read_text()}},
                  "dataConnectionId": CONN})
    print("update SQL cell ->", st)
else:
    st, body = call("POST", "/cells", {
        "projectId": PROJECT, "cellType": "SQL", "label": label,
        "contents": {"sqlCell": {"source": CARD.read_text()}}})
    print("create SQL cell ->", st)
    # POST drops dataConnectionId; set it explicitly or the cell cannot run
    st2, _ = call("PATCH", f"/cells/{body['id']}",
                  {"contents": {"sqlCell": {"source": CARD.read_text()}},
                   "dataConnectionId": CONN})
    print("  connection ->", st2)

export_req = urllib.request.Request(
    BASE + "/projects/export", data=json.dumps({"projectId": PROJECT}).encode(), method="POST",
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
             "Accept": "*/*", "User-Agent": UA})
with urllib.request.urlopen(export_req, timeout=60) as r:
    doc = yaml.safe_load(json.loads(r.read().decode())["content"])
df = next(c["config"]["resultVariableName"] for c in doc["cells"]
          if c.get("cellLabel") == label)
print("dataframe:", df)

for title, column, out_var, decimals, desc in KPIS:
    config = {
        "title": title,
        "valueVariableName": df,
        "valueColumn": column,
        "valueAggregate": "Sum",
        "valueRowIndex": None,
        "showComparison": False,
        "comparisonType": "VALUE",
        "comparisonVariableName": None,
        "comparisonColumn": None,
        "comparisonRowIndex": None,
        "comparisonAggregate": None,
        "comparisonLabel": None,
        "comparisonFormat": None,
        "displayFormat": {
            "format": "NUMBER",
            "columnType": "NUMBER",
            "showSeparators": True,
            "numDecimalDigits": decimals,
            "abbreviateLargeNumbers": False,
        },
        "valueResultVariable": out_var,
        "comparisonResultVariable": out_var + "_comparison",
        "outputResult": False,
    }
    if title in existing:
        st, _ = call("PATCH", f"/cells/{existing[title]['id']}", {"config": config})
        print(f"  {title:<18} config -> {st}")
    else:
        st, body = call("POST", "/cells", {
            "projectId": PROJECT, "cellType": "METRIC", "label": title,
            "contents": {}, "config": config})
        print(f"  {title:<18} create -> {st}")
