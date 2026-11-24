# Smart Parking Demand and Event Impact

## Problem Statement
A parking operator collects entry/exit gate logs, lot capacity, payment transactions, nearby event schedules, and hourly weather data. Because gate clocks are unsynchronized and some vehicles have unmatched exits, raw logs produce inaccurate occupancy metrics. This project builds an end-to-end data engineering and visualization pipeline to clean event sequences, estimate hourly occupancy, identify peak periods, compare event vs. non-event windows, and document analytical limitations.

## Tech Stack
- **Data Generation & Interval Processing:** Python (`pandas`, `numpy`)
- **In-Process Analytical ETL:** DuckDB SQL (`LEAD`/`LAG` sequence pairing, median gate clock alignment)
- **Storage Format:** CSV (raw) & Apache Parquet (cleaned)
- **Visualization:** Streamlit & Plotly

## Project Structure
```text
smart-parking-analytics/
├── app.py                   # Streamlit + Plotly interactive dashboard
├── requirements.txt         # Python dependencies
├── README.md                # Project documentation
├── data/
│   ├── raw/                 # 5 raw input CSV tables
│   └── cleaned/             # 4 processed Parquet tables
└── src/
    ├── generate_data.py     # Generates 60 days of multi-source data with clock drift & unmatched exits
    └── etl_pipeline.py      # Synchronizes clocks, pairs sessions, computes occupancy & peak flags