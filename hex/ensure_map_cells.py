"""Place the map query and its Plotly card, then preview the figure as a PNG.

The figure is generated here exactly as the Hex cell will generate it, so the preview is the
thing that will ship rather than an approximation.
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
CARD = Path("/home/jimbotron/Documents/SVFD_Capstone/hex/cards/15_map_points.sql")
STATIONS = Path("/home/jimbotron/Documents/SVFD_Capstone/hex/cards/16_station_locations.sql")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
TOKEN = [l.split("=", 1)[1].strip().strip('"').strip("'")
         for l in Path(os.path.expanduser("~/.hermes/.env")).read_text().splitlines()
         if l.startswith("HEX_API_TOKEN=")][0]

MAP_CODE = '''import plotly.graph_objects as go

# Crest palette: department red and gold on a dark base, so the map belongs to the app.
FAMILY_COLOURS = {
    "EMS": "#cc0000", "Fire": "#bf9000", "Service": "#8c8c8c",
    "Motor vehicle accident": "#2f6fa8", "Wildland": "#6b8f3a",
    "Backcountry rescue": "#a05fa8", "Untyped": "#5a5a5a",
}

df = __DF__.copy()
df["family"] = df["call_family"].fillna("Untyped")
stations = __STATIONS__.copy()

# Plotly renamed Scattermapbox to Scattermap in v7; support whichever the kernel has.
modern = not hasattr(go, "Scattermapbox")
Trace = go.Scattermap if modern else go.Scattermapbox

layers = []
for family, colour in FAMILY_COLOURS.items():
    part = df[df["family"] == family]
    if part.empty:
        continue
    layers.append(Trace(
        lat=part["lat"], lon=part["lon"], mode="markers",
        marker=dict(size=(part["incidents"] * 3 + 6).tolist(), color=colour, opacity=0.6),
        name=f"{family} ({int(part['incidents'].sum()):,})",
        hovertemplate="%{text}<extra></extra>",
        text=[f"{int(n):,} call" + ("s" if n > 1 else "") for n in part["incidents"]],
    ))

# Stations sit above the density layer: a station is a place, not a volume.
layers.append(Trace(
    lat=stations["lat"], lon=stations["lon"], mode="markers+text",
    marker=dict(size=22, color="#ffffff", opacity=1.0,
                symbol="star" if not modern else None),
    text=stations["label"], textposition="top center",
    textfont=dict(size=11, color="#ffffff"),
    name="Fire stations",
    hovertemplate="%{text}<br>%{customdata}<extra></extra>",
    customdata=stations["basis"],
))

fig = go.Figure(layers)
view = dict(style="open-street-map", center=dict(lat=43.64, lon=-114.35), zoom=9.2)
fig.update_layout(
    **({"map": view} if modern else {"mapbox": view}),
    margin=dict(l=0, r=0, t=0, b=0),
    height=560,
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0, title=None),
    showlegend=True,
)
fig.show()
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

sql_label = "15 map points"
if sql_label in existing:
    st, _ = call("PATCH", f"/cells/{existing[sql_label]['id']}",
                 {"contents": {"sqlCell": {"source": CARD.read_text()}},
                  "dataConnectionId": CONN})
    print("update SQL cell ->", st)
else:
    st, body = call("POST", "/cells", {
        "projectId": PROJECT, "cellType": "SQL", "label": sql_label,
        "contents": {"sqlCell": {"source": CARD.read_text()}}})
    call("PATCH", f"/cells/{body['id']}",
         {"contents": {"sqlCell": {"source": CARD.read_text()}}, "dataConnectionId": CONN})
    print("create SQL cell ->", st)

station_label = "16 station locations"
if station_label in existing:
    st, _ = call("PATCH", f"/cells/{existing[station_label]['id']}",
                 {"contents": {"sqlCell": {"source": STATIONS.read_text()}},
                  "dataConnectionId": CONN})
    print("update stations SQL ->", st)
else:
    st, body = call("POST", "/cells", {
        "projectId": PROJECT, "cellType": "SQL", "label": station_label,
        "contents": {"sqlCell": {"source": STATIONS.read_text()}}})
    call("PATCH", f"/cells/{body['id']}",
         {"contents": {"sqlCell": {"source": STATIONS.read_text()}}, "dataConnectionId": CONN})
    print("create stations SQL ->", st)

export_req = urllib.request.Request(
    BASE + "/projects/export", data=json.dumps({"projectId": PROJECT}).encode(), method="POST",
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
             "Accept": "*/*", "User-Agent": UA})
with urllib.request.urlopen(export_req, timeout=60) as r:
    doc = yaml.safe_load(json.loads(r.read().decode())["content"])
var = next(c["config"]["resultVariableName"] for c in doc["cells"]
           if c.get("cellLabel") == sql_label)
station_var = next(c["config"]["resultVariableName"] for c in doc["cells"]
                   if c.get("cellLabel") == station_label)
print("dataframes:", var, "|", station_var)

code_label = "Call map"
src = MAP_CODE.replace("__DF__", var).replace("__STATIONS__", station_var)
if code_label in existing:
    st, _ = call("PATCH", f"/cells/{existing[code_label]['id']}",
                 {"contents": {"codeCell": {"source": src}}})
    print("update CODE cell ->", st)
else:
    st, body = call("POST", "/cells", {
        "projectId": PROJECT, "cellType": "CODE", "label": code_label,
        "contents": {"codeCell": {"source": src}}})
    print("create CODE cell ->", st)
