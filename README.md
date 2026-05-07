# Subway O-D Explorer

Interactive dual-map visualization of NYC subway origin-destination ridership (2025).

## Setup

Using conda (recommended — installs the `shapely` geometry dep cleanly):

```bash
conda env create -f subway_viz/environment.yml
conda activate subway-viz
```

Or with pip:

```bash
pip install fastapi uvicorn duckdb pandas numpy scipy shapely
```

## Run

```bash
python subway_viz/server.py
```

Open http://localhost:8765

## Usage

- **Left map**: drag to select origin stations (toggle Select/Pan mode in toolbar)
- **Right map**: shows destination stations sized and colored by ridership volume
- Click individual stations to toggle selection
- Use filters (month, day, hour range) to narrow the data
- Switch "Select Origins" / "Select Destinations" to reverse the direction
