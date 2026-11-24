from pathlib import Path
import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="Smart Parking Demand & Event Impact",
    page_icon="🅿️",
    layout="wide",
)

BASE_DIR = Path(__file__).resolve().parent
CLEAN_DIR = BASE_DIR / "data" / "cleaned"

required_files = [
    "clean_sessions.parquet",
    "all_sessions_with_unmatched.parquet",
    "hourly_occupancy.parquet",
    "gate_offsets.parquet",
]
missing = [f for f in required_files if not (CLEAN_DIR / f).exists()]
if missing:
    st.error(f"Missing cleaned files in data/cleaned: {', '.join(missing)}. Run `python src/etl_pipeline.py` first.")
    st.stop()


@st.cache_data
def load_data():
    sessions = pd.read_parquet(CLEAN_DIR / "clean_sessions.parquet")
    all_sessions = pd.read_parquet(CLEAN_DIR / "all_sessions_with_unmatched.parquet")
    hourly = pd.read_parquet(CLEAN_DIR / "hourly_occupancy.parquet")
    offsets = pd.read_parquet(CLEAN_DIR / "gate_offsets.parquet")

    hourly["hour_ts"] = pd.to_datetime(hourly["hour_ts"])
    sessions["entry_ts"] = pd.to_datetime(sessions["entry_ts"])
    sessions["exit_ts"] = pd.to_datetime(sessions["exit_ts"])
    return sessions, all_sessions, hourly, offsets


sessions_df, all_sessions_df, hourly_df, offsets_df = load_data()

st.title("Smart Parking Demand & Event Impact")
st.caption("Descriptive parking analytics: gate clock synchronization, occupancy estimation, peak detection, and event vs. non-event comparison.")

# --- Sidebar Filters ---
st.sidebar.header("Filters")
lot_options = sorted(hourly_df["lot_id"].unique().tolist())
selected_lot = st.sidebar.selectbox("Parking Lot", options=lot_options)

min_date = hourly_df["hour_ts"].min().date()
max_date = hourly_df["hour_ts"].max().date()
selected_dates = st.sidebar.date_input(
    "Date Range",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date,
)

if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
    start_d, end_d = selected_dates
else:
    start_d = end_d = min_date

weather_options = ["All"] + sorted(hourly_df["weather_condition"].unique().tolist())
selected_weather = st.sidebar.selectbox("Weather Condition", options=weather_options)

# Apply filters
f_hourly = hourly_df[
    (hourly_df["lot_id"] == selected_lot)
    & (hourly_df["hour_ts"].dt.date >= start_d)
    & (hourly_df["hour_ts"].dt.date <= end_d)
].copy()

if selected_weather != "All":
    f_hourly = f_hourly[f_hourly["weather_condition"] == selected_weather].copy()

f_sessions = sessions_df[
    (sessions_df["lot_id"] == selected_lot)
    & (sessions_df["entry_ts"].dt.date >= start_d)
    & (sessions_df["entry_ts"].dt.date <= end_d)
].copy()

if f_hourly.empty:
    st.warning("No hourly records match the selected filters.")
    st.stop()

# --- Top KPIs ---
lot_capacity = int(f_hourly["total_spaces"].iloc[0])
avg_occ = round(f_hourly["occupancy_pct"].mean(), 1)
peak_occ = round(f_hourly["occupancy_pct"].max(), 1)
paired_count = len(f_sessions)
total_rev = round(f_sessions["amount"].sum(), 2)

k1, k2, k3, k4 = st.columns(4)
k1.metric("Average Occupancy", f"{avg_occ}%", help=f"Lot capacity: {lot_capacity} spaces")
k2.metric("Peak Occupancy", f"{peak_occ}%", help="Highest hourly occupancy in selected range")
k3.metric("Paired Sessions", f"{paired_count:,}")
k4.metric("Matched Revenue", f"₹{total_rev:,.2f}")

# --- Tabbed Views ---
tab1, tab2, tab3, tab4 = st.tabs([
    "Occupancy Over Time",
    "Peak Periods Heatmap",
    "Event vs. Non-Event",
    "Data Quality & Limitations",
])

with tab1:
    st.subheader("Hourly Active Vehicles vs. Lot Capacity")
    fig_occ = go.Figure()
    fig_occ.add_trace(
        go.Scatter(
            x=f_hourly["hour_ts"],
            y=f_hourly["active_vehicles"],
            mode="lines",
            name="Active Vehicles",
        )
    )
    fig_occ.add_hline(
        y=lot_capacity,
        line_dash="dash",
        line_color="red",
        annotation_text=f"Capacity ({lot_capacity})",
    )
    fig_occ.update_layout(
        xaxis_title="Hour",
        yaxis_title="Vehicles in Lot",
        hovermode="x unified",
        height=400,
    )
    st.plotly_chart(fig_occ, use_container_width=True)

    st.subheader("Occupancy % by Hour")
    fig_pct = px.bar(
        f_hourly,
        x="hour_ts",
        y="occupancy_pct",
        color="is_event_window",
        labels={"hour_ts": "Hour", "occupancy_pct": "Occupancy (%)", "is_event_window": "Event Window"},
        height=340,
    )
    st.plotly_chart(fig_pct, use_container_width=True)

