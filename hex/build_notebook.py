"""Build the Hex notebook from the card files through the Hex public API.

One card file in `hex/cards/` becomes one SQL cell, in name order, wired to a data
connection. Idempotent with --reset: it deletes every existing cell first, so re-running
rebuilds the notebook from the files on disk instead of drifting from them.

    export HEX_API_TOKEN=...    # ~/.hermes/.env, personal access token (Editor+)
    .venv/bin/python hex/build_notebook.py --project <projectId> --connection <connId> --reset

Ids are printed at the end; both can be listed with --list.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://app.hex.tech/api/v1"
HERE = Path(__file__).resolve().parent
# Cloudflare answers 403 (error 1010) to the default urllib agent, so this is required, not cosmetic.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")

INTRO = """# SVFD Operations

Operational data analysis for Sun Valley Fire Department, built on the warehouse in
`~/Documents/SVFD_Capstone`. This project runs against the **synthetic** build, so every
figure here is plausible but invented.

Rules the cards follow: response measures use in-scope incidents only (no mutual aid given,
not a wildland deployment, arrival inside 25 minutes); excluded work is reported as burden,
never folded into performance; and the newest months are incomplete because reports are
written after the fact, so every trend is read against the *data as of* date below."""


def token() -> str:
    for line in Path(os.path.expanduser("~/.hermes/.env")).read_text().splitlines():
        if line.startswith("HEX_API_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("HEX_API_TOKEN not found in ~/.hermes/.env")


TOKEN = token()


def call(method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": UA,
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                body = r.read().decode()
                return r.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:400]
            # The cell endpoints allow 30 requests/minute; a 429 here just means slow down.
            if e.code == 429 and attempt < 3:
                wait = int(e.headers.get("Retry-After") or 20)
                print(f"    429, waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            return e.code, detail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project")
    ap.add_argument("--connection", help="data connection id for every SQL cell")
    ap.add_argument("--reset", action="store_true", help="delete existing cells first")
    ap.add_argument("--list", action="store_true", help="list projects and connections, then exit")
    args = ap.parse_args()

    if args.list:
        for path in ("/projects?limit=20", "/data-connections?limit=20"):
            status, body = call("GET", path)
            print(f"{path} -> {status}")
            for it in (body or {}).get("values", []):
                print(f"   {it['id']}  {it.get('title') or it.get('name')!r}  {it.get('type','')}")
        return 0

    if not (args.project and args.connection):
        print("--project and --connection are required (use --list to find them)", file=sys.stderr)
        return 2

    if args.reset:
        status, body = call("GET", f"/cells?projectId={args.project}&limit=100")
        if status != 200:
            print(f"cannot list cells: {status} {body}", file=sys.stderr)
            return 1
        existing = (body or {}).get("values", [])
        for c in existing:
            dstatus, dbody = call("DELETE", f"/cells/{c['id']}")
            print(f"  deleted {c.get('cellType')} {c['id']} -> {dstatus}")
            if dstatus not in (200, 204):
                print(f"    {dbody}", file=sys.stderr)

    created = []

    status, body = call("POST", "/cells", {
        "projectId": args.project,
        "cellType": "MARKDOWN",
        "label": "Overview",
        "contents": {"markdownCell": {"source": INTRO}},
    })
    print(f"  markdown overview -> {status}")
    if status == 200:
        created.append(("Overview", "MARKDOWN", body.get("id")))
    else:
        print(f"    {body}", file=sys.stderr)

    for card in sorted((HERE / "cards").glob("*.sql")):
        label = card.stem.replace("_", " ")
        status, body = call("POST", "/cells", {
            "projectId": args.project,
            "cellType": "SQL",
            "label": label,
            "contents": {"sqlCell": {"source": card.read_text()}},
            "dataConnectionId": args.connection,
        })
        if status != 200:
            print(f"  {card.stem} -> {status} {body}", file=sys.stderr)
            continue
        cell_id = body.get("id")
        conn = body.get("dataConnectionId")
        # Some workspaces ignore dataConnectionId on create; set it explicitly if so.
        if conn != args.connection:
            ustatus, ubody = call("PATCH", f"/cells/{cell_id}",
                                  {"dataConnectionId": args.connection})
            if ustatus != 200:
                print(f"  {card.stem}: could not set connection -> {ustatus} {ubody}", file=sys.stderr)
            else:
                conn = ubody.get("dataConnectionId")
        ok = "ok" if conn == args.connection else "NO CONNECTION"
        print(f"  {card.stem:<28} -> {status}  conn={conn}  {ok}")
        created.append((label, "SQL", cell_id))
        time.sleep(0.15)  # 30 requests/minute on the cell endpoints

    print(f"\n{len(created)} cells created in project {args.project}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
