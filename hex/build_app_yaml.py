"""Build the Hex app layout offline and hand back a .hex.yaml to import.

Publishing a notebook does not put its cells on the app -- the exported file showed the app
carrying one markdown cell and nothing else, which is why the published view had no charts.
Hex has no API for the app layout, but it round-trips projects as `.hex.yaml`, so this script
exports the current project, adds chart cells and a laid-out tab, validates the result against
the published schema, and writes a file to import with History & Versions -> Import a version.

    .venv/bin/python hex/build_app_yaml.py --project <id> --connection <id>

The chart cells read the dataframe variable of the SQL card above them, so the SQL stays the
single definition of every number: no chart computes its own measure.
"""

from __future__ import annotations

import argparse
import json
import re
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "https://app.hex.tech/api/v1"
HERE = Path(__file__).resolve().parent
OUT = HERE / "project.hex.yaml"
SCHEMA_URL = "https://static.hex.site/hex-file-schema.json"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")

OVERVIEW = """### Operational data analysis for Sun Valley Fire Department

This is the **synthetic build**: every customer, address and incident number here is invented.

Response measures use in-scope incidents only (no mutual aid given, not a wildland deployment,
arrival inside 25 minutes). Excluded work is reported as unit-hours, never folded into
performance. The newest months are incomplete because reports are written after the fact, so
read every trend against the *data through* date beside the demand chart."""

RED = "#cc0000"
GOLD = "#bf9000"
NAVY = "#2f6fa8"

# EXPLORE cells require a full table display config even when they render a chart.
TEMPLATE: dict | None = None

TABLE_CONFIG = {
    "pageSize": 50, "height": None, "hideIcons": False, "defaultColumnWidth": None,
    "hideIndex": False, "defaultSortColumn": None, "defaultSortIndexColumn": None,
    "defaultSortDirection": "ASC", "conditionalFormatting": None, "calcs": None,
    "filters": None, "columnProperties": [],
}


