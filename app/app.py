#!/usr/bin/env python3
"""SVFD / NBCFD operations dashboard.

Reads warehouse.db read-only and applies the analyst decisions recorded in
docs/SCHEMA.md - two baskets (performance vs burden), analysis window from 2020,
performance measures on in-scope incidents only, and an explicit cut-off for the
incomplete reporting tail.

Run:  .venv/bin/streamlit run app/app.py
Set SVFD_DB to read a different build (e.g. data/synthetic/warehouse.db for the
public demo, which carries no real department records).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.abspath(os.environ.get("SVFD_DB") or os.path.join(ROOT, "warehouse.db"))
START = date(2020, 1, 1)
LAG_MONTHS = 3          # trailing months treated as incomplete (reporting lag)
RESPONSE_CAP = 25       # minutes - NFPA-style in-scope cap for arrival
SCENE_CAP = 240         # minutes - 4 h cap for time on scene
STATIONS = {"ELK": "Elkhorn (HQ)", "GH": "Greenhorn", "GB": "Griffin Butte"}

st.set_page_config(page_title="SVFD Operations", page_icon="🚒", layout="wide")


# --------------------------------------------------------------------------- data
# Cache keys include the database's size and mtime: rebuilding warehouse.db while the
# app is running must not keep serving yesterday's numbers out of the cache.
DB_SIG = ((os.path.getsize(DB), os.path.getmtime(DB)) if os.path.exists(DB) else (0, 0.0))


def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


@st.cache_data(show_spinner=False)
def load_incidents(db_sig=DB_SIG) -> pd.DataFrame:
    df = query("""
        SELECT incident_uid, incident_dt, year, station_code, org_code, org_source,
               incident_class, incident_subtype, type_desc, mutual_aid_given, schema_era,
               street_name, geo_lat, geo_lon, geo_source,
               alarm_to_arrival_min, alarm_to_cleared_min, is_actual
        FROM fact_incident
    """)
    df["date"] = pd.to_datetime(df["incident_dt"], errors="coerce")
    df = df[df["date"].notna()]
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
    df["mutual_aid_given"] = df["mutual_aid_given"].fillna("")
    return df


@st.cache_data(show_spinner=False)
def load_units(db_sig=DB_SIG) -> pd.DataFrame:
    # incident numbers repeat across years, so a plain key join duplicates unit rows.
    # Match to exactly one incident, preferring the one whose year agrees with the
    # unit's own dispatch timestamp.
    df = query("""
        WITH m AS (
          SELECT u.unit_row_id, u.apparatus_code, u.arrived_to_cleared_min, u.unit_hours,
                 i.incident_uid, i.year, i.incident_dt, i.station_code, i.org_code,
                 ROW_NUMBER() OVER (
                   PARTITION BY u.unit_row_id
                   ORDER BY CASE WHEN u.dispatch_dt IS NOT NULL
                                  AND i.year = CAST(substr(u.dispatch_dt,1,4) AS INTEGER)
                                 THEN 0 ELSE 1 END,
                            i.incident_uid) AS rn
          FROM fact_unit_response u
          JOIN fact_incident i ON i.incident_key = u.incident_key
        )
        SELECT incident_uid, apparatus_code, arrived_to_cleared_min, unit_hours,
               year, incident_dt, station_code, org_code
        FROM m WHERE rn = 1
    """)
    df["date"] = pd.to_datetime(df["incident_dt"], errors="coerce")
    return df


@st.cache_data(show_spinner=False)
def load_station_days(db_sig=DB_SIG) -> pd.DataFrame:
    df = query("""
        SELECT work_date, station_code, year, is_actual, headcount, has_paramedic
        FROM v_station_day
    """)
    df["date"] = pd.to_datetime(df["work_date"], errors="coerce")
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
    return df


inc = load_incidents(DB_SIG)
units = load_units(DB_SIG)
sdays = load_station_days(DB_SIG)

actual = inc[inc["is_actual"] == 1]
DATA_AS_OF = actual["date"].max().date() if not actual.empty else date.today()
FIRST = max(START, actual["date"].min().date())
CUTOFF = (pd.Timestamp(DATA_AS_OF) - pd.DateOffset(months=LAG_MONTHS)).date()


def in_scope(df: pd.DataFrame) -> pd.DataFrame:
    """Performance basket: no mutual aid given, not wildland, arrival within cap."""
    return df[
        (df["mutual_aid_given"] == "")
        & (df["incident_class"] != "Wildland")
        & (df["alarm_to_arrival_min"].isna() | (df["alarm_to_arrival_min"] <= RESPONSE_CAP))
    ]


def render(fig: go.Figure, legend: bool = True) -> None:
    """Draw a chart with the toolbars off and any legend parked under the plot so it
    cannot collide with the title."""
    has_legend = (legend and len(fig.data) > 1
                  and fig.layout.showlegend is not False)
    fig.update_layout(margin=dict(l=0, r=0, t=46, b=28 if has_legend else 0))
    if has_legend:
        fig.update_layout(legend=dict(orientation="h", yanchor="top", y=-0.16, x=0),
                          showlegend=True)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


# ------------------------------------------------------------------------ sidebar
st.sidebar.header("Filters")

window = st.sidebar.date_input(
    "Date range",
    value=(FIRST, DATA_AS_OF),
    min_value=FIRST,
    max_value=DATA_AS_OF,
    help="Incidents are counted by incident date; planning records come from the "
         "staffing calendar in whatever range you pick.",
)
start, end = (window if isinstance(window, tuple) and len(window) == 2 else (FIRST, DATA_AS_OF))

station_sel = st.sidebar.multiselect(
    "Station", list(STATIONS), default=list(STATIONS),
    format_func=lambda c: f"{STATIONS[c]} ({c})",
)
org_sel = st.sidebar.multiselect(
    "Jurisdiction", ["SVFD", "NBCFD", "unresolved"], default=["SVFD", "NBCFD", "unresolved"],
    help="Only resolvable from 2026 - the NFIRS era carries no reliable jurisdiction flag.",
)
family_sel = st.sidebar.multiselect(
    "Call type", ["Fire", "Wildland", "EMS", "Motor vehicle accident",
                  "Backcountry rescue", "Service", "Untyped"],
    help="Families from etl/taxonomy.py. 'Untyped' is the NERIS era, whose export "
         "carries no incident type at all.",
)

st.sidebar.divider()
include_unresolved_station = st.sidebar.checkbox(
    "Include incidents with no station on the report", value=True,
    help="43% of incidents carry no primary station. Unticking this shows only "
         "station-attributed work, which is a subset.",
)

st.sidebar.caption(
    f"Warehouse as of **{DATA_AS_OF:%d %b %Y}**. "
    f"Months after **{CUTOFF:%b %Y}** are shaded as incomplete."
)


# ------------------------------------------------------------------------ filtering
def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    m = (df["date"].dt.date >= start) & (df["date"].dt.date <= end)
    if station_sel and len(station_sel) < len(STATIONS):
        m &= df["station_code"].isin(station_sel)
        if not include_unresolved_station:
            m &= df["station_code"].notna()
    elif not include_unresolved_station:
        m &= df["station_code"].notna()
    if org_sel and len(org_sel) < 3:
        key = df["org_code"].fillna("unresolved")
        m &= key.isin(org_sel)
    if family_sel:
        m &= df["incident_class"].isin(family_sel)
    return df[m]


sel = apply_filters(actual)
perf = in_scope(sel)
days = max((end - start).days + 1, 1)


def fmt1(v) -> str:
    """One decimal, formatted in Python so the dashboard cannot disagree with the
    printed analysis scripts on a rounding half (numpy rounds 6.65 down, Python up)."""
    return "—" if v is None or pd.isna(v) else f"{v:.1f}"


def pct(series: pd.Series, q: float) -> float | None:
    """Nearest-rank percentile - the same convention analysis/01 and analysis/02 use, so
    the dashboard and the printed reports cannot disagree by an interpolation hair."""
    v = series.dropna().sort_values().to_numpy()
    if len(v) == 0:
        return None
    return float(v[min(len(v) - 1, int(len(v) * q))])


def fmt(v: float | None, suffix: str = " min") -> str:
    return "—" if v is None else f"{v:.1f}{suffix}"


# ----------------------------------------------------------------------- headline
st.title("Sun Valley Fire Department — operations")
st.caption(
    "Incident demand, response performance and station coverage in one view. "
    "Combined SVFD + NBCFD unless filtered."
)

c = st.columns(5)
c[0].metric("Incidents", f"{len(sel):,}")
c[1].metric("In-scope (performance basket)", f"{len(perf):,}")
SCOPE_HELP = ("In-scope incidents only: no mutual aid given, not a wildland deployment, "
              "arrival within 25 minutes. Excluded work is reported as burden, not as "
              "response performance.")
c[2].metric("Response p50 (in-scope)", fmt(pct(perf["alarm_to_arrival_min"], .5)),
            help=SCOPE_HELP)
c[3].metric("Response p90 (in-scope)", fmt(pct(perf["alarm_to_arrival_min"], .9)),
            help=SCOPE_HELP)
scene = units[(units["date"].dt.date >= start) & (units["date"].dt.date <= end)
              & (units["arrived_to_cleared_min"].between(0, SCENE_CAP))]
c[4].metric("Time on scene p50", fmt(pct(scene["arrived_to_cleared_min"], .5)))
st.caption(
    f"{len(sel):,} incidents over {days:,} days — {len(sel)/days:.2f} calls/day. "
    f"Response percentiles cover the {len(perf):,} in-scope incidents "
    f"({len(perf)/max(len(sel),1)*100:.0f}% of the total); the rest is counted as burden."
)

if len(sel) == 0:
    st.warning("No incidents in this range and filter combination.")
    st.stop()

tabs = st.tabs(["Demand", "Response", "Stations", "Map", "Coverage", "Burden"])

# -------------------------------------------------------------------- demand trend
with tabs[0]:
    monthly = sel.groupby("month").size().reset_index(name="incidents")
    annual = sel.groupby("year").size().reset_index(name="incidents")

    fig = go.Figure()
    fig.add_bar(x=monthly["month"], y=monthly["incidents"], name="Incidents",
                marker_color="#2f7ed8")
    fig.add_vrect(x0=pd.Timestamp(CUTOFF), x1=pd.Timestamp(end),
                  fillcolor="rgba(200,200,200,0.22)", line_width=0,
                  annotation_text="reports still\noutstanding",
                  annotation_position="top left",
                  annotation_font_size=10)
    fig.update_yaxes(range=[0, max(monthly["incidents"].max() * 1.22, 1)])
    fig.update_layout(height=340, yaxis_title="incidents / month", xaxis_title=None,
                      showlegend=False,
                      title=f"Monthly demand — data as of {DATA_AS_OF:%d %b %Y}")
    render(fig)

    left, right = st.columns([3, 2])
    with left:
        annual["partial"] = annual["year"] == DATA_AS_OF.year
        figy = px.bar(annual, x="year", y="incidents", text="incidents")
        figy.update_traces(
            marker_color=["#7aa9de" if p else "#2f7ed8" for p in annual["partial"]],
            textposition="outside")
        figy.update_layout(height=300, margin=dict(l=0, r=0, t=24, b=0),
                           title=f"Incidents by year (paler bar = partial year, "
                                 f"to {DATA_AS_OF:%b %d})",
                           xaxis_title=None, yaxis_title=None)
        render(figy)
    with right:
        fam = (sel.groupby("incident_class").size()
               .reset_index(name="incidents").sort_values("incidents"))
        figc = px.bar(fam, x="incidents", y="incident_class", orientation="h",
                      text=fam["incidents"].map("{:,}".format))
        figc.update_traces(marker_color="#4c9a6a", textposition="outside")
        figc.update_xaxes(range=[0, max(fam["incidents"].max() * 1.2, 1)])
        figc.update_layout(height=300, title="Call-type families", xaxis_title=None,
                           yaxis_title=None)
        render(figc)

    st.markdown("##### Call types — all incidents, not the in-scope basket")
    sub = (sel.groupby(["incident_class", "incident_subtype"]).size()
           .reset_index(name="incidents"))
    tree = px.treemap(sub, path=["incident_class", "incident_subtype"],
                      values="incidents", color="incident_class",
                      color_discrete_sequence=px.colors.qualitative.Set2)
    tree.update_traces(
        textinfo="label+value", textfont=dict(size=14),
        marker=dict(line=dict(color="rgba(25,25,25,0.55)", width=1)),
        root_color="rgba(0,0,0,0)")
    tree.update_layout(height=430, margin=dict(l=0, r=0, t=8, b=0))
    st.plotly_chart(tree, width="stretch", config={"displayModeBar": False})

    total = max(len(sel), 1)
    sub["share"] = (sub["incidents"] / total * 100).map(lambda v: f"{v:.1f}%")
    st.dataframe(
        sub.sort_values(["incident_class", "incidents"], ascending=[True, False])
           .rename(columns={"incident_class": "family",
                            "incident_subtype": "subtype",
                            "incidents": "incidents",
                            "share": "share of range"}),
        hide_index=True, width="stretch", height=320)
    st.caption(
        f"Counts here cover all {len(sel):,} incidents in the selected range and filters, "
        f"before the in-scope basket is applied (that basket is {len(perf):,}). "
        "Families and subtypes come from the NFIRS type code (see `etl/taxonomy.py`); "
        "the description is only a fallback, because keyword matching had been filing "
        "'Cooking fire, confined to container' as a backcountry rescue. Mutual aid is a "
        "flag, not a type of call — a mutual-aid structure fire counts as a structure "
        "fire. **Untyped** means no incident type is recorded anywhere: the NERIS export "
        "carries the constant string 'Fire NERIS' in its type column, so those incidents "
        "are typed from Elite's own NERIS incident list instead. What remains is about "
        "1.5% of incidents, kept visible rather than folded into a real family."
    )

    st.caption(
        "The bar after the shaded line is under-counted: department reports are written "
        "after the fact and some stay outstanding for months. Read the tail as reporting "
        "lag, never as a fall in demand."
    )

# ------------------------------------------------------------------ response times
with tabs[1]:
    st.caption(
        "Response times below use the in-scope basket — no mutual aid given, not a "
        "wildland deployment, arrival within 25 minutes. Time on scene uses the unit "
        "grain with a 4 h cap."
    )
    r = perf.dropna(subset=["alarm_to_arrival_min"])
    if r.empty:
        st.info("No timed in-scope incidents in this range.")
    else:
        band = (r.groupby("month")["alarm_to_arrival_min"]
                .agg(p50=lambda s: pct(s, .5), p90=lambda s: pct(s, .9),
                     n="size").reset_index())
        band = band[band["n"] >= 3]
        fig = go.Figure()
        fig.add_scatter(x=band["month"], y=band["p90"], name="p90 (9 in 10)",
                        mode="lines+markers", line=dict(color="#c0392b", width=2))
        fig.add_scatter(x=band["month"], y=band["p50"], name="p50 (typical)",
                        mode="lines+markers", line=dict(color="#2f7ed8", width=2))
        fig.update_layout(height=340, margin=dict(l=0, r=0, t=24, b=0),
                          yaxis_title="minutes alarm → arrival", xaxis_title=None,
                          legend=dict(orientation="h", y=1.12))
        render(fig)

        left, right = st.columns(2)
        with left:
            hist = px.histogram(r, x="alarm_to_arrival_min", nbins=40)
            hist.update_traces(marker_color="#2f7ed8")
            hist.add_vline(x=RESPONSE_CAP, line_dash="dash", line_color="#c0392b",
                           annotation_text=f"{RESPONSE_CAP} min cap")
            hist.update_layout(height=300, margin=dict(l=0, r=0, t=24, b=0),
                               title=f"Distribution — n={len(r):,}",
                               xaxis_title="minutes", yaxis_title="incidents")
            render(hist)
        with right:
            yearly = (r.groupby("year")["alarm_to_arrival_min"]
                      .agg(p50=lambda s: pct(s, .5), p90=lambda s: pct(s, .9),
                           n="size").reset_index())
            fig2 = px.bar(yearly.melt(id_vars=["year", "n"], value_vars=["p50", "p90"],
                                      var_name="percentile", value_name="minutes"),
                          x="year", y="minutes", color="percentile", barmode="group",
                          text="minutes", hover_data=["n"])
            fig2.update_traces(texttemplate="%{text:.1f}", textposition="outside")
            fig2.update_layout(height=300, title="Percentiles by year",
                               yaxis_title="minutes", xaxis_title=None,
                               legend_title=None)
            render(fig2)

        tbl = (r.groupby("year")["alarm_to_arrival_min"]
               .agg(n="size", p50=lambda s: pct(s, .5), p90=lambda s: pct(s, .9),
                    p95=lambda s: pct(s, .95), worst="max").reset_index())
        for c_ in ("p50", "p90", "p95", "worst"):
            tbl[c_] = tbl[c_].map(fmt1)
        st.dataframe(tbl, hide_index=True, width="stretch")

    std = units[(units["date"].dt.date >= start) & (units["date"].dt.date <= end)]
    std = std[std["arrived_to_cleared_min"].between(0, SCENE_CAP)]
    if not std.empty:
        sband = (std.groupby([std["date"].dt.to_period("M").dt.to_timestamp()])
                 ["arrived_to_cleared_min"]
                 .agg(p50=lambda s: pct(s, .5), n="size").reset_index()
                 .rename(columns={"date": "month"}))
        sband = sband[sband["n"] >= 3]
        figs = px.line(sband, x="month", y="p50", markers=True)
        figs.update_traces(line_color="#4c9a6a")
        figs.update_layout(height=260, margin=dict(l=0, r=0, t=24, b=0),
                           title="Time on scene — unit grain, arrived → cleared (p50)",
                           yaxis_title="minutes", xaxis_title=None)
        render(figs)
        st.caption(
            "Unit-level records stop at the NFIRS era — only 32 of 693 incidents in 2026 "
            "carry apparatus rows. Treat 2026 time-on-scene as thin."
        )

# --------------------------------------------------------------------- by station
with tabs[2]:
    stn = sel.dropna(subset=["station_code"]).copy()
    if stn.empty:
        st.info("No station-attributed incidents in this range.")
    else:
        # volume is all work; the percentiles are the same in-scope basket as the KPIs,
        # so a station's p90 cannot be inflated by mutual-aid and wildland runs
        agg = stn.groupby("station_code").agg(calls=("incident_uid", "size")).reset_index()
        perf_stn = (perf.dropna(subset=["station_code", "alarm_to_arrival_min"])
                    .groupby("station_code")["alarm_to_arrival_min"]
                    .agg(timed="size", p50=lambda s: pct(s, .5),
                         p90=lambda s: pct(s, .9)).reset_index())
        agg = agg.merge(perf_stn, on="station_code", how="left")
        agg["station"] = agg["station_code"].map(STATIONS).fillna(agg["station_code"])
        left, right = st.columns(2)
        with left:
            figb = px.bar(agg.sort_values("calls"), x="calls", y="station",
                          orientation="h", text="calls")
            figb.update_traces(marker_color="#2f7ed8", textposition="outside")
            figb.update_layout(height=280, margin=dict(l=0, r=0, t=24, b=0),
                              title="Call volume", xaxis_title=None, yaxis_title=None)
            render(figb)
        with right:
            figr = px.bar(agg.melt(id_vars=["station", "calls", "timed"],
                                   value_vars=["p50", "p90"],
                                   var_name="percentile", value_name="minutes"),
                          x="station", y="minutes", color="percentile", barmode="group",
                          text="minutes", hover_data=["calls", "timed"])
            figr.update_traces(texttemplate="%{text:.1f}", textposition="outside")
            figr.update_layout(height=280, title="Response time by station",
                               yaxis_title="minutes", xaxis_title=None,
                               legend_title=None)
            render(figr)

        shown = agg[["station", "calls", "timed", "p50", "p90"]].copy()
        for c_ in ("p50", "p90"):
            shown[c_] = shown[c_].map(fmt1)
        st.dataframe(shown, hide_index=True, width="stretch")
        st.caption(
            "Volume counts all work at the station; the percentiles use the in-scope "
            "basket, so they are comparable with the KPI row above. 'timed' is how many "
            "incidents had a usable arrival time."
        )

        # staffed vs unstaffed, joining the calendar
        s = sdays[(sdays["date"].dt.date >= start) & (sdays["date"].dt.date <= end)
                  & (sdays["is_actual"] == 1)][["date", "station_code", "headcount"]]
        stn["day"] = stn["date"].dt.normalize()
        joined = stn.merge(s, left_on=["day", "station_code"],
                           right_on=["date", "station_code"],
                           how="left", suffixes=("", "_cal"))
        joined = joined[joined["incident_uid"].isin(perf["incident_uid"])]
        joined["state"] = joined["headcount"].fillna(0).apply(
            lambda h: "staffed" if h > 0 else "not staffed")
        cmp = (joined.dropna(subset=["alarm_to_arrival_min"])
               .groupby(["station_code", "state"])["alarm_to_arrival_min"]
               .agg(n="size", p50=lambda x: pct(x, .5),
                    p90=lambda x: pct(x, .9)).reset_index())
        if not cmp.empty:
            cmp["station"] = cmp["station_code"].map(STATIONS).fillna(cmp["station_code"])
            figcmp = px.bar(cmp, x="station", y="p50", color="state", barmode="group",
                            text=cmp["p50"].round(1), hover_data=["n", "p90"])
            figcmp.update_layout(height=300, margin=dict(l=0, r=0, t=24, b=0),
                                 title="Response time by station and staffing state "
                                       "(2025+, where a calendar exists)",
                                 yaxis_title="p50 minutes", xaxis_title=None,
                                 legend_title=None, legend=dict(orientation="h", y=1.15))
            render(figcmp)
            st.caption(
                "Staffing data starts 2025-01-01, so this comparison covers only the "
                "recent window. 'Not staffed' includes days with no calendar row at all."
            )
        st.caption("Griffin Butte is unstaffed — its handful of records are reporting "
                   "locations, not workload signals.")

# --------------------------------------------------------------------------- map
with tabs[3]:
    st.caption(
        "Incident locations, geocoded offline against an OpenStreetMap street reference — "
        "no address leaves this machine. Precision is stated per point: a *reported* "
        "coordinate is one a crew actually recorded, a *street* point is the middle of the "
        "matched street, and streets longer than 2 km are marked broad, because the "
        "centroid of a valley-long highway means \"somewhere on that road\" and nothing "
        "more. NERIS-era addresses come from Elite's NERIS incident list, since that era's "
        "CSV export carries no address. Unplaced incidents are those with no address at "
        "all, or a private development name OpenStreetMap does not carry."
    )
    geo = sel.dropna(subset=["geo_lat", "geo_lon"])
    if geo.empty:
        st.info("No geocoded incidents in this range. Geocoding covers the NFIRS-era "
                "records with a street in the address.")
    else:
        k = st.columns(3)
        k[0].metric("Mapped incidents", f"{len(geo):,}")
        k[1].metric("Share of range", f"{len(geo) / max(len(sel), 1) * 100:.0f}%")
        k[2].metric("Crew-reported coordinates",
                    f"{int((geo['geo_source'] == 'reported_point').sum()):,}")

        precise = st.toggle("Crew-reported coordinates only", value=False,
                            help="Strictly the 248 incidents where the crew entered a "
                                 "point. Off by default so the whole shape of demand is "
                                 "visible.")
        plot = geo[geo["geo_source"] == "reported_point"] if precise else geo
        if plot.empty:
            st.info("No crew-reported coordinates in this range.")
        else:
            # Aggregate to a ~150 m grid so the colour bar carries an actual count.
            # Plotly's density trace normalises its surface to 0-1 in the browser, which
            # tells a reader nothing; a grid cell count is a number a chief can quote.
            cell = 0.0015
            grid = plot.assign(
                cell_lat=(plot["geo_lat"] / cell).round() * cell,
                cell_lon=(plot["geo_lon"] / cell).round() * cell,
            ).groupby(["cell_lat", "cell_lon"]).size().reset_index(name="incidents")
            fig = px.scatter_map(
                grid, lat="cell_lat", lon="cell_lon",
                size="incidents", color="incidents",
                size_max=26, zoom=10,
                center=dict(lat=float(plot["geo_lat"].median()),
                            lon=float(plot["geo_lon"].median())),
                map_style="carto-darkmatter", height=580,
                color_continuous_scale="Inferno",
                hover_data={"incidents": True, "cell_lat": ":.4f", "cell_lon": ":.4f"},
                labels={"incidents": "incidents per 150 m cell"},
            )
            fig.update_layout(margin=dict(l=0, r=0, t=0, b=0),
                              coloraxis_colorbar=dict(title="incidents<br>per cell",
                                                      thickness=12))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            if not precise:
                st.caption(
                    f"{len(grid):,} cells of ~150 m. A cell's count inherits the precision of "
                    "the points in it — street-centroid points bunch at the middle of a "
                    "street, so a long street reads as one hot cell rather than a spread."
                )

            st.markdown("##### Busiest streets in range")
            top = (geo.groupby("street_name").size().reset_index(name="incidents")
                   .sort_values("incidents", ascending=False).head(12))
            st.dataframe(top, hide_index=True, width="stretch")

    src = (sel["geo_source"].fillna("no coordinate").value_counts()
           .rename_axis("geo_source").reset_index(name="incidents"))
    st.caption("How every incident in range is positioned:")
    st.dataframe(src, hide_index=True, width="stretch")

# ----------------------------------------------------------------------- coverage
with tabs[4]:
    s = sdays[(sdays["date"].dt.date >= start) & (sdays["date"].dt.date <= end)
              & (sdays["is_actual"] == 1)]
    if s.empty:
        st.info(
            "No staffing calendar in this range. First Due schedule data starts "
            "December 2024, so staffed-day questions only work from 2025 onward."
        )
    else:
        # denominator is every date the calendar covers in the window, not just the
        # dates a station happens to appear on - otherwise an unstaffed station looks
        # fully covered by virtue of having few rows.
        window_days = s["date"].nunique()
        tot_days = s.groupby("station_code")["date"].nunique()
        staffed = s[s["headcount"] > 0].groupby("station_code")["date"].nunique()
        medic = (s[s["has_paramedic"] == 1].groupby("station_code")["date"].nunique())
        cov = pd.DataFrame({
            "station_code": tot_days.index,
            "days_with_rows": tot_days.values,
            "days_staffed": staffed.reindex(tot_days.index).fillna(0).astype(int).values,
            "days_with_paramedic": medic.reindex(tot_days.index).fillna(0).astype(int).values,
        })
        cov["staffed_pct"] = (cov["days_staffed"] / window_days * 100).round(1)
        cov["paramedic_pct"] = (cov["days_with_paramedic"] / window_days * 100).round(1)
        cov["station"] = cov["station_code"].map(STATIONS).fillna(cov["station_code"])

        figcov = go.Figure()
        figcov.add_bar(x=cov["station"], y=cov["staffed_pct"], name="any crew on duty",
                       marker_color="#2f7ed8")
        figcov.add_bar(x=cov["station"], y=cov["paramedic_pct"],
                       name="paramedic on duty", marker_color="#4c9a6a")
        figcov.update_layout(height=320, margin=dict(l=0, r=0, t=24, b=0), barmode="group",
                             title=f"Coverage of a 24 h day — % of the {window_days} "
                                   f"calendar days in range",
                             yaxis_title="% of days", xaxis_title=None,
                             legend=dict(orientation="h", y=1.15))
        render(figcov)
        st.dataframe(cov[["station", "days_with_rows", "days_staffed", "staffed_pct",
                          "days_with_paramedic", "paramedic_pct"]],
                     hide_index=True, width="stretch")

        mband = (s.groupby(["month", "station_code"])
                 .agg(days=("date", "nunique"),
                      staffed=("headcount", lambda h: int((h > 0).sum())),
                      medic=("has_paramedic", lambda h: int((h == 1).sum())))
                 .reset_index())
        mband["staffed_pct"] = mband["staffed"] / mband["days"] * 100
        mband["medic_pct"] = mband["medic"] / mband["days"] * 100
        mband["station"] = mband["station_code"].map(STATIONS).fillna(mband["station_code"])
        figm = px.line(mband, x="month", y="medic_pct", color="station", markers=True,
                        labels={"medic_pct": "% of days",
                                "station": "Station"})
        figm.update_layout(height=300, margin=dict(l=0, r=0, t=24, b=0),
                           title="Paramedic coverage by month",
                           legend_title=None, legend=dict(orientation="h", y=1.15))
        render(figm)

        st.caption(
            "Paramedic days use confirmed start dates only (certificate or department-"
            "confirmed). Three members carry an estimated start date and are excluded "
            "here, so this is the lower bound of clinical coverage."
        )

        # crew size against daily volume
        daily = s.merge(
            sel.assign(day=sel["date"].dt.normalize())
            .groupby("day").size().rename("calls").reset_index(),
            left_on="date", right_on="day", how="left",
        ).fillna({"calls": 0})
        daily["crew_bucket"] = daily["headcount"].clip(0, 4).astype(int)
        vol = (daily.groupby("crew_bucket")["calls"]
               .agg(station_days="size", mean_calls="mean").reset_index())
        vol["crew"] = vol["crew_bucket"].replace({0: "0 (unstaffed)"}).astype(str)
        figv = px.bar(vol, x="crew", y="mean_calls", text=vol["mean_calls"].round(2),
                      hover_data=["station_days"])
        figv.update_traces(marker_color="#8e6bbf", textposition="outside")
        figv.update_layout(height=280, margin=dict(l=0, r=0, t=24, b=0),
                           title="Calls per day by crew size (station-days)",
                           xaxis_title="people on the station", yaxis_title="mean calls/day")
        render(figv)

# ------------------------------------------------------------------------- burden
with tabs[5]:
    basket = sel[~sel.index.isin(perf.index)]
    u = units[(units["date"].dt.date >= start) & (units["date"].dt.date <= end)]
    u = u[u["unit_hours"].between(0, 48)]
    left, right = st.columns(2)
    with left:
        figp = px.pie(names=["performance basket", "burden / out-of-scope"],
                      values=[len(perf), len(basket)], hole=.55,
                      color_discrete_sequence=["#2f7ed8", "#c0392b"])
        figp.update_layout(height=300, margin=dict(l=0, r=0, t=24, b=0),
                           title="Work split — in scope vs burden",
                           legend=dict(orientation="h", y=-0.1))
        render(figp)
    with right:
        uh = u.groupby("year")["unit_hours"].agg(rows="size", hours="sum").reset_index()
        if not uh.empty:
            tot = (sel.groupby("year")["incident_uid"].nunique()
                   .rename("incidents").reset_index())
            uh = uh.merge(tot, on="year", how="left")
            per_inc = (u.groupby("year")["incident_uid"].nunique()
                       .rename("incidents_with_units").reset_index())
            uh = uh.merge(per_inc, on="year", how="left")
            uh["unit_coverage"] = (uh["incidents_with_units"] /
                                   uh["incidents"].replace(0, pd.NA))
            usable = uh[uh["unit_coverage"].fillna(0) >= 0.5].copy()
            dropped = uh[uh["unit_coverage"].fillna(0) < 0.5]
            usable["hours_per_day"] = (usable["hours"] /
                                       usable["year"].apply(
                                           lambda y: 366 if y % 4 == 0 else 365)).round(2)
            figh = px.bar(usable, x="year", y="hours_per_day", text="hours_per_day")
            figh.update_traces(marker_color="#e67e22", textposition="outside")
            figh.update_layout(height=300, margin=dict(l=0, r=0, t=24, b=0),
                               title="Unit-hours committed per day",
                               xaxis_title=None, yaxis_title="hours / day")
            render(figh)
            if not dropped.empty:
                st.caption(
                    "Years without apparatus records are omitted rather than plotted as "
                    "zero: "
                    + ", ".join(f"{int(r.year)} ({r.unit_coverage*100:.0f}% of incidents "
                                f"have unit rows)" for r in dropped.itertuples())
                    + ". Unit-level data ends with the NFIRS era."
                )

    reasons = pd.DataFrame([
        {"reason excluded": "mutual aid given",
         "incidents": int((basket["mutual_aid_given"] != "").sum())},
        {"reason excluded": "wildland",
         "incidents": int((basket["incident_class"] == "Wildland").sum())},
        {"reason excluded": f"arrival over {RESPONSE_CAP} min",
         "incidents": int((basket["alarm_to_arrival_min"] > RESPONSE_CAP).sum())},
        {"reason excluded": "backcountry / rescue class",
         "incidents": int((basket["incident_class"] == "rescue_backcountry").sum())},
        {"reason excluded": "ALL out-of-scope (distinct)", "incidents": len(basket)},
        {"reason excluded": "in-scope (performance basket)", "incidents": len(perf)},
    ])
    st.dataframe(reasons, hide_index=True, width="stretch")
    st.caption(
        "A single incident can be excluded for more than one reason, so the reason rows "
        "add up to more than the distinct total. Burden is reported separately and never "
        "mixed into response performance: mutual-aid runs, wildland deployments and "
        "backcountry rescues are real work that NFPA 1710 excludes from that measure."
    )

# ----------------------------------------------------------------------- method
with st.expander("Metric definitions and caveats"):
    st.markdown(f"""
