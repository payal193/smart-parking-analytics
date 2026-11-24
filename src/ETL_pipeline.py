from pathlib import Path
import duckdb
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"
CLEAN_DIR = BASE_DIR / "data" / "cleaned"
CLEAN_DIR.mkdir(parents=True, exist_ok=True)

con = duckdb.connect(database=":memory:")

# Load raw CSVs into DuckDB views
con.execute(f"""
    CREATE OR REPLACE VIEW raw_gate_events AS
    SELECT
        event_id,
        vehicle_id,
        lot_id,
        gate_id,
        UPPER(TRIM(direction)) AS direction,
        CAST(raw_timestamp AS TIMESTAMP) AS raw_ts
    FROM read_csv_auto('{ (RAW_DIR / "gate_events.csv").as_posix() }', header=True);

    CREATE OR REPLACE VIEW raw_payments AS
    SELECT
        payment_id,
        vehicle_id,
        lot_id,
        CAST(paid_at AS TIMESTAMP) AS paid_at,
        CAST(amount AS DOUBLE) AS amount
    FROM read_csv_auto('{ (RAW_DIR / "payments.csv").as_posix() }', header=True);

    CREATE OR REPLACE VIEW raw_lots AS
    SELECT
        lot_id,
        lot_name,
        CAST(total_spaces AS INTEGER) AS total_spaces
    FROM read_csv_auto('{ (RAW_DIR / "lot_capacity.csv").as_posix() }', header=True);

    CREATE OR REPLACE VIEW raw_events AS
    SELECT
        event_id,
        event_name,
        lot_id,
        CAST(start_time AS TIMESTAMP) AS start_time,
        CAST(end_time AS TIMESTAMP) AS end_time,
        CAST(expected_attendance AS INTEGER) AS expected_attendance
    FROM read_csv_auto('{ (RAW_DIR / "nearby_events.csv").as_posix() }', header=True);

    CREATE OR REPLACE VIEW raw_weather AS
    SELECT
        CAST(timestamp_hour AS TIMESTAMP) AS timestamp_hour,
        CAST(temp_c AS DOUBLE) AS temp_c,
        CAST(precipitation_mm AS DOUBLE) AS precipitation_mm,
        condition
    FROM read_csv_auto('{ (RAW_DIR / "weather.csv").as_posix() }', header=True);
""")

# 1. Estimate gate clock offsets (in seconds) using EXIT events matched to payments
# Payment kiosks are assumed synchronized, so median(paid_at - raw_exit_ts) estimates gate drift.
offsets_df = con.execute("""
    WITH exit_pay AS (
        SELECT
            g.gate_id,
            EXTRACT(EPOCH FROM (p.paid_at - g.raw_ts)) AS diff_sec
        FROM raw_gate_events g
        JOIN raw_payments p
          ON g.vehicle_id = p.vehicle_id
         AND g.lot_id = p.lot_id
        WHERE g.direction = 'EXIT'
          AND ABS(EXTRACT(EPOCH FROM (p.paid_at - g.raw_ts))) < 900
    )
    SELECT
        gate_id,
        ROUND(MEDIAN(diff_sec), 1) AS estimated_offset_sec
    FROM exit_pay
    GROUP BY gate_id
""").df()

offset_map = dict(zip(offsets_df["gate_id"], offsets_df["estimated_offset_sec"]))
con.register("gate_offsets", offsets_df)

# Normalize timestamps by adding estimated clock offset per gate
synced_events = con.execute("""
    SELECT
        g.event_id,
        g.vehicle_id,
        g.lot_id,
        g.gate_id,
        g.direction,
        g.raw_ts,
        COALESCE(o.estimated_offset_sec, 0.0) AS offset_sec,
        g.raw_ts + (COALESCE(o.estimated_offset_sec, 0.0) * INTERVAL '1 second') AS synced_ts
    FROM raw_gate_events g
    LEFT JOIN gate_offsets o ON g.gate_id = o.gate_id
""").df()

con.register("synced_events", synced_events)