with tab2:
    st.subheader("Peak Period Heatmap (85th Percentile Threshold per Lot)")
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    heat_df = (
        f_hourly.groupby(["day_of_week", "hour_of_day"], as_index=False)["occupancy_pct"]
        .mean()
    )
    heat_df["day_of_week"] = pd.Categorical(heat_df["day_of_week"], categories=day_order, ordered=True)
    heat_df = heat_df.sort_values(["day_of_week", "hour_of_day"])

    pivot_heat = heat_df.pivot(index="day_of_week", columns="hour_of_day", values="occupancy_pct")
    fig_heat = px.imshow(
        pivot_heat,
        labels={"x": "Hour of Day (0-23)", "y": "Day of Week", "color": "Avg Occupancy %"},
        aspect="auto",
        height=420,
    )
    st.plotly_chart(fig_heat, use_container_width=True)

    peak_hours_summary = (
        f_hourly[f_hourly["is_peak_hour"]]
        .groupby("hour_of_day", as_index=False)
        .agg(peak_hour_count=("hour_ts", "count"), avg_occ_pct=("occupancy_pct", "mean"))
        .sort_values("peak_hour_count", ascending=False)
    )
    st.markdown("**Most Frequent Peak Hours**")
    st.dataframe(peak_hours_summary.head(8), use_container_width=True, hide_index=True)

with tab3:
    st.subheader("Event Window vs. Non-Event Window Occupancy")
    comp_df = (
        f_hourly.groupby("is_event_window", as_index=False)
        .agg(
            hours_count=("hour_ts", "count"),
            avg_occupancy_pct=("occupancy_pct", "mean"),
            avg_active_vehicles=("active_vehicles", "mean"),
        )
    )
    comp_df["window_type"] = comp_df["is_event_window"].map({True: "Event Window (±2h)", False: "Non-Event Window"})
    comp_df["avg_occupancy_pct"] = comp_df["avg_occupancy_pct"].round(1)
    comp_df["avg_active_vehicles"] = comp_df["avg_active_vehicles"].round(1)

    c1, c2 = st.columns([1.2, 1])
    with c1:
        fig_comp = px.bar(
            comp_df,
            x="window_type",
            y="avg_occupancy_pct",
            color="window_type",
            labels={"window_type": "Period Type", "avg_occupancy_pct": "Average Occupancy (%)"},
            height=360,
        )
        st.plotly_chart(fig_comp, use_container_width=True)

    with c2:
        st.dataframe(
            comp_df[["window_type", "hours_count", "avg_active_vehicles", "avg_occupancy_pct"]],
            use_container_width=True,
            hide_index=True,
        )

    event_only = f_hourly[f_hourly["is_event_window"]]
    if not event_only.empty:
        st.markdown("**Events Appearing in Selected Range**")
        ev_table = (
            event_only.groupby("event_names", as_index=False)
            .agg(hours_covered=("hour_ts", "count"), avg_occ_pct=("occupancy_pct", "mean"), max_occ_pct=("occupancy_pct", "max"))
            .round(1)
        )
        st.dataframe(ev_table, use_container_width=True, hide_index=True)

with tab4:
    st.subheader("Gate Clock Offsets & Unmatched Event Audit")
    lot_offsets = offsets_df[offsets_df["gate_id"].str.contains(selected_lot[-1], na=False)]
    st.markdown("**Estimated Gate Clock Offsets (Seconds Added to Sync to Payment Clock)**")
    st.dataframe(lot_offsets if not lot_offsets.empty else offsets_df, use_container_width=True, hide_index=True)

    status_counts = (
        all_sessions_df[all_sessions_df["lot_id"] == selected_lot]["session_status"]
        .value_counts()
        .reset_index()
    )
    status_counts.columns = ["session_status", "record_count"]
    st.markdown("**Session Pairing Breakdown**")
    st.dataframe(status_counts, use_container_width=True, hide_index=True)

    st.markdown("### Documented Limitations (Important for Jury Viva)")
    st.markdown("""
    * **Observational comparison, not strict causation:** Higher occupancy during event windows can overlap with weekends, evening peaks, or weather shifts—event schedules show association, not isolated causal proof.
    * **Boundary & missing-entry blind spots:** Vehicles with unmatched exits (`UNMATCHED_EXIT`) or missing exits are excluded from duration-based occupancy rather than guessed, which can slightly undercount active vehicles.
    * **Initial state at start of dataset (`t = 0`):** Any vehicle already parked before the first recorded timestamp is not captured unless an exit occurs later.
    * **Clock offset estimation assumption:** Gate drift is estimated relative to payment timestamps, assuming payment terminals keep accurate network time.
    """)