# Subway O-D Explorer

Interactive dual-map visualization of NYC subway origin-destination ridership (2025).

## Setup

```bash
pip install fastapi uvicorn duckdb
```

## Run

```bash
python subway_viz/server.py
```

Open http://localhost:8765

## Usage

- **Left map**: drag to select destination stations (toggle Select/Pan mode in toolbar)
- **Right map**: shows origin stations sized and colored by ridership volume
- Click individual stations to toggle selection
- Use filters (month, day, hour range) to narrow the data
- Switch "Select Destinations" / "Select Origins" to reverse the direction