# 2. Pair sequential ENTRY -> EXIT per vehicle & lot; also flag unmatched exits & missing exits
sessions_df = con.execute("""
    WITH ordered_ev AS (
        SELECT
            *,
            LEAD(direction) OVER (PARTITION BY vehicle_id, lot_id ORDER BY synced_ts, event_id) AS next_dir,
            LEAD(synced_ts) OVER (PARTITION BY vehicle_id, lot_id ORDER BY synced_ts, event_id) AS next_ts,
            LAG(direction) OVER (PARTITION BY vehicle_id, lot_id ORDER BY synced_ts, event_id) AS prev_dir
        FROM synced_events
    ),
    paired AS (
        SELECT
            vehicle_id,
            lot_id,
            synced_ts AS entry_ts,
            next_ts AS exit_ts,
            ROUND(EXTRACT(EPOCH FROM (next_ts - synced_ts)) / 60.0, 1) AS duration_min,
            'PAIRED' AS session_status
        FROM ordered_ev
        WHERE direction = 'ENTRY'
          AND next_dir = 'EXIT'
          AND next_ts > synced_ts
          AND EXTRACT(EPOCH FROM (next_ts - synced_ts)) <= 86400
    ),
    unmatched_exits AS (
        SELECT
            o.vehicle_id,
            o.lot_id,
            CAST(NULL AS TIMESTAMP) AS entry_ts,
            o.synced_ts AS exit_ts,
            CAST(NULL AS DOUBLE) AS duration_min,
            'UNMATCHED_EXIT' AS session_status
        FROM ordered_ev o
        WHERE o.direction = 'EXIT'
          AND (o.prev_dir IS NULL OR o.prev_dir <> 'ENTRY')
    )
    SELECT * FROM paired
    UNION ALL
    SELECT * FROM unmatched_exits
""").df()

# Attach payment amount where vehicle_id + lot_id matches
payments_df = con.execute("SELECT * FROM raw_payments").df()
paired_only = sessions_df[sessions_df["session_status"] == "PAIRED"].copy()
paired_only = paired_only.merge(
    payments_df[["vehicle_id", "lot_id", "amount"]],
    on=["vehicle_id", "lot_id"],
    how="left",
)
paired_only["amount"] = paired_only["amount"].fillna(0.0)

# 3. Compute hourly occupancy across all 14 days x 24 hours per lot
lots_df = con.execute("SELECT * FROM raw_lots").df()
weather_df = con.execute("SELECT * FROM raw_weather").df()
events_df = con.execute("SELECT * FROM raw_events").df()

hourly_rows = []
for _, lot in lots_df.iterrows():
    lot_id = lot["lot_id"]
    cap = int(lot["total_spaces"])
    lot_sessions = paired_only[paired_only["lot_id"] == lot_id]

    for _, w in weather_df.iterrows():
        h_start = pd.Timestamp(w["timestamp_hour"])
        h_end = h_start + pd.Timedelta(hours=1)

        # Vehicle is present during [h_start, h_end) if entry_ts < h_end and exit_ts > h_start
        active = int(((lot_sessions["entry_ts"] < h_end) & (lot_sessions["exit_ts"] > h_start)).sum())
        occ_pct = round((active / cap) * 100.0, 1)

        # Check if an event overlaps this hour or falls within 2 hours before/after
        ev_match = events_df[
            (events_df["lot_id"] == lot_id)
            & (pd.to_datetime(events_df["start_time"]) - pd.Timedelta(hours=2) < h_end)
            & (pd.to_datetime(events_df["end_time"]) + pd.Timedelta(hours=2) > h_start)
        ]
        is_event_window = len(ev_match) > 0
        event_names = ", ".join(ev_match["event_name"].tolist()) if is_event_window else "None"

        hourly_rows.append({
            "lot_id": lot_id,
            "lot_name": lot["lot_name"],
            "total_spaces": cap,
            "hour_ts": h_start,
            "date_str": h_start.strftime("%Y-%m-%d"),
            "day_of_week": h_start.day_name(),
            "hour_of_day": int(h_start.hour),
            "active_vehicles": active,
            "occupancy_pct": occ_pct,
            "is_event_window": is_event_window,
            "event_names": event_names,
            "temp_c": w["temp_c"],
            "precipitation_mm": w["precipitation_mm"],
            "weather_condition": w["condition"],
        })

hourly_df = pd.DataFrame(hourly_rows)

# Mark top 15% (85th percentile+) occupancy hours per lot as peak
p85_by_lot = hourly_df.groupby("lot_id")["occupancy_pct"].quantile(0.85).to_dict()
hourly_df["is_peak_hour"] = hourly_df.apply(
    lambda r: bool(r["occupancy_pct"] >= p85_by_lot[r["lot_id"]]), axis=1
)

# Save cleaned outputs for fast dashboard loading
paired_only.to_parquet(CLEAN_DIR / "clean_sessions.parquet", index=False)
sessions_df.to_parquet(CLEAN_DIR / "all_sessions_with_unmatched.parquet", index=False)
hourly_df.to_parquet(CLEAN_DIR / "hourly_occupancy.parquet", index=False)
offsets_df.to_parquet(CLEAN_DIR / "gate_offsets.parquet", index=False)

print("ETL complete!")
print("Detected gate offsets (sec):", offset_map)
print("Paired sessions:", len(paired_only), "| Unmatched exits:", int((sessions_df["session_status"] == "UNMATCHED_EXIT").sum()))
print("Saved cleaned parquet files to:", CLEAN_DIR)