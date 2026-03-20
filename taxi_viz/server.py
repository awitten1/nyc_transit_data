import calendar
import json

import os
import re
from functools import lru_cache
from pathlib import Path

import duckdb
import geopandas as gpd
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).parent
RECORDS_DIR = Path(os.path.join(os.path.dirname(BASE_DIR), "data", "yellow_taxi_records"))
ZONES_SHP = BASE_DIR / "tmp" / "taxi_zones2" / "taxi_zones" / "taxi_zones.shp"
ZONES_CACHE = BASE_DIR / "zones_cache.geojson"

# ── Startup: scan available months ───────────────────────────────────────────
_MONTH_RE = re.compile(r"yellow_taxi_(\d{4})_(\d{2})\.parquet$")

AVAILABLE_MONTHS: list[dict] = []
for fname in sorted(RECORDS_DIR.glob("*.parquet")):
    m = _MONTH_RE.match(fname.name)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        AVAILABLE_MONTHS.append(
            {
                "year": y,
                "month": mo,
                "label": f"{calendar.month_abbr[mo]} {y}",
            }
        )
AVAILABLE_MONTHS.sort(key=lambda x: (x["year"], x["month"]))

# ── Startup: generate/load zones GeoJSON ─────────────────────────────────────
if not ZONES_CACHE.exists():
    print("Generating zones_cache.geojson …")
    gdf = gpd.read_file(ZONES_SHP).to_crs("EPSG:4326")
    gdf = gdf[["LocationID", "zone", "borough", "geometry"]].copy()
    gdf["LocationID"] = gdf["LocationID"].astype(int)
    gdf["geometry"] = gdf["geometry"].simplify(tolerance=0.0003, preserve_topology=True)
    gdf.to_file(ZONES_CACHE, driver="GeoJSON")
    print(f"  Written: {ZONES_CACHE} ({ZONES_CACHE.stat().st_size // 1024} KB)")

with open(ZONES_CACHE, encoding="utf-8") as f:
    ZONES_GEOJSON: str = f.read()

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI()

# Serve static files (index.html, etc.)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/")
def root():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


@app.get("/api/available_months")
def available_months():
    return {"months": AVAILABLE_MONTHS}


@app.get("/api/zones")
def zones():
    return Response(
        content=ZONES_GEOJSON,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@lru_cache(maxsize=20)
def _query_od_month(year: int, month: int) -> str:
    """Query one month's parquet and return JSON string. Cached indefinitely."""
    path = RECORDS_DIR / f"yellow_taxi_{year}_{month:02d}.parquet"
    if not path.exists():
        return ""

    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    sql = f"""
        SELECT PULocationID, DOLocationID,
               ROUND(AVG(fare_amount), 2) AS avg_fare,
               COUNT(*) AS n_trips
        FROM read_parquet('{path}')
        WHERE trip_distance > 0
          AND fare_amount > 0
          AND total_amount > 0
          AND tpep_pickup_datetime >= '{year}-{month:02d}-01'
          AND tpep_pickup_datetime  < '{next_year}-{next_month:02d}-01'
          AND date_diff('minute', tpep_pickup_datetime, tpep_dropoff_datetime) BETWEEN 1 AND 180
        GROUP BY PULocationID, DOLocationID
    """
    rows = duckdb.execute(sql).fetchall()

    od: dict = {}
    for pu, do, avg_fare, n_trips in rows:
        pu, do = int(pu), int(do)
        od.setdefault(pu, {})[do] = {"f": float(avg_fare), "n": int(n_trips)}

    return json.dumps(od)


def _month_range(start_year: int, start_month: int, end_year: int, end_month: int):
    """Yield (year, month) tuples from start to end inclusive."""
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


def _merge_od(start_year: int, start_month: int, end_year: int, end_month: int) -> dict:
    """Aggregate OD data across all months in range using a weighted average fare."""
    # acc[pu][do] = [weighted_fare_sum, total_trips]
    acc: dict = {}
    for year, month in _month_range(start_year, start_month, end_year, end_month):
        raw = _query_od_month(year, month)
        if not raw:
            continue
        for pu_str, dests in json.loads(raw).items():
            pu = int(pu_str)
            acc.setdefault(pu, {})
            for do_str, stats in dests.items():
                do = int(do_str)
                if do not in acc[pu]:
                    acc[pu][do] = [0.0, 0]
                acc[pu][do][0] += stats["f"] * stats["n"]  # accumulate weighted fare
                acc[pu][do][1] += stats["n"]

    # Compute weighted average fare for each PU→DO pair
    return {
        pu: {
            do: {"f": round(fare_sum / n, 2), "n": n}
            for do, (fare_sum, n) in dests.items()
        }
        for pu, dests in acc.items()
    }


@app.get("/api/od")
def od_flow(
    start_year: int = Query(ge=2019, le=2030),
    start_month: int = Query(ge=1, le=12),
    end_year: int = Query(ge=2019, le=2030),
    end_month: int = Query(ge=1, le=12),
):
    if (end_year, end_month) < (start_year, start_month):
        raise HTTPException(status_code=422, detail="end must be >= start")

    od = _merge_od(start_year, start_month, end_year, end_month)
    if not od:
        raise HTTPException(status_code=404, detail="No data found for this range")

    payload = json.dumps(
        {
            "start": {"year": start_year, "month": start_month},
            "end": {"year": end_year, "month": end_month},
            "od": od,
        }
    )
    return Response(content=payload, media_type="application/json")