def token() -> str:
    for line in Path(os.path.expanduser("~/.hermes/.env")).read_text().splitlines():
        if line.startswith("HEX_API_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("HEX_API_TOKEN not found in ~/.hermes/.env")


def export(project_id: str) -> dict:
    body = json.dumps({"projectId": project_id}).encode()
    req = urllib.request.Request(
        BASE + "/projects/export", data=body, method="POST",
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json",
                 "Accept": "*/*", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"export failed: {e.code} {e.read()[:200]}")


def new_id() -> str:
    return str(uuid.uuid4())


def explore_cell(label: str, df: str, x: str, x_type: str, ys: list[dict],
                 legend: str, color_by: str | None = None, cell_id: str | None = None,
                 x_title: str | None = None, y_title: str | None = None) -> dict:
    """One EXPLORE chart cell.

    EXPLORE, not CHARTV2: this workspace's importer rejects `CHARTV2` with
    "Invalid constant value", while EXPLORE cells already import cleanly from a v3 export.
    ys items are {col, kind, color, name}, one series each; color_by stacks/bars by a category.
    """
    dtype = {"number": ("INTEGER", "number"), "string": ("VARCHAR", "string"),
             "datetime": ("TIMESTAMP", "datetime")}[x_type]
    fields, series, groups = [], [], []
    base = {"grid": {"style": "solid"}, "ticks": {}}

    base_field = {
        "id": new_id(), "value": x, "channel": "base-axis", "dataType": dtype[0],
        "seriesId": new_id()[:8], "fieldType": "COLUMN", "queryPath": [],
        "scaleType": dtype[1], "axis": base,
    }
    if x_type == "datetime":
        # A real date lets Hex thin the tick labels and sort chronologically; a string of
        # "2026-01" values renders as 82 rotated labels.
        base_field["truncUnit"] = "month"
    if x_title:
        # Without this Hex titles the axis after the column and its truncation unit,
        # which produced "Month of month".
        base_field["title"] = x_title
    fields.append(base_field)
    for item in ys:
        sid = new_id()[:8]
        fields.append({
            "id": new_id(), "value": item["col"], "channel": "cross-axis",
            "dataType": "FLOAT", "seriesId": sid, "fieldType": "COLUMN", "queryPath": [],
            "scaleType": "number", "aggregation": "Sum", "axis": base,
        })
        s = {
            "id": sid, "type": item["kind"],
            "color": ({"source": "color"} if color_by
                      else {"source": "color", "staticValue": item["color"]}),
            "opacity": {"source": "opacity", "staticMode": True, "staticValue": 1},
            "tooltip": {"includeAuto": True},
        }
        if item.get("name"):
            s["name"] = item["name"]
        if y_title and len(ys) == 1:
            # One series: title the axis ourselves, otherwise it reads "Sum of <column>".
            fields[-1]["title"] = y_title
        elif y_title:
            # Several series share the axis, so Hex concatenates their titles; this replaces it.
            fields[-1]["axis"] = {**base, "multiFieldTitle": y_title}
        if item["kind"] == "bar":
            s["barGrouped"] = False
        else:
            s["linePoint"] = True
            s["lineWidth"] = {}
        series.append(s)
        groups.append(sid)

    if color_by:
        fields.append({
            "id": new_id(), "value": color_by, "channel": "color", "dataType": "VARCHAR",
            "seriesId": groups[0], "fieldType": "COLUMN", "queryPath": [],
            "scaleType": "string", "sort": {"mode": "value-ascending"},
        })

    import copy
    config = copy.deepcopy(TEMPLATE) if TEMPLATE else {
        "semanticProjectId": None,
        "displayTableConfig": TABLE_CONFIG,
        "spec": {"details": {"fields": [], "enabled": True,
                             "showAllBaseTableDetailFields": True},
                 "viewType": "visualization", "rowTotals": True,
                 "columnTotals": True, "visualizationType": "chart"},
    }
    config["semanticProjectId"] = None
    config["dataframe"] = df
    config["colorMappings"] = {}
    config["spec"]["fields"] = fields
    config["spec"]["chartConfig"] = {
        "series": series,
        "settings": {"legend": {"position": legend}},
        "orientation": "vertical",
        "seriesGroups": [groups],
    }
    return {
        "cellType": "EXPLORE",
        # Reuse the id of an existing chart with the same label, so a re-import updates it in
        # place instead of leaving the old one orphaned beside a new copy.
        "cellId": cell_id or new_id(),
        "cellLabel": label,
        "config": config,
    }


def input_cell(label: str, name: str, default: str, cell_id: str | None = None) -> dict:
    """A DATE input the SQL cards reference as {{name}}."""
    return {
        "cellType": "INPUT",
        "cellId": cell_id or new_id(),
        "cellLabel": label,
        # A DATE input's default is a DateOnly object. A bare string passes the schema but
        # never hydrates, and then every cell referencing {{name}} fails to compile with no
        # error text at all — Hex has nothing to report because the query never ran.
        "config": {"name": name, "inputType": "DATE", "outputType": "DATETIME",
                   "options": None, "defaultValue": {"dateString": default}},
    }


def metric_cell(title: str, df: str, column: str, out_var: str, decimals: int,
                cell_id: str | None = None) -> dict:
    """A METRIC tile. Display only: the number is a column of an existing SQL result, so a tile
    can never be computed differently from the card below it."""
    return {
        "cellType": "METRIC",
        "cellId": cell_id or new_id(),
        "cellLabel": title,
        "config": {
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
            # currency is a required field even for a plain NUMBER format
            "displayFormat": {"format": "NUMBER", "columnType": "NUMBER",
                               "currency": "USD", "showSeparators": True,
                               "numDecimalDigits": decimals,
                               "abbreviateLargeNumbers": False},
            "valueResultVariable": out_var,
            "comparisonResultVariable": out_var + "_comparison",
            "outputResult": False,
        },
    }


def element(cell_id: str, height=None) -> dict:
    return {"showSource": False, "hideOutput": False, "type": "CELL", "cellId": cell_id,
            "sharedFilterId": None, "height": height, "showLabel": True, "explorable": None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()

    import yaml

    payload = export(args.project)
    doc = yaml.safe_load(payload["content"])

    # The exporter returns the charts a previous run of this script added. Drop them and rebuild,
    # otherwise each import stacks another copy (26 cells instead of 18).
    global TEMPLATE
    existing_charts = {c.get("cellLabel"): c["cellId"] for c in doc["cells"]
                       if c.get("cellType") == "EXPLORE"}
    # Clone the shape of a chart the importer itself exported. The published JSON schema is
    # newer than this workspace's importer: a hand-built cell that passes the schema can still
    # be rejected on upload, and the error names the cell type rather than the missing key.
    for c in doc["cells"]:
        if c.get("cellType") == "EXPLORE":
            TEMPLATE = c["config"]
            break
    doc["cells"] = [c for c in doc["cells"] if c.get("cellType") != "EXPLORE"]

    sql = {c.get("cellLabel"): c for c in doc["cells"] if c.get("cellType") == "SQL"}

    # The card files are the single source of truth: push their SQL into the matching cells so
    # one import updates the queries and the charts laid over them in the same pass.
    card_for = {
        "00 data as of": "00_data_as_of", "01 demand monthly": "01_demand_monthly",
        "02 response percentiles": "02_response_percentiles",
        "03 time on scene": "03_time_on_scene",
        "04 unit hours burden": "04_unit_hours_burden",
        "05 unit coverage": "05_unit_coverage",
        "06 paramedic coverage": "06_paramedic_coverage",
        "07 call type mix": "07_call_type_mix",
        "08 station split": "08_station_split",
        "09 burden split": "09_burden_split",
        "10 out of scope reasons": "10_out_of_scope_reasons",
        "11 call types": "11_call_types",
        "12 long calls": "12_long_calls",
    }
    for label, stem in card_for.items():
        if label in sql:
            sql[label]["config"]["source"] = (HERE / "cards" / f"{stem}.sql").read_text()
    missing = [k for k in ("00 data as of", "01 demand monthly", "02 response percentiles",
                           "03 time on scene", "04 unit hours burden", "05 unit coverage",
                           "06 paramedic coverage", "07 call type mix", "08 station split")
               if k not in sql]
    if missing:
        raise SystemExit(f"SQL cells missing from the project: {missing}")

    def df_of(label: str) -> str:
        return sql[label]["config"]["resultVariableName"]

    charts = {
        "demand": explore_cell("Demand monthly", df_of("01 demand monthly"), "month", "datetime",
                               [{"col": "incidents", "kind": "line", "color": RED}], "none",
                               x_title="Month", y_title="Calls"),
        "response": explore_cell("Response time", df_of("02 response percentiles"), "month",
                                 "datetime",
                                 [{"col": "p50", "kind": "line", "color": RED, "name": "median"},
                                  {"col": "p90", "kind": "line", "color": GOLD, "name": "90th pct"}],
                                 "bottom", x_title="Month", y_title="Minutes"),
        "scene": explore_cell("Time on scene", df_of("03 time on scene"), "month", "datetime",
                              [{"col": "p50", "kind": "line", "color": RED, "name": "median"}],
                              "none", x_title="Month", y_title="Minutes"),
        "hours": explore_cell("Burden - hours out of scope (workload)",
                              df_of("04 unit hours burden"), "year", "string",
                              [{"col": "in scope hours", "kind": "bar", "color": RED,
                                "name": "in scope"},
                               {"col": "burden hours", "kind": "bar", "color": GOLD,
                                "name": "burden"}], "bottom",
                              x_title="Year", y_title="Crew hours"),
        "coverage": explore_cell("Paramedic coverage", df_of("06 paramedic coverage"), "month",
                                 "datetime",
                                 [{"col": "pct of days", "kind": "line", "color": NAVY,
                                   "name": "share of days"}], "none",
                                 x_title="Month", y_title="Share of days (%)"),
        "mix": explore_cell("Calls by type", df_of("07 call type mix"), "year", "string",
                            [{"col": "incidents", "kind": "bar", "color": RED, "name": "calls"}],
                            "right", color_by="call_family",
                            x_title="Year", y_title="Calls"),
        "station": explore_cell("Calls by station", df_of("08 station split"), "station", "string",
                                [{"col": "incidents", "kind": "bar", "color": RED, "name": "calls"}],
                                "none", x_title="Station", y_title="Calls"),
        "burden": explore_cell("Burden - calls out of scope", df_of("09 burden split"),
                               "year", "string",
                               [{"col": "in_scope", "kind": "bar", "color": RED,
                                 "name": "in scope"},
                                {"col": "out_of_scope", "kind": "bar", "color": GOLD,
                                 "name": "out of scope"}], "bottom",
                               x_title="Year", y_title="Incidents"),
        "longcalls": explore_cell("Long calls (4 h or more)", df_of("12 long calls"),
                                  "year", "string",
                                  [{"col": "pct_over_four_hours", "kind": "bar", "color": NAVY,
                                    "name": "share over four hours"}], "none",
                                  x_title="Year", y_title="Share over 4 h (%)"),
    }
    renamed = {"Burden - calls out of scope": "Burden split",
               # Deliberately NOT mapped: the unit-hours chart changed axis grain from month to
               # year, and re-importing that under its existing id comes back as
               # `Invalid constant value "EXPLORE"`. A fresh cell id sidesteps the merge; the
               # old chart stays orphaned in the notebook and drops out of the layout.
               "Long calls (4 h or more)": "Long calls"}
    existing_inputs = {c.get("cellLabel"): c["cellId"] for c in doc["cells"]
                       if c.get("cellType") == "INPUT"}
    # Drop the previous inputs and rebuild them from this file: the export parses their
    # defaultValue into a date object, which the schema rejects, and a second copy would
    # leave two cells bound to the same variable name.
    doc["cells"] = [c for c in doc["cells"] if c.get("cellType") != "INPUT"]
    date_inputs = [
        input_cell("From", "start_date", "2020-01-01", existing_inputs.get("From")),
        input_cell("To", "end_date", "2026-12-31", existing_inputs.get("To")),
    ]
    # Inputs go FIRST in the notebook, not last: Hex resolves variables in cell order, so a
    # card above its input sees "Undefined variable(s)" even though the input exists.
    doc["cells"] = date_inputs + doc["cells"]

    for chart in charts.values():
        reuse = existing_charts.get(renamed.get(chart["cellLabel"], chart["cellLabel"]))
        if reuse:
            chart["cellId"] = reuse
    doc["cells"].extend(charts.values())

    # The cover carries the crest as a data URI, so it lives in a file rather than being retyped
    # here; otherwise every import would strip the logo back off.
    cover_file = HERE / "cover.md"
    if cover_file.exists():
        next(c for c in doc["cells"] if c.get("cellLabel") == "Cover")["config"]["source"] = \
            cover_file.read_text()

    overview_cell = next(c for c in doc["cells"] if c.get("cellLabel") == "Overview")
    overview_cell["config"]["source"] = OVERVIEW
    overview = overview_cell["cellId"]
    as_of = sql["00 data as of"]["cellId"]
    notes_cell = next(c for c in doc["cells"] if c.get("cellLabel") == "Notes")

    # A three-column table with a row index reads as a database view. Hide the index and name
    # the columns the way a reader would say them.
    as_of_table = sql["00 data as of"]["config"]["tableDisplayConfig"]
    as_of_table.update({"hideIndex": True, "pageSize": 5, "hideIcons": True})
    as_of_table["columnProperties"] = [
        {"originalName": "data_as_of", "renameTo": "Data through", "size": 130,
         "wrapText": None, "displayFormat": None},
        {"originalName": "latest_year", "renameTo": "Year", "size": 70,
         "wrapText": None, "displayFormat": None},
        {"originalName": "incidents_on_hand", "renameTo": "Records", "size": 110,
         "wrapText": None, "displayFormat": None},
    ]

    kpi_df = sql["14 kpi strip"]["config"]["resultVariableName"]
    existing_metrics = {c.get("cellLabel"): c["cellId"] for c in doc["cells"]
                        if c.get("cellType") == "METRIC"}
    doc["cells"] = [c for c in doc["cells"] if c.get("cellType") != "METRIC"]
    tiles = [
        metric_cell("Calls", kpi_df, "incidents", "kpi_calls", 0,
                    existing_metrics.get("Calls")),
        metric_cell("Median response", kpi_df, "p50_min", "kpi_p50", 1,
                    existing_metrics.get("Median response")),
        metric_cell("90th percentile", kpi_df, "p90_min", "kpi_p90", 1,
                    existing_metrics.get("90th percentile")),
        metric_cell("In scope", kpi_df, "pct_in_scope", "kpi_in_scope", 1,
                    existing_metrics.get("In scope")),
    ]
    doc["cells"].extend(tiles)

    cover_cell = next(c["cellId"] for c in doc["cells"] if c.get("cellLabel") == "Cover")
    map_code = next(c["cellId"] for c in doc["cells"] if c.get("cellLabel") == "Call map")

    story_sql = next(c["cellId"] for c in doc["cells"]
                     if c.get("cellLabel") == "13 story metrics")
    story_code = next(c["cellId"] for c in doc["cells"]
                      if c.get("cellLabel") == "Story narrative")

    # Notebook order decides what is defined at run time: inputs first, then the story query,
    # then the narrative that reads it, then everything else.
    head_ids = ([cover_cell,
                 date_inputs[0]["cellId"], date_inputs[1]["cellId"],
                 sql["14 kpi strip"]["cellId"]]
                + [t["cellId"] for t in tiles]
                + [story_sql, story_code])
    by_id = {c["cellId"]: c for c in doc["cells"]}
    head = [by_id[i] for i in head_ids if i in by_id]
    tail = [c for c in doc["cells"] if c["cellId"] not in head_ids]
    doc["cells"] = head + tail

    def row(spans: list[tuple[int, int, str, int | None]]) -> dict:
        return {"columns": [
            {"start": s, "end": e, "elements": [element(cid, h)]} for s, e, cid, h in spans]}

    doc["appLayout"] = {
        "visibleMetadataFields": ["NAME", "DESCRIPTION", "AUTHOR", "LAST_EDITED", "LAST_RUN",
                                  "TOTAL_EDITED", "CATEGORIES", "STATUS", "TABLE_OF_CONTENTS"],
        "fullWidth": True,
        "tabs": [{
            "name": "The story",
            "rows": [
                row([(0, 120, cover_cell, None)]),
                # One generated paragraph does the arguing; the three charts underneath are the
                # evidence for it. The prose is a Python cell, so it quotes live numbers.
                row([(0, 120, story_code, 430)]),
                row([(0, 40, charts["demand"]["cellId"], 320),
                     (40, 80, charts["response"]["cellId"], 320),
                     (80, 120, charts["station"]["cellId"], 320)]),
            ],
        }, {
            "name": "Operations",
            "rows": [
                # Date range first: everything below it is scoped by these two inputs.
                row([(0, 30, date_inputs[0]["cellId"], 150),
                     (30, 60, date_inputs[1]["cellId"], 150)]),
                row([(0, 120, overview, None)]),
                # The strip answers "where do I stand for this range" before any chart.
                row([(0, 24, tiles[0]["cellId"], 150), (24, 48, tiles[1]["cellId"], 150),
                     (48, 72, tiles[2]["cellId"], 150), (72, 96, tiles[3]["cellId"], 150)]),
                row([(0, 30, as_of, 220), (30, 120, charts["demand"]["cellId"], 340)]),
                row([(0, 60, charts["response"]["cellId"], 340),
                     (60, 120, charts["scene"]["cellId"], 340)]),
                row([(0, 60, charts["coverage"]["cellId"], 340),
                     (60, 120, charts["mix"]["cellId"], 340)]),
                row([(0, 60, charts["station"]["cellId"], 340),
                     (60, 120, charts["longcalls"]["cellId"], 340)]),
                # The one visually-led card in the app. Street-level points, not addresses.
                row([(0, 120, map_code, 560)]),
                # The two burden measures side by side: calls out of scope, then hours out of
                # scope. Same baskets, different question, so they belong next to each other.
                row([(0, 60, charts["burden"]["cellId"], 340),
                     (60, 120, charts["hours"]["cellId"], 340)]),
                row([(0, 60, sql["10 out of scope reasons"]["cellId"], 300),
                     (60, 120, sql["11 call types"]["cellId"], 300)]),
                row([(0, 120, notes_cell["cellId"], None)]),
            ],
        }],
    }
    # TOTAL_EDITED is not a real metadata field; drop anything the schema rejects later.
    doc["appLayout"]["visibleMetadataFields"] = [
        f for f in doc["appLayout"]["visibleMetadataFields"]
        if f in ("NAME", "DESCRIPTION", "AUTHOR", "LAST_EDITED", "LAST_RUN", "CATEGORIES",
                 "STATUS", "TABLE_OF_CONTENTS")
    ]

    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)
    Path(args.out).write_text(text)
    print(f"wrote {args.out} ({len(text)} chars, {len(doc['cells'])} cells, "
          f"{len(charts)} charts)")

    if not args.no_validate:
        try:
            import jsonschema
        except ImportError:
            print("jsonschema not installed; skipping schema validation", file=sys.stderr)
            return 0
        cache = Path("/home/jimbotron/.hermes/cache/scratch/hex-schema.json")
        if not cache.exists():
            with urllib.request.urlopen(SCHEMA_URL, timeout=60) as r:
                cache.write_bytes(r.read())
        schema = json.loads(cache.read_text())
        try:
            jsonschema.validate(instance=doc, schema=schema)
            print("schema validation: OK")
        except jsonschema.ValidationError as e:
            print(f"schema validation FAILED at {'/'.join(map(str, e.absolute_path))}: "
                  f"{e.message}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
