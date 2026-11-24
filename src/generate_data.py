from pathlib import Path
import numpy as np
import pandas as pd

# Fixed seed so to get the same dataset every time i run it
rng = np.random.default_rng(42)

# Find smart-parking-analytics/data/raw no matter where i run the script from
BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

# 1. Lot capacity
lots_df = pd.DataFrame([
    {"lot_id": "LOT_A", "lot_name": "City Mall Garage", "total_spaces": 120},
    {"lot_id": "LOT_B", "lot_name": "Metro Station Lot", "total_spaces": 80},
])
lots_df.to_csv(RAW_DIR / "lot_capacity.csv", index=False)

# 2. Nearby events (10 event windows across October & November 2026)
NUM_DAYS = 60
start_date = pd.Timestamp("2026-10-01 00:00:00")

events_df = pd.DataFrame([
    {"event_id": "EVT_01", "event_name": "Arena Concert", "lot_id": "LOT_A", "start_time": "2026-10-03 18:00:00", "end_time": "2026-10-03 22:30:00", "expected_attendance": 4500},
    {"event_id": "EVT_02", "event_name": "Weekend Food Fest", "lot_id": "LOT_A", "start_time": "2026-10-04 12:00:00", "end_time": "2026-10-04 21:00:00", "expected_attendance": 3000},
    {"event_id": "EVT_03", "event_name": "Tech Expo", "lot_id": "LOT_B", "start_time": "2026-10-08 09:00:00", "end_time": "2026-10-08 18:00:00", "expected_attendance": 2200},
    {"event_id": "EVT_04", "event_name": "Night Cricket Screening", "lot_id": "LOT_B", "start_time": "2026-10-11 19:00:00", "end_time": "2026-10-11 23:30:00", "expected_attendance": 3500},
    {"event_id": "EVT_05", "event_name": "Diwali Shopping Night", "lot_id": "LOT_A", "start_time": "2026-10-24 16:00:00", "end_time": "2026-10-24 23:00:00", "expected_attendance": 5000},
    {"event_id": "EVT_06", "event_name": "Festive Mela", "lot_id": "LOT_A", "start_time": "2026-10-25 11:00:00", "end_time": "2026-10-25 22:00:00", "expected_attendance": 4200},
    {"event_id": "EVT_07", "event_name": "Startup Summit", "lot_id": "LOT_B", "start_time": "2026-11-05 09:30:00", "end_time": "2026-11-05 18:30:00", "expected_attendance": 2600},
    {"event_id": "EVT_08", "event_name": "Comedy Night Live", "lot_id": "LOT_A", "start_time": "2026-11-14 19:00:00", "end_time": "2026-11-14 22:30:00", "expected_attendance": 2800},
    {"event_id": "EVT_09", "event_name": "Book & Craft Fair", "lot_id": "LOT_B", "start_time": "2026-11-21 10:00:00", "end_time": "2026-11-21 20:00:00", "expected_attendance": 3100},
    {"event_id": "EVT_10", "event_name": "Music Festival Finale", "lot_id": "LOT_A", "start_time": "2026-11-28 17:00:00", "end_time": "2026-11-28 23:00:00", "expected_attendance": 4800},
])
events_df.to_csv(RAW_DIR / "nearby_events.csv", index=False)

# 3. Hourly weather over 60 days (Oct 1 - Nov 29, 2026)
hours = pd.date_range(start_date, periods=NUM_DAYS * 24, freq="h")
conditions = rng.choice(["Clear", "Cloudy", "Light Rain"], size=len(hours), p=[0.68, 0.23, 0.09])
weather_df = pd.DataFrame({
    "timestamp_hour": hours.strftime("%Y-%m-%d %H:%M:%S"),
    "temp_c": np.round(rng.uniform(20.0, 33.5, size=len(hours)), 1),
    "precipitation_mm": np.where(conditions == "Light Rain", np.round(rng.uniform(0.5, 6.0, size=len(hours)), 1), 0.0),
    "condition": conditions,
})
weather_df.to_csv(RAW_DIR / "weather.csv", index=False)

