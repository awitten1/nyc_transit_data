import calendar
import json
import os
import re
from functools import lru_cache
from pathlib import Path

import duckdb
import geopandas as gpd
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).parent
RECORDS_DIR = Path(
    os.path.join(os.path.dirname(BASE_DIR), "data", "yellow_taxi_records")
)
CITIBIKE_DB = BASE_DIR.parent / "citibike_data.duckdb"
ZONES_SHP = BASE_DIR / "tmp" / "taxi_zones2" / "taxi_zones" / "taxi_zones.shp"
ZONES_CACHE = BASE_DIR / "zones_cache.geojson"

# ── Startup: scan available taxi months ──────────────────────────────────────
_MONTH_RE = re.compile(r"yellow_taxi_(\d{4})_(\d{2})\.parquet$")

AVAILABLE_MONTHS: list[dict] = []
for fname in sorted(RECORDS_DIR.glob("*.parquet")):
    m = _MONTH_RE.match(fname.name)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        AVAILABLE_MONTHS.append(
            {"year": y, "month": mo, "label": f"{calendar.month_abbr[mo]} {y}"}
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
else:
    gdf = gpd.read_file(ZONES_CACHE)
    gdf["LocationID"] = gdf["LocationID"].astype(int)

with open(ZONES_CACHE, encoding="utf-8") as f:
    ZONES_GEOJSON: str = f.read()

# ── Startup: compute citibike station → taxi zone mapping (once) ─────────────
# We do this once so per-request citibike queries skip the expensive spatial join.
_STATION_ZONE: dict[str, int] = {}  # station_id → LocationID
_ZONE_SIDS: dict[int, list] = {}  # LocationID → [station_ids]

if CITIBIKE_DB.exists():
    print("Computing citibike station → zone mapping …")
    try:
        _cb_conn = duckdb.connect(str(CITIBIKE_DB), read_only=True)
        _stn_df = _cb_conn.execute(
            """
            SELECT start_station_id AS id,
                   avg(start_lat)   AS lat,
                   avg(start_lng)   AS lng
            FROM rides
            WHERE start_station_id IS NOT NULL
              AND start_lat BETWEEN 40.4 AND 41.0
              AND start_lng BETWEEN -74.3 AND -73.7
            GROUP BY start_station_id
        """
        ).df()
        _cb_conn.close()

        _stn_gdf = gpd.GeoDataFrame(
            _stn_df,
            geometry=gpd.points_from_xy(_stn_df["lng"], _stn_df["lat"]),
            crs="EPSG:4326",
        )
        _joined = _stn_gdf.sjoin(
            gdf[["LocationID", "geometry"]], how="left", predicate="within"
        )
        for _, row in _joined.iterrows():
            if pd.notna(row.get("LocationID")):
                sid = str(row["id"])
                zid = int(row["LocationID"])
                _STATION_ZONE[sid] = zid
                _ZONE_SIDS.setdefault(zid, []).append(sid)

        print(f"  Mapped {len(_STATION_ZONE)} stations → " f"{len(_ZONE_SIDS)} zones")

        # Scan available citibike months
        _cb_months = _cb_conn2 = None
        try:
            _cb_conn2 = duckdb.connect(str(CITIBIKE_DB), read_only=True)
            _cb_months = _cb_conn2.execute(
                """
                SELECT year(started_at) AS y, month(started_at) AS m, count(*) AS n
                FROM rides
                WHERE started_at IS NOT NULL
                GROUP BY y, m
                HAVING n > 1000
                ORDER BY y, m
            """
            ).fetchall()
            _cb_conn2.close()
        except Exception:
            pass

        CITIBIKE_MONTHS: list[dict] = [
            {
                "year": int(y),
                "month": int(m),
                "label": f"{calendar.month_abbr[int(m)]} {int(y)}",
            }
            for y, m, _ in (_cb_months or [])
        ]
    except Exception as e:
        print(f"  Warning: citibike mapping failed — {e}")
else:
    print("citibike_data.duckdb not found — /api/citibike will be unavailable")
    CITIBIKE_MONTHS: list[dict] = []

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI()
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/")
def root():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


@app.get("/api/available_months")
def available_months():
    return {"months": AVAILABLE_MONTHS}


@app.get("/api/citibike_months")
def citibike_months():
    return {"months": CITIBIKE_MONTHS}


@app.get("/api/zones")
def zones():
    return Response(
        content=ZONES_GEOJSON,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ── Taxi OD ───────────────────────────────────────────────────────────────────
@lru_cache(maxsize=20)
def _query_od_month(year: int, month: int) -> str:
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
        WHERE trip_distance > 0 AND fare_amount > 0 AND total_amount > 0
          AND tpep_pickup_datetime >= '{year}-{month:02d}-01'
          AND tpep_pickup_datetime  < '{next_year}-{next_month:02d}-01'
          AND date_diff('minute', tpep_pickup_datetime, tpep_dropoff_datetime)
              BETWEEN 1 AND 180
        GROUP BY PULocationID, DOLocationID
    """
    with duckdb.connect() as conn:
        rows = conn.execute(sql).fetchall()
    od: dict = {}
    for pu, do, avg_fare, n_trips in rows:
        od.setdefault(int(pu), {})[int(do)] = {"f": float(avg_fare), "n": int(n_trips)}
    return json.dumps(od)


def _month_range(sy, sm, ey, em):
    y, m = sy, sm
    while (y, m) <= (ey, em):
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


def _merge_od(sy, sm, ey, em) -> dict:
    acc: dict = {}
    for year, month in _month_range(sy, sm, ey, em):
        raw = _query_od_month(year, month)
        if not raw:
            continue
        for pu_s, dests in json.loads(raw).items():
            pu = int(pu_s)
            acc.setdefault(pu, {})
            for do_s, stats in dests.items():
                do = int(do_s)
                if do not in acc[pu]:
                    acc[pu][do] = [0.0, 0]
                acc[pu][do][0] += stats["f"] * stats["n"]
                acc[pu][do][1] += stats["n"]
    return {
        pu: {do: {"f": round(fs / n, 2), "n": n} for do, (fs, n) in dests.items()}
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
        raise HTTPException(status_code=404, detail="No data for this range")
    payload = json.dumps(
        {
            "start": {"year": start_year, "month": start_month},
            "end": {"year": end_year, "month": end_month},
            "od": od,
        }
    )
    return Response(content=payload, media_type="application/json")


# ── Citibike flows ────────────────────────────────────────────────────────────
_citibike_cache: dict = {}


def _build_citibike(sy: int, sm: int, ey: int, em: int) -> str:
    """Query citibike OD for the time range, group by taxi zone, return JSON."""
    next_em = em + 1 if em < 12 else 1
    next_ey = ey if em < 12 else ey + 1

    conn = duckdb.connect(str(CITIBIKE_DB), read_only=True)
    flows_df = conn.execute(
        f"""
        SELECT start_station_id, end_station_id,
               avg(start_lat) AS slat, avg(start_lng) AS slng,
               avg(end_lat)   AS elat, avg(end_lng)   AS elng,
               count(*)       AS n,
               avg(datediff('second', started_at, ended_at)) AS avg_dur
        FROM rides
        WHERE start_station_id IS NOT NULL AND end_station_id IS NOT NULL
          AND start_lat BETWEEN 40.4 AND 41.0 AND start_lng BETWEEN -74.3 AND -73.7
          AND end_lat   BETWEEN 40.4 AND 41.0 AND end_lng   BETWEEN -74.3 AND -73.7
          AND started_at >= '{sy}-{sm:02d}-01'
          AND started_at <  '{next_ey}-{next_em:02d}-01'
        GROUP BY start_station_id, end_station_id
    """
    ).df()

    # All stations for dot rendering (not time-filtered)
    stations_df = conn.execute(
        """
        SELECT start_station_id AS id,
               any_value(start_station_name) AS name,
               avg(start_lat) AS lat,
               avg(start_lng) AS lng
        FROM rides
        WHERE start_station_id IS NOT NULL
          AND start_lat BETWEEN 40.4 AND 41.0
          AND start_lng BETWEEN -74.3 AND -73.7
        GROUP BY start_station_id
    """
    ).df()
    conn.close()

    # Build zone-level flows: aggregate top 30 rides per zone
    zone_flows: dict = {}
    for zid, sids in _ZONE_SIDS.items():
        sid_set = set(sids)
        mask = flows_df["start_station_id"].astype(str).isin(sid_set)
        sub = flows_df[mask].nlargest(30, "n")
        arrows = [
            {
                "slat": round(float(r["slat"]), 5),
                "slng": round(float(r["slng"]), 5),
                "elat": round(float(r["elat"]), 5),
                "elng": round(float(r["elng"]), 5),
                "n": int(r["n"]),
                "dur": (
                    round(float(r["avg_dur"])) if pd.notna(r.get("avg_dur")) else 600
                ),
            }
            for _, r in sub.iterrows()
            if not any(pd.isna([r["slat"], r["slng"], r["elat"], r["elng"]]))
        ]
        if arrows:
            zone_flows[str(zid)] = arrows

    stations_out = [
        {
            "id": str(r["id"]),
            "name": str(r["name"]) if pd.notna(r["name"]) else "",
            "lat": round(float(r["lat"]), 5),
            "lng": round(float(r["lng"]), 5),
            "zone": _STATION_ZONE.get(str(r["id"]), -1),
        }
        for _, r in stations_df.iterrows()
    ]

    return json.dumps({"stations": stations_out, "zone_flows": zone_flows})


@app.get("/api/citibike")
def citibike_flow(
    start_year: int = Query(ge=2019, le=2030),
    start_month: int = Query(ge=1, le=12),
    end_year: int = Query(ge=2019, le=2030),
    end_month: int = Query(ge=1, le=12),
):
    if not CITIBIKE_DB.exists():
        raise HTTPException(status_code=503, detail="Citibike database not found")
    if (end_year, end_month) < (start_year, start_month):
        raise HTTPException(status_code=422, detail="end must be >= start")

    key = (start_year, start_month, end_year, end_month)
    if key not in _citibike_cache:
        try:
            _citibike_cache[key] = _build_citibike(*key)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return Response(content=_citibike_cache[key], media_type="application/json")


# ── Citibike hourly flows (for Pulse Map) ────────────────────────────────────
_citibike_hourly_cache: dict = {}


def _build_citibike_hourly(sy: int, sm: int, ey: int, em: int) -> str:
    if not CITIBIKE_DB.exists():
        return json.dumps({"by_hour": {}})
    next_em = em + 1 if em < 12 else 1
    next_ey = ey      if em < 12 else ey + 1

    with duckdb.connect(str(CITIBIKE_DB), read_only=True) as conn:
        df = conn.execute(f"""
            SELECT hour(started_at)      AS h,
                   start_station_id      AS ssid,
                   end_station_id        AS esid,
                   avg(start_lat)        AS slat,
                   avg(start_lng)        AS slng,
                   avg(end_lat)          AS elat,
                   avg(end_lng)          AS elng,
                   count(*)              AS n
            FROM rides
            WHERE start_station_id IS NOT NULL AND end_station_id IS NOT NULL
              AND start_lat BETWEEN 40.4 AND 41.0 AND start_lng BETWEEN -74.3 AND -73.7
              AND end_lat   BETWEEN 40.4 AND 41.0 AND end_lng   BETWEEN -74.3 AND -73.7
              AND started_at >= '{sy}-{sm:02d}-01'
              AND started_at  < '{next_ey}-{next_em:02d}-01'
            GROUP BY h, start_station_id, end_station_id
        """).df()

    by_hour: dict = {}
    for h_val, grp in df.groupby("h"):
        by_hour[str(int(h_val))] = [
            {"ssid": str(r["ssid"]), "esid": str(r["esid"]),
             "slat": round(float(r["slat"]), 5), "slng": round(float(r["slng"]), 5),
             "elat": round(float(r["elat"]), 5), "elng": round(float(r["elng"]), 5),
             "n":    int(r["n"])}
            for _, r in grp.iterrows()
            if not any(pd.isna([r["slat"], r["slng"], r["elat"], r["elng"]]))
        ]
    return json.dumps({"by_hour": by_hour})


@app.get("/api/citibike_hourly")
def citibike_hourly_endpoint(
    start_year:  int = Query(ge=2019, le=2030),
    start_month: int = Query(ge=1,    le=12),
    end_year:    int = Query(ge=2019, le=2030),
    end_month:   int = Query(ge=1,    le=12),
):
    if (end_year, end_month) < (start_year, start_month):
        raise HTTPException(status_code=422, detail="end must be >= start")
    key = (start_year, start_month, end_year, end_month)
    if key not in _citibike_hourly_cache:
        try:
            _citibike_hourly_cache[key] = _build_citibike_hourly(*key)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return Response(content=_citibike_hourly_cache[key], media_type="application/json")


# ── Hourly OD flows (for Pulse Map arrows) ────────────────────────────────────
@lru_cache(maxsize=20)
def _query_hourly_od_month(year: int, month: int) -> str:
    """Return (h, pu, do, n) rows — one per OD pair per hour — for arrow drawing."""
    path = RECORDS_DIR / f"yellow_taxi_{year}_{month:02d}.parquet"
    if not path.exists():
        return ""
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    sql = f"""
        SELECT hour(tpep_pickup_datetime) AS h,
               PULocationID               AS pu,
               DOLocationID               AS do,
               count(*)                   AS n
        FROM read_parquet('{path}')
        WHERE trip_distance > 0 AND fare_amount > 0
          AND tpep_pickup_datetime >= '{year}-{month:02d}-01'
          AND tpep_pickup_datetime  < '{next_year}-{next_month:02d}-01'
        GROUP BY h, PULocationID, DOLocationID
    """
    with duckdb.connect() as conn:
        return conn.execute(sql).df().to_json(orient="records")


def _build_hourly_od(sy: int, sm: int, ey: int, em: int) -> str:
    # acc: hour → {(pu,do) → n}
    acc: dict = {}
    for year, month in _month_range(sy, sm, ey, em):
        raw = _query_hourly_od_month(year, month)
        if not raw:
            continue
        for row in json.loads(raw):
            h = str(int(row["h"]))
            pu = int(row["pu"])
            do = int(row["do"])
            n = int(row["n"])
            acc.setdefault(h, {})
            key = f"{pu}:{do}"
            acc[h][key] = acc[h].get(key, 0) + n

    by_hour: dict = {}
    for h, pairs in acc.items():
        by_hour[h] = [
            {"pu": int(k.split(":")[0]), "do": int(k.split(":")[1]), "n": v}
            for k, v in pairs.items()
        ]
    return json.dumps({"by_hour": by_hour})


_hourly_od_cache: dict = {}


@app.get("/api/hourly_od")
def hourly_od_endpoint(
    start_year: int = Query(ge=2019, le=2030),
    start_month: int = Query(ge=1, le=12),
    end_year: int = Query(ge=2019, le=2030),
    end_month: int = Query(ge=1, le=12),
):
    if (end_year, end_month) < (start_year, start_month):
        raise HTTPException(status_code=422, detail="end must be >= start")
    key = (start_year, start_month, end_year, end_month)
    if key not in _hourly_od_cache:
        try:
            _hourly_od_cache[key] = _build_hourly_od(*key)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return Response(content=_hourly_od_cache[key], media_type="application/json")


# ── Hourly pressure (per-zone pickups/dropoffs by hour) ───────────────────────
@lru_cache(maxsize=20)
def _query_hourly_pressure_month(year: int, month: int) -> str:
    """Return per-zone pickups and dropoffs grouped by hour for one month."""
    path = RECORDS_DIR / f"yellow_taxi_{year}_{month:02d}.parquet"
    if not path.exists():
        return ""
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    sql = f"""
        SELECT h, zone, sum(pickups) AS pickups, sum(dropoffs) AS dropoffs
        FROM (
            SELECT hour(tpep_pickup_datetime) AS h, PULocationID AS zone,
                   count(*) AS pickups, 0 AS dropoffs
            FROM read_parquet('{path}')
            WHERE trip_distance > 0 AND fare_amount > 0
              AND tpep_pickup_datetime >= '{year}-{month:02d}-01'
              AND tpep_pickup_datetime  < '{next_year}-{next_month:02d}-01'
            GROUP BY h, PULocationID
            UNION ALL
            SELECT hour(tpep_pickup_datetime) AS h, DOLocationID AS zone,
                   0 AS pickups, count(*) AS dropoffs
            FROM read_parquet('{path}')
            WHERE trip_distance > 0 AND fare_amount > 0
              AND tpep_pickup_datetime >= '{year}-{month:02d}-01'
              AND tpep_pickup_datetime  < '{next_year}-{next_month:02d}-01'
            GROUP BY h, DOLocationID
        )
        GROUP BY h, zone
    """
    with duckdb.connect() as conn:
        return conn.execute(sql).df().to_json(orient="records")


def _build_hourly_pressure(sy: int, sm: int, ey: int, em: int) -> str:
    acc: dict = {}  # h → zone → {"pickups": int, "dropoffs": int}
    for year, month in _month_range(sy, sm, ey, em):
        raw = _query_hourly_pressure_month(year, month)
        if not raw:
            continue
        for row in json.loads(raw):
            h    = str(int(row["h"]))
            zone = str(int(row["zone"]))
            acc.setdefault(h, {}).setdefault(zone, {"pickups": 0, "dropoffs": 0})
            acc[h][zone]["pickups"]  += int(row["pickups"])
            acc[h][zone]["dropoffs"] += int(row["dropoffs"])
    by_hour = {
        h: {
            zone: {
                "net":   v["pickups"] - v["dropoffs"],
                "total": v["pickups"] + v["dropoffs"],
            }
            for zone, v in zones.items()
        }
        for h, zones in acc.items()
    }
    return json.dumps({"by_hour": by_hour})


_hourly_pressure_cache: dict = {}


@app.get("/api/hourly_pressure")
def hourly_pressure_endpoint(
    start_year:  int = Query(ge=2019, le=2030),
    start_month: int = Query(ge=1,    le=12),
    end_year:    int = Query(ge=2019, le=2030),
    end_month:   int = Query(ge=1,    le=12),
):
    if (end_year, end_month) < (start_year, start_month):
        raise HTTPException(status_code=422, detail="end must be >= start")
    key = (start_year, start_month, end_year, end_month)
    if key not in _hourly_pressure_cache:
        try:
            _hourly_pressure_cache[key] = _build_hourly_pressure(*key)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return Response(content=_hourly_pressure_cache[key], media_type="application/json")


# ── Pressure (aggregate net outflow per zone, used for legend scale) ───────────
@lru_cache(maxsize=20)
def _query_pressure_month(year: int, month: int) -> str:
    """Return per-zone pickups and dropoffs for the month."""
    path = RECORDS_DIR / f"yellow_taxi_{year}_{month:02d}.parquet"
    if not path.exists():
        return ""
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    sql = f"""
        SELECT zone, sum(pickups) AS pickups, sum(dropoffs) AS dropoffs
        FROM (
            SELECT PULocationID AS zone, count(*) AS pickups, 0 AS dropoffs
            FROM read_parquet('{path}')
            WHERE trip_distance > 0 AND fare_amount > 0
              AND tpep_pickup_datetime >= '{year}-{month:02d}-01'
              AND tpep_pickup_datetime  < '{next_year}-{next_month:02d}-01'
            GROUP BY PULocationID
            UNION ALL
            SELECT DOLocationID AS zone, 0 AS pickups, count(*) AS dropoffs
            FROM read_parquet('{path}')
            WHERE trip_distance > 0 AND fare_amount > 0
              AND tpep_pickup_datetime >= '{year}-{month:02d}-01'
              AND tpep_pickup_datetime  < '{next_year}-{next_month:02d}-01'
            GROUP BY DOLocationID
        )
        GROUP BY zone
    """
    with duckdb.connect() as conn:
        return conn.execute(sql).df().to_json(orient="records")


def _build_pressure(sy: int, sm: int, ey: int, em: int) -> str:
    acc: dict = {}  # zone → {"pickups": int, "dropoffs": int}
    for year, month in _month_range(sy, sm, ey, em):
        raw = _query_pressure_month(year, month)
        if not raw:
            continue
        for row in json.loads(raw):
            zone = str(int(row["zone"]))
            if zone not in acc:
                acc[zone] = {"pickups": 0, "dropoffs": 0}
            acc[zone]["pickups"]  += int(row["pickups"])
            acc[zone]["dropoffs"] += int(row["dropoffs"])
    zones = {
        zone: {
            "net":   v["pickups"] - v["dropoffs"],
            "total": v["pickups"] + v["dropoffs"],
        }
        for zone, v in acc.items()
    }
    return json.dumps({"zones": zones})


_pressure_cache: dict = {}


@app.get("/api/pressure")
def pressure_endpoint(
    start_year: int = Query(ge=2019, le=2030),
    start_month: int = Query(ge=1, le=12),
    end_year: int = Query(ge=2019, le=2030),
    end_month: int = Query(ge=1, le=12),
):
    if (end_year, end_month) < (start_year, start_month):
        raise HTTPException(status_code=422, detail="end must be >= start")
    key = (start_year, start_month, end_year, end_month)
    if key not in _pressure_cache:
        try:
            _pressure_cache[key] = _build_pressure(*key)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return Response(content=_pressure_cache[key], media_type="application/json")


# ── Temporal analysis ─────────────────────────────────────────────────────────
@lru_cache(maxsize=1000)
def _query_temporal_month(zone_id: int, year: int, month: int) -> str:
    """Return (h, dow, mo, n, total_fare) rows for one zone+month as JSON."""
    path = RECORDS_DIR / f"yellow_taxi_{year}_{month:02d}.parquet"
    if not path.exists():
        return ""
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    sql = f"""
        SELECT
            hour(tpep_pickup_datetime)      AS h,
            dayofweek(tpep_pickup_datetime) AS dow,
            month(tpep_pickup_datetime)     AS mo,
            count(*)                        AS n,
            sum(fare_amount)                AS total_fare
        FROM read_parquet('{path}')
        WHERE PULocationID = {zone_id}
          AND trip_distance > 0 AND fare_amount > 0
          AND tpep_pickup_datetime >= '{year}-{month:02d}-01'
          AND tpep_pickup_datetime  < '{next_year}-{next_month:02d}-01'
        GROUP BY h, dow, mo
    """
    with duckdb.connect() as conn:
        return conn.execute(sql).df().to_json(orient="records")


@lru_cache(maxsize=1000)
def _query_od_fare_month(zone_id: int, year: int, month: int) -> str:
    """Return (dest, h, dow, mo, n, total_fare) for each OD pair × time bucket."""
    path = RECORDS_DIR / f"yellow_taxi_{year}_{month:02d}.parquet"
    if not path.exists():
        return ""
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    sql = f"""
        SELECT DOLocationID                    AS dest,
               hour(tpep_pickup_datetime)      AS h,
               dayofweek(tpep_pickup_datetime) AS dow,
               month(tpep_pickup_datetime)     AS mo,
               count(*)                        AS n,
               sum(fare_amount)                AS total_fare
        FROM read_parquet('{path}')
        WHERE PULocationID = {zone_id}
          AND trip_distance > 0 AND fare_amount > 0
          AND tpep_pickup_datetime >= '{year}-{month:02d}-01'
          AND tpep_pickup_datetime  < '{next_year}-{next_month:02d}-01'
        GROUP BY dest, h, dow, mo
    """
    with duckdb.connect() as conn:
        return conn.execute(sql).df().to_json(orient="records")


def _build_temporal(zone_id: int, sy: int, sm: int, ey: int, em: int) -> str:
    result: dict = {
        "taxi_by_hour": [],
        "taxi_by_dow": [],
        "taxi_by_month": [],
        "bike_by_hour": [],
        "bike_by_dow": [],
        "bike_by_month": [],
        "od_top": [],
        "od_fare_by_hour": {},
        "od_fare_by_dow": {},
        "od_fare_by_month": {},
    }

    usage_frames, od_frames = [], []
    for y, m in _month_range(sy, sm, ey, em):
        raw_u = _query_temporal_month(zone_id, y, m)
        if raw_u:
            usage_frames.append(pd.read_json(raw_u))
        raw_od = _query_od_fare_month(zone_id, y, m)
        if raw_od:
            od_frames.append(pd.read_json(raw_od))

    # ── Taxi usage ────────────────────────────────────────────────────────
    if usage_frames:
        df = pd.concat(usage_frames, ignore_index=True)

        def taxi_agg(col: str) -> list:
            g = df.groupby(col, as_index=False).agg(n=("n", "sum"))
            return g[[col, "n"]].rename(columns={col: "x"}).to_dict("records")

        result["taxi_by_hour"] = taxi_agg("h")
        result["taxi_by_dow"] = taxi_agg("dow")
        result["taxi_by_month"] = taxi_agg("mo")

    # ── Taxi fare per OD pair (top 5 destinations) ────────────────────────
    if od_frames:
        odf = pd.concat(od_frames, ignore_index=True)
        top_dests = odf.groupby("dest")["n"].sum().nlargest(5).index.tolist()
        result["od_top"] = [int(d) for d in top_dests]

        def od_fare_agg(col: str) -> dict:
            out = {}
            for dest in top_dests:
                sub = (
                    odf[odf["dest"] == dest]
                    .groupby(col, as_index=False)
                    .agg(n=("n", "sum"), total_fare=("total_fare", "sum"))
                )
                sub["avg_fare"] = (sub["total_fare"] / sub["n"]).round(2)
                out[str(int(dest))] = (
                    sub[[col, "avg_fare"]].rename(columns={col: "x"}).to_dict("records")
                )
            return out

        result["od_fare_by_hour"] = od_fare_agg("h")
        result["od_fare_by_dow"] = od_fare_agg("dow")
        result["od_fare_by_month"] = od_fare_agg("mo")

    # ── Citibike usage ────────────────────────────────────────────────────
    if CITIBIKE_DB.exists() and zone_id in _ZONE_SIDS:
        sids = _ZONE_SIDS[zone_id]
        sid_list = ", ".join(f"'{s}'" for s in sids)
        next_em = em + 1 if em < 12 else 1
        next_ey = ey if em < 12 else ey + 1
        try:
            cb_conn = duckdb.connect(str(CITIBIKE_DB), read_only=True)
            cb_df = cb_conn.execute(
                f"""
                SELECT hour(started_at)      AS h,
                       dayofweek(started_at) AS dow,
                       month(started_at)     AS mo,
                       count(*)              AS n
                FROM rides
                WHERE CAST(start_station_id AS VARCHAR) IN ({sid_list})
                  AND started_at >= '{sy}-{sm:02d}-01'
                  AND started_at  < '{next_ey}-{next_em:02d}-01'
                GROUP BY h, dow, mo
            """
            ).df()
            cb_conn.close()

            def bike_agg(col: str) -> list:
                g = cb_df.groupby(col, as_index=False).agg(n=("n", "sum"))
                return g[[col, "n"]].rename(columns={col: "x"}).to_dict("records")

            result["bike_by_hour"] = bike_agg("h")
            result["bike_by_dow"] = bike_agg("dow")
            result["bike_by_month"] = bike_agg("mo")
        except Exception as e:
            print(f"  Citibike temporal query failed for zone {zone_id}: {e}")

    return json.dumps(result)


_temporal_cache: dict = {}


@app.get("/api/temporal")
def temporal(
    zone_id: int = Query(ge=1, le=300),
    start_year: int = Query(ge=2019, le=2030),
    start_month: int = Query(ge=1, le=12),
    end_year: int = Query(ge=2019, le=2030),
    end_month: int = Query(ge=1, le=12),
):
    if (end_year, end_month) < (start_year, start_month):
        raise HTTPException(status_code=422, detail="end must be >= start")
    key = (zone_id, start_year, start_month, end_year, end_month)
    if key not in _temporal_cache:
        try:
            _temporal_cache[key] = _build_temporal(*key)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return Response(content=_temporal_cache[key], media_type="application/json")
