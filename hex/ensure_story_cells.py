"""Create the two story cells: the metrics query and the narrative that renders it.

The narrative is a Python cell, not static markdown: it reads the query's dataframe and writes
the sentences with the numbers in them, so a data refresh rewrites the prose instead of leaving
it contradicting the charts.
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://app.hex.tech/api/v1"
PROJECT = "01a1a804-99cb-7342-9ee2-847df32397d8"
CONN = "01a1a802-223c-758c-ada3-70be18d99378"
CARD = Path("/home/jimbotron/Documents/SVFD_Capstone/hex/cards/13_story_metrics.sql")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
TOKEN = [l.split("=", 1)[1].strip().strip('"').strip("'")
         for l in Path(os.path.expanduser("~/.hermes/.env")).read_text().splitlines()
         if l.startswith("HEX_API_TOKEN=")][0]

NARRATIVE = '''from IPython.display import Markdown, display

r = __DF__.iloc[0]

def n(v):          # 720.0 -> "720"; 2020.0 -> "2020", never "2,020"
    v = float(v)
    return f"{v:,.0f}" if v >= 10000 else f"{v:.0f}"

def m(v):          # 6.6666 -> "6.7"
    return f"{v:.1f}"

verdict = "More calls. Same speed." if r["demand_pct_change"] >= 0 else "Fewer calls. Same speed."

text = f"""# {verdict}

**Demand rose {n(r['demand_pct_change'])}% between {n(r['first_year'])} and {n(r['last_year'])} — from {n(r['demand_first'])} incidents to {n(r['demand_last'])} — and response did not slip.**

The 90th-percentile alarm-to-arrival time moved from {m(r['p90_first'])} minutes to {m(r['p90_last'])}; the median from {m(r['p50_first'])} to {m(r['p50_last'])}. Time on scene held at {m(r['scene_first'])} minutes median in {n(r['first_year'])} and {m(r['scene_last'])} in {n(r['last_year'])}.

The growth landed at Greenhorn. It was staffed {n(r['gh_staffed_pct_2025'])}% of days in 2025 and {n(r['gh_staffed_pct_2026'])}% so far in 2026, and its call rate has gone from {m(r['gh_calls_2025'] / r['gh_months_2025'])} a month to {m(r['gh_calls_2026'] / r['gh_months_2026'])} — {n(r['gh_calls_2026'])} calls in {n(r['gh_months_2026'])} months, against {n(r['gh_calls_2025'])} in {n(r['gh_months_2025'])}.

What these numbers do not say: whether that is demand moving or reporting catching up, and whether a sub-minute change in a percentile is anything but noise. Every figure above is as of {r['data_as_of']}.
"""

display(Markdown(text))
'''


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

sql_label = "13 story metrics"
if sql_label in existing:
    # Keep the query in step with the card file: a stale source here silently drops the columns
    # the narrative reads, and the code cell fails on a missing key.
    sql_cell = existing[sql_label]
    st, _ = call("PATCH", f"/cells/{sql_cell['id']}",
                 {"contents": {"sqlCell": {"source": CARD.read_text()}},
                  "dataConnectionId": CONN})
    print("update SQL cell ->", st, sql_cell["id"])
else:
    st, sql_cell = call("POST", "/cells", {
        "projectId": PROJECT, "cellType": "SQL", "label": sql_label,
        "contents": {"sqlCell": {"source": CARD.read_text()}},
        "dataConnectionId": CONN})
    print("create SQL cell ->", st, sql_cell.get("id") if isinstance(sql_cell, dict) else sql_cell)

# the CODE cell needs the dataframe variable name the SQL cell produces
_, detail = call("GET", f"/cells?projectId={PROJECT}&limit=100")
sql_cell = next(c for c in detail["values"] if c.get("label") == sql_label)
src = sql_cell.get("contents") or {}
print("sql cell contents keys:", list(src) if isinstance(src, dict) else type(src))

# resultVariableName is not exposed by /cells, so resolve it from the project export
export_req = urllib.request.Request(
    BASE + "/projects/export", data=json.dumps({"projectId": PROJECT}).encode(), method="POST",
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
             "Accept": "*/*", "User-Agent": UA})
with urllib.request.urlopen(export_req, timeout=60) as r:
    import yaml

    doc = yaml.safe_load(json.loads(r.read().decode())["content"])
var = next(c["config"]["resultVariableName"] for c in doc["cells"]
           if c.get("cellLabel") == sql_label)
print("dataframe variable:", var)

code_label = "Story narrative"
if code_label in existing:
    st, body = call("PATCH", f"/cells/{existing[code_label]['id']}",
                    {"contents": {"codeCell": {"source": NARRATIVE.replace("__DF__", var)}}})
    print("update CODE cell ->", st)
else:
    st, body = call("POST", "/cells", {
        "projectId": PROJECT, "cellType": "CODE", "label": code_label,
        "contents": {"codeCell": {"source": NARRATIVE.replace("__DF__", var)}}})
    print("create CODE cell ->", st, body.get("id") if isinstance(body, dict) else body)