# 4. Gate events & payments with intentional clock drift + unmatched exits
gate_offsets_sec = {
    "GATE_A_IN": 0,
    "GATE_A_OUT": -240,  # Exit gate runs 4 minutes behind
    "GATE_B_IN": 120,    # Entry gate runs 2 minutes ahead
    "GATE_B_OUT": -180,  # Exit gate runs 3 minutes behind
}

gate_rows = []
payment_rows = []
event_counter = 1
pay_counter = 1

event_days = {
    (row["lot_id"], row["start_time"][:10])
    for _, row in events_df.iterrows()
}

for day_idx in range(NUM_DAYS):
    day_ts = start_date + pd.Timedelta(days=day_idx)
    day_str = day_ts.strftime("%Y-%m-%d")

    for lot_id, in_gate, out_gate in [
        ("LOT_A", "GATE_A_IN", "GATE_A_OUT"),
        ("LOT_B", "GATE_B_IN", "GATE_B_OUT"),
    ]:
        base_trips = 95 if (lot_id, day_str) in event_days else 55
        n_trips = int(base_trips + rng.integers(-8, 12))

        for t_idx in range(n_trips):
            vehicle_id = f"VEH_{day_idx:02d}_{lot_id}_{t_idx:03d}"

            if (lot_id, day_str) in event_days and rng.random() < 0.45:
                arrival_hour = int(rng.integers(15, 21))
            else:
                arrival_hour = int(rng.integers(7, 22))

            true_entry = day_ts + pd.Timedelta(
                hours=arrival_hour,
                minutes=int(rng.integers(0, 60)),
                seconds=int(rng.integers(0, 60)),
            )
            duration_mins = int(rng.integers(25, 240))
            true_exit = true_entry + pd.Timedelta(minutes=duration_mins)

            raw_entry = true_entry + pd.Timedelta(seconds=gate_offsets_sec[in_gate])
            raw_exit = true_exit + pd.Timedelta(seconds=gate_offsets_sec[out_gate])

            drop_entry = rng.random() < 0.06  # ~6% unmatched exits (missing entry)
            drop_exit = rng.random() < 0.03   # ~3% missing exit

            if not drop_entry:
                gate_rows.append({
                    "event_id": f"GE_{event_counter:05d}",
                    "vehicle_id": vehicle_id,
                    "lot_id": lot_id,
                    "gate_id": in_gate,
                    "direction": "ENTRY",
                    "raw_timestamp": raw_entry.strftime("%Y-%m-%d %H:%M:%S"),
                })
                event_counter += 1

            if not drop_exit:
                gate_rows.append({
                    "event_id": f"GE_{event_counter:05d}",
                    "vehicle_id": vehicle_id,
                    "lot_id": lot_id,
                    "gate_id": out_gate,
                    "direction": "EXIT",
                    "raw_timestamp": raw_exit.strftime("%Y-%m-%d %H:%M:%S"),
                })
                event_counter += 1

                paid_at = true_exit - pd.Timedelta(seconds=int(rng.integers(15, 180)))
                amount = float(np.clip(round((duration_mins / 60.0) * 30.0, 2), 20.0, 150.0))
                payment_rows.append({
                    "payment_id": f"PAY_{pay_counter:05d}",
                    "vehicle_id": vehicle_id,
                    "lot_id": lot_id,
                    "paid_at": paid_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "amount": amount,
                })
                pay_counter += 1

pd.DataFrame(gate_rows).to_csv(RAW_DIR / "gate_events.csv", index=False)
pd.DataFrame(payment_rows).to_csv(RAW_DIR / "payments.csv", index=False)

print(f"Generated 5 CSV files covering {NUM_DAYS} days in:", RAW_DIR)