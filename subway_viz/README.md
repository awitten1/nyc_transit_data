# Subway Ridership Visualizer

An interactive subway ridership visualizer with clustering and O-D visualization.

## Setup

### Download datasets

Download `subway_2025` and `subway_hourly_2025` datasets.  These are duckdb files.

### Install dependencies

Using conda:

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

- Left map: drag to select origin stations (toggle Select/Pan mode in toolbar)
- Right map: shows destination stations sized and colored by ridership volume
- Click individual stations or click and drag to select multiple stations.
- Click "clusters" on the top right to see clusters.
- From the "clusters" view interactively adjust the number of clusters using the slide bar at the top