**Window.** Analysis starts 2020-01-01: incident counts jump 2.19× from 2019 (292) to
2020 (431) — a reporting-practice change, not workload. Data as of **{DATA_AS_OF:%d %b %Y}**,
with the last {LAG_MONTHS} months under-counted.

**Call types** come from the NFIRS type code: Fire (structure, vehicle, alarms, HazMat,
other), Wildland, EMS, Motor vehicle accident, Backcountry rescue, Service. Mutual aid is a
flag rather than a class, so a mutual-aid structure fire counts as a structure fire.
Records from the NERIS era carry no type at all and stay **Untyped** instead of being
folded into Fire.

**Two baskets.** *Performance* uses in-scope incidents only — no mutual aid given, not
wildland, arrival within {RESPONSE_CAP} min. Everything excluded is reported as
*burden* (unit-hours consumed), following the NFPA 1710/1720 convention.

**Response time** = alarm → first unit arrival, incident grain, percentiles computed on
timed in-scope incidents only. **Time on scene** = arrived → cleared, unit grain, capped
at {SCENE_CAP} min; unit records thin out badly in 2026.

**Coverage** counts station assignments only (`Elkhorn`, `Greenhorn`), excluding training,
event staffing and wildland deployment, and excludes the ambiguous `Day Shift` /
`POC Shift 24hr` categories — so the figure is deliberately conservative. Published future
schedule is always excluded from performance measures.

**Jurisdiction.** Only resolvable from 2026 (explicit SVFD/NBCFD prefix, else inferred from
zone); {int((inc["org_code"].isna() & (inc["year"] >= 2026)).sum()):,} records stay unresolved
rather than defaulted.
""")
