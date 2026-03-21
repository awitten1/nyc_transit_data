# NYC Transit Explorer

Interactive visualization of NYC yellow taxi and Citibike trips by zone.

## Data layout

```
nyc_transit_data/
├── data/
│   └── yellow_taxi_records/   # yellow_taxi_YYYY_MM.parquet files
├── citibike_data.duckdb        # Citibike rides database
└── taxi_viz/                  # this directory
```

The server expects taxi parquet files at `../data/yellow_taxi_records/` and the Citibike database at `../citibike_data.duckdb` relative to this directory. Citibike data is optional — the app runs without it.

## Setup

**Python dependencies**

```bash
pip install fastapi uvicorn duckdb geopandas pandas
```

**Build the frontend** (requires Node 18+)

```bash
cd frontend
npm install
npm run build
cd ..
```

This compiles the React app into `static/`, which the FastAPI server serves directly.

## Run

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

Then open [http://localhost:8000](http://localhost:8000).

## Development (live-reload frontend)

In one terminal start the backend:

```bash
uvicorn server:app --reload --port 8000
```

In another, start the Vite dev server:

```bash
cd frontend
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). API calls are proxied to port 8000.
