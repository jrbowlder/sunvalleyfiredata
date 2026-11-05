"""Run every Hex card against MotherDuck and print what comes back.

The cards are the deliverable; this proves they execute against the cloud warehouse before
anyone pastes them into Hex, and re-proves it after a refresh.

    export MOTHERDUCK_TOKEN=...      # or source it from ~/.hermes/.env
    .venv/bin/python hex/run_cards.py
    .venv/bin/python hex/run_cards.py --database svfd --context demo

A card that raises prints the error and the run exits non-zero, so a broken card cannot pass
as a silent empty result.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", default="svfd")
    ap.add_argument("--context", default="demo", help="schema the card SQL expects (demo)")
    ap.add_argument("--rows", type=int, default=3, help="rows to show from each end")
    args = ap.parse_args()

    if not os.environ.get("MOTHERDUCK_TOKEN"):
        print("MOTHERDUCK_TOKEN is not set", file=sys.stderr)
        return 2

    import duckdb

    cards = sorted((HERE / "cards").glob("*.sql"))
    if not cards:
        print("no cards found", file=sys.stderr)
        return 2

    con = duckdb.connect(f"md:{args.database}", read_only=True)
    con.execute(f"USE {args.context}")
    failures = 0
    try:
        for card in cards:
            # The cards are Hex SQL and carry {{start_date}} / {{end_date}} input variables.
            # Substitute literal dates so the same file can be run against MotherDuck.
            sql = (card.read_text()
                   .replace("{{start_date}}", "DATE '2020-01-01'")
                   .replace("{{end_date}}", "DATE '2099-12-31'"))
            print(f"\n=== {card.stem} ===")
            try:
                cur = con.execute(sql)
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
            except Exception as exc:  # noqa: BLE001 - report and keep going
                failures += 1
                print(f"  FAILED: {exc}")
                continue
            print(f"  {len(rows)} rows | {', '.join(cols)}")
            shown = rows if len(rows) <= args.rows * 2 else rows[: args.rows] + rows[-args.rows:]
            for row in shown:
                print("   ", "  ".join("NULL" if v is None else str(v) for v in row))
            if shown is not rows:
                print("    ...")
    finally:
        con.close()

    print(f"\n{len(cards) - failures}/{len(cards)} cards ran")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
