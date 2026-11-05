"""Create the cover block. The crest itself is a drag-and-drop step in the UI: the yaml format
can reference an external image URL but cannot carry a binary upload.
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://app.hex.tech/api/v1"
PROJECT = "01a1a804-99cb-7342-9ee2-847df32397d8"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
TOKEN = [l.split("=", 1)[1].strip().strip('"').strip("'")
         for l in Path(os.path.expanduser("~/.hermes/.env")).read_text().splitlines()
         if l.startswith("HEX_API_TOKEN=")][0]

COVER = """# Sun Valley Fire Department

### Operational dashboard — calls, response, staffing

*Synthetic build: every name, address and incident number on these tabs is invented. What is real
is the method — definitions, denominators and the reporting-lag caveat that every figure carries.*
"""


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

if "Cover" in existing:
    st, _ = call("PATCH", f"/cells/{existing['Cover']['id']}",
                 {"contents": {"markdownCell": {"source": COVER}}})
    print("update Cover ->", st)
else:
    st, body = call("POST", "/cells", {
        "projectId": PROJECT, "cellType": "MARKDOWN", "label": "Cover",
        "contents": {"markdownCell": {"source": COVER}}})
    print("create Cover ->", st, body.get("id") if isinstance(body, dict) else body)
