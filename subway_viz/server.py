import json
from functools import lru_cache

import duckdb
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pathlib import Path
from shapely.geometry import shape
from typing import Optional

app = FastAPI()

SUBWAY_DB = Path(__file__).parent.parent / "subway_2025"
CITIBIKE_DB = Path(__file__).parent.parent / "citibike_data.duckdb"
TAXI_PARQUET_GLOB = str(Path(__file__).parent.parent / "data/yellow_taxi_records/yellow_taxi_*.parquet")
TAXI_ZONES_FILE = Path(__file__).parent.parent / "taxi_viz/zones_cache.geojson"


def get_con(dataset: str):
    path = SUBWAY_DB if dataset == "subway" else CITIBIKE_DB
    return duckdb.connect(str(path), read_only=True)


# ── Subway endpoints ──


@app.get("/api/subway/stations")
def subway_stations():
    con = get_con("subway")
    df = con.execute("""
        SELECT DISTINCT
            "Origin Station Complex ID" AS station_id,
            "Origin Station Complex Name" AS station_name,
            "Origin Latitude" AS lat,
            "Origin Longitude" AS lng
        FROM subway_data
        WHERE "Origin Latitude" IS NOT NULL
        ORDER BY station_name
    """).fetchdf()
    con.close()
    return df.to_dict(orient="records")


def _subway_where(direction, ids, month, day_of_week, hour_start, hour_end):
    id_col = "Destination Station Complex ID" if direction == "dest" else "Origin Station Complex ID"
    placeholders = ",".join(str(i) for i in ids)
    clauses = [f'"{id_col}" IN ({placeholders})']
    if month is not None:
        clauses.append(f"Month = {month}")
    if day_of_week == 'Weekday':
        clauses.append("\"Day of Week\" IN ('Monday','Tuesday','Wednesday','Thursday','Friday')")
    elif day_of_week == 'Weekend':
        clauses.append("\"Day of Week\" IN ('Saturday','Sunday')")
    elif day_of_week is not None:
        clauses.append(f"\"Day of Week\" = '{day_of_week}'")
    if hour_start is not None and hour_end is not None:
        if hour_start <= hour_end:
            clauses.append(f"\"Hour of Day\" >= {hour_start} AND \"Hour of Day\" <= {hour_end}")
        else:
            clauses.append(f"(\"Hour of Day\" >= {hour_start} OR \"Hour of Day\" <= {hour_end})")
    return " AND ".join(clauses)


@app.get("/api/subway/origins")
def subway_origins(
    dest_ids: str = Query(...),
    month: Optional[int] = None,
    day_of_week: Optional[str] = None,
    hour_start: Optional[int] = None,
    hour_end: Optional[int] = None,
):
    ids = [int(x) for x in dest_ids.split(",") if x.strip()]
    if not ids:
        return []
    con = get_con("subway")
    where = _subway_where("dest", ids, month, day_of_week, hour_start, hour_end)
    df = con.execute(f"""
        SELECT
            "Origin Station Complex ID" AS station_id,
            "Origin Station Complex Name" AS station_name,
            "Origin Latitude" AS lat,
            "Origin Longitude" AS lng,
            SUM("Estimated Average Ridership") AS total_ridership
        FROM subway_data
        WHERE {where}
        GROUP BY 1, 2, 3, 4
        ORDER BY total_ridership DESC
    """).fetchdf()
    con.close()
    return df.to_dict(orient="records")


@app.get("/api/subway/destinations")
def subway_destinations(
    origin_ids: str = Query(...),
    month: Optional[int] = None,
    day_of_week: Optional[str] = None,
    hour_start: Optional[int] = None,
    hour_end: Optional[int] = None,
):
    ids = [int(x) for x in origin_ids.split(",") if x.strip()]
    if not ids:
        return []
    con = get_con("subway")
    where = _subway_where("origin", ids, month, day_of_week, hour_start, hour_end)
    df = con.execute(f"""
        SELECT
            "Destination Station Complex ID" AS station_id,
            "Destination Station Complex Name" AS station_name,
            "Destination Latitude" AS lat,
            "Destination Longitude" AS lng,
            SUM("Estimated Average Ridership") AS total_ridership
        FROM subway_data
        WHERE {where}
        GROUP BY 1, 2, 3, 4
        ORDER BY total_ridership DESC
    """).fetchdf()
    con.close()
    return df.to_dict(orient="records")


# ── Citibike endpoints ──


@app.get("/api/citibike/stations")
def citibike_stations():
    con = get_con("citibike")
    df = con.execute("""
        SELECT
            start_station_id AS station_id,
            start_station_name AS station_name,
            AVG(start_lat) AS lat,
            AVG(start_lng) AS lng
        FROM rides
        WHERE start_station_id IS NOT NULL
            AND start_lat IS NOT NULL
        GROUP BY 1, 2
        ORDER BY station_name
    """).fetchdf()
    con.close()
    return df.to_dict(orient="records")


def _citibike_where(direction, ids, month, day_of_week, hour_start, hour_end, member_casual):
    id_col = "end_station_id" if direction == "dest" else "start_station_id"
    quoted = ",".join(f"'{i}'" for i in ids)
    clauses = [f"{id_col} IN ({quoted})"]
    if month is not None:
        clauses.append(f"MONTH(started_at) = {month}")
    if day_of_week == 'Weekday':
        clauses.append("DAYNAME(started_at) IN ('Monday','Tuesday','Wednesday','Thursday','Friday')")
    elif day_of_week == 'Weekend':
        clauses.append("DAYNAME(started_at) IN ('Saturday','Sunday')")
    elif day_of_week is not None:
        clauses.append(f"DAYNAME(started_at) = '{day_of_week}'")
    if hour_start is not None and hour_end is not None:
        if hour_start <= hour_end:
            clauses.append(f"HOUR(started_at) >= {hour_start} AND HOUR(started_at) <= {hour_end}")
        else:
            clauses.append(f"(HOUR(started_at) >= {hour_start} OR HOUR(started_at) <= {hour_end})")
    if member_casual:
        clauses.append(f"member_casual = '{member_casual}'")
    return " AND ".join(clauses)


@app.get("/api/citibike/origins")
def citibike_origins(
    dest_ids: str = Query(...),
    month: Optional[int] = None,
    day_of_week: Optional[str] = None,
    hour_start: Optional[int] = None,
    hour_end: Optional[int] = None,
    member_casual: Optional[str] = None,
):
    ids = [x.strip() for x in dest_ids.split(",") if x.strip()]
    if not ids:
        return []
    con = get_con("citibike")
    where = _citibike_where("dest", ids, month, day_of_week, hour_start, hour_end, member_casual)
    df = con.execute(f"""
        SELECT
            start_station_id AS station_id,
            start_station_name AS station_name,
            AVG(start_lat) AS lat,
            AVG(start_lng) AS lng,
            COUNT(*) AS total_ridership
        FROM rides
        WHERE {where}
            AND start_station_id IS NOT NULL
            AND start_lat IS NOT NULL
        GROUP BY 1, 2
        ORDER BY total_ridership DESC
    """).fetchdf()
    con.close()
    return df.to_dict(orient="records")


@app.get("/api/citibike/destinations")
def citibike_destinations(
    origin_ids: str = Query(...),
    month: Optional[int] = None,
    day_of_week: Optional[str] = None,
    hour_start: Optional[int] = None,
    hour_end: Optional[int] = None,
    member_casual: Optional[str] = None,
):
    ids = [x.strip() for x in origin_ids.split(",") if x.strip()]
    if not ids:
        return []
    con = get_con("citibike")
    where = _citibike_where("origin", ids, month, day_of_week, hour_start, hour_end, member_casual)
    df = con.execute(f"""
        SELECT
            end_station_id AS station_id,
            end_station_name AS station_name,
            AVG(end_lat) AS lat,
            AVG(end_lng) AS lng,
            COUNT(*) AS total_ridership
        FROM rides
        WHERE {where}
            AND end_station_id IS NOT NULL
            AND end_lat IS NOT NULL
        GROUP BY 1, 2
        ORDER BY total_ridership DESC
    """).fetchdf()
    con.close()
    return df.to_dict(orient="records")


# ── Taxi endpoints ──


@lru_cache(maxsize=1)
def _taxi_zone_list():
    with open(TAXI_ZONES_FILE) as f:
        gj = json.load(f)
    result = []
    for feat in gj['features']:
        props = feat['properties']
        centroid = shape(feat['geometry']).centroid
        result.append({
            'station_id': props['LocationID'],
            'station_name': f"{props['zone']} ({props['borough']})",
            'lat': centroid.y,
            'lng': centroid.x,
        })
    return result


@lru_cache(maxsize=1)
def _taxi_zone_lookup():
    return {z['station_id']: z for z in _taxi_zone_list()}


@app.get("/api/taxi/stations")
def taxi_stations():
    return _taxi_zone_list()


@app.get("/api/taxi/zones")
def taxi_zones():
    with open(TAXI_ZONES_FILE) as f:
        return json.load(f)


def _taxi_filter_clauses(month, day_of_week, hour_start, hour_end):
    clauses = ['trip_distance > 0', 'fare_amount > 0']
    if month is not None:
        clauses.append(f"MONTH(tpep_pickup_datetime) = {month}")
    if day_of_week == 'Weekday':
        clauses.append("DAYNAME(tpep_pickup_datetime) IN ('Monday','Tuesday','Wednesday','Thursday','Friday')")
    elif day_of_week == 'Weekend':
        clauses.append("DAYNAME(tpep_pickup_datetime) IN ('Saturday','Sunday')")
    elif day_of_week is not None:
        clauses.append(f"DAYNAME(tpep_pickup_datetime) = '{day_of_week}'")
    if hour_start is not None and hour_end is not None:
        if hour_start <= hour_end:
            clauses.append(f"HOUR(tpep_pickup_datetime) >= {hour_start} AND HOUR(tpep_pickup_datetime) <= {hour_end}")
        else:
            clauses.append(f"(HOUR(tpep_pickup_datetime) >= {hour_start} OR HOUR(tpep_pickup_datetime) <= {hour_end})")
    return clauses


@app.get("/api/taxi/origins")
def taxi_origins(
    dest_ids: str = Query(...),
    month: Optional[int] = None,
    day_of_week: Optional[str] = None,
    hour_start: Optional[int] = None,
    hour_end: Optional[int] = None,
):
    zone_ids = [int(x) for x in dest_ids.split(",") if x.strip()]
    if not zone_ids:
        return []
    placeholders = ",".join(str(i) for i in zone_ids)
    clauses = [f"DOLocationID IN ({placeholders})"] + _taxi_filter_clauses(month, day_of_week, hour_start, hour_end)
    where = " AND ".join(clauses)
    con = duckdb.connect()
    df = con.execute(f"""
        SELECT PULocationID AS station_id, COUNT(*) AS total_ridership
        FROM read_parquet('{TAXI_PARQUET_GLOB}')
        WHERE {where}
        GROUP BY 1
        ORDER BY total_ridership DESC
    """).fetchdf()
    con.close()
    lookup = _taxi_zone_lookup()
    return [
        {**lookup[int(row.station_id)], 'total_ridership': int(row.total_ridership)}
        for _, row in df.iterrows()
        if int(row.station_id) in lookup
    ]


@app.get("/api/taxi/destinations")
def taxi_destinations(
    origin_ids: str = Query(...),
    month: Optional[int] = None,
    day_of_week: Optional[str] = None,
    hour_start: Optional[int] = None,
    hour_end: Optional[int] = None,
):
    zone_ids = [int(x) for x in origin_ids.split(",") if x.strip()]
    if not zone_ids:
        return []
    placeholders = ",".join(str(i) for i in zone_ids)
    clauses = [f"PULocationID IN ({placeholders})"] + _taxi_filter_clauses(month, day_of_week, hour_start, hour_end)
    where = " AND ".join(clauses)
    con = duckdb.connect()
    df = con.execute(f"""
        SELECT DOLocationID AS station_id, COUNT(*) AS total_ridership
        FROM read_parquet('{TAXI_PARQUET_GLOB}')
        WHERE {where}
        GROUP BY 1
        ORDER BY total_ridership DESC
    """).fetchdf()
    con.close()
    lookup = _taxi_zone_lookup()
    return [
        {**lookup[int(row.station_id)], 'total_ridership': int(row.total_ridership)}
        for _, row in df.iterrows()
        if int(row.station_id) in lookup
    ]


@app.get("/api/subway/hourly")
def subway_hourly(
    origin_ids: Optional[str] = Query(None),
    dest_ids: Optional[str] = Query(None),
    month: Optional[int] = None,
    day_of_week: Optional[str] = None,
):
    ids_str = origin_ids or dest_ids
    if not ids_str:
        return []
    ids = [int(x) for x in ids_str.split(",") if x.strip()]
    if not ids:
        return []
    id_col = '"Origin Station Complex ID"' if origin_ids else '"Destination Station Complex ID"'
    placeholders = ",".join(str(i) for i in ids)
    clauses = [f"{id_col} IN ({placeholders})"]
    if month is not None:
        clauses.append(f"Month = {month}")
    if day_of_week == 'Weekday':
        clauses.append("\"Day of Week\" IN ('Monday','Tuesday','Wednesday','Thursday','Friday')")
    elif day_of_week == 'Weekend':
        clauses.append("\"Day of Week\" IN ('Saturday','Sunday')")
    elif day_of_week is not None:
        clauses.append(f"\"Day of Week\" = '{day_of_week}'")
    where = " AND ".join(clauses)
    con = get_con("subway")
    df = con.execute(f"""
        SELECT "Hour of Day" AS hour, SUM("Estimated Average Ridership") AS total
        FROM subway_data
        WHERE {where}
        GROUP BY 1 ORDER BY 1
    """).fetchdf()
    con.close()
    return df.to_dict(orient="records")


@app.get("/api/citibike/hourly")
def citibike_hourly(
    origin_ids: Optional[str] = Query(None),
    dest_ids: Optional[str] = Query(None),
    month: Optional[int] = None,
    day_of_week: Optional[str] = None,
    member_casual: Optional[str] = None,
):
    ids_str = origin_ids or dest_ids
    if not ids_str:
        return []
    ids = [x.strip() for x in ids_str.split(",") if x.strip()]
    if not ids:
        return []
    id_col = "start_station_id" if origin_ids else "end_station_id"
    quoted = ",".join(f"'{i}'" for i in ids)
    clauses = [f"{id_col} IN ({quoted})"]
    if month is not None:
        clauses.append(f"MONTH(started_at) = {month}")
    if day_of_week == 'Weekday':
        clauses.append("DAYNAME(started_at) IN ('Monday','Tuesday','Wednesday','Thursday','Friday')")
    elif day_of_week == 'Weekend':
        clauses.append("DAYNAME(started_at) IN ('Saturday','Sunday')")
    elif day_of_week is not None:
        clauses.append(f"DAYNAME(started_at) = '{day_of_week}'")
    if member_casual:
        clauses.append(f"member_casual = '{member_casual}'")
    where = " AND ".join(clauses)
    con = get_con("citibike")
    df = con.execute(f"""
        SELECT HOUR(started_at) AS hour, COUNT(*) AS total
        FROM rides
        WHERE {where}
        GROUP BY 1 ORDER BY 1
    """).fetchdf()
    con.close()
    return df.to_dict(orient="records")


@app.get("/api/taxi/hourly")
def taxi_hourly(
    origin_ids: Optional[str] = Query(None),
    dest_ids: Optional[str] = Query(None),
    month: Optional[int] = None,
    day_of_week: Optional[str] = None,
):
    ids_str = origin_ids or dest_ids
    if not ids_str:
        return []
    zone_ids = [int(x) for x in ids_str.split(",") if x.strip()]
    if not zone_ids:
        return []
    id_col = "PULocationID" if origin_ids else "DOLocationID"
    placeholders = ",".join(str(i) for i in zone_ids)
    clauses = [f"{id_col} IN ({placeholders})", 'trip_distance > 0', 'fare_amount > 0']
    if month is not None:
        clauses.append(f"MONTH(tpep_pickup_datetime) = {month}")
    if day_of_week == 'Weekday':
        clauses.append("DAYNAME(tpep_pickup_datetime) IN ('Monday','Tuesday','Wednesday','Thursday','Friday')")
    elif day_of_week == 'Weekend':
        clauses.append("DAYNAME(tpep_pickup_datetime) IN ('Saturday','Sunday')")
    elif day_of_week is not None:
        clauses.append(f"DAYNAME(tpep_pickup_datetime) = '{day_of_week}'")
    where = " AND ".join(clauses)
    con = duckdb.connect()
    df = con.execute(f"""
        SELECT HOUR(tpep_pickup_datetime) AS hour, COUNT(*) AS total
        FROM read_parquet('{TAXI_PARQUET_GLOB}')
        WHERE {where}
        GROUP BY 1 ORDER BY 1
    """).fetchdf()
    con.close()
    return df.to_dict(orient="records")


# ── Clustering (system-wide daily patterns) ──


@lru_cache(maxsize=128)
def _daily_matrix(dataset: str, station_id: Optional[int] = None, role: str = "either"):
    """Return (dates, matrix) where matrix is [n_days, 24] of ridership.
    station_id=None means system-wide.
    role in {'origin', 'destination', 'either'} — only used when station_id is set.
    """
    con = get_con(dataset)
    if dataset == "subway":
        where = ""
        if station_id is not None:
            if role == "origin":
                where = f'WHERE "Origin Station Complex ID" = {station_id}'
            elif role == "destination":
                where = f'WHERE "Destination Station Complex ID" = {station_id}'
            else:  # either
                where = (f'WHERE "Origin Station Complex ID" = {station_id} '
                         f'OR "Destination Station Complex ID" = {station_id}')
        df = con.execute(f"""
            SELECT DATE_TRUNC('day', Timestamp) AS date,
                   "Hour of Day" AS hour,
                   SUM("Estimated Average Ridership") AS ridership
            FROM subway_data
            {where}
            GROUP BY 1, 2
            ORDER BY 1, 2
        """).fetchdf()
    else:
        df = con.execute("""
            SELECT CAST(started_at AS DATE) AS date,
                   HOUR(started_at) AS hour,
                   COUNT(*) AS ridership
            FROM rides
            GROUP BY 1, 2
            ORDER BY 1, 2
        """).fetchdf()
    con.close()
    if len(df) == 0:
        return [], []
    pivot = df.pivot(index="date", columns="hour", values="ridership").fillna(0)
    for h in range(24):
        if h not in pivot.columns:
            pivot[h] = 0
    pivot = pivot.reindex(columns=range(24))
    dates = [d.strftime("%Y-%m-%d") for d in pivot.index]
    matrix = pivot.values.astype(float)
    return dates, matrix


@lru_cache(maxsize=128)
def _linkage(dataset: str, station_id: Optional[int] = None, role: str = "either"):
    from scipy.cluster.hierarchy import linkage
    _, matrix = _daily_matrix(dataset, station_id, role)
    if len(matrix) < 2:
        return None
    Z = linkage(matrix, method="ward")
    return Z


@app.get("/api/{dataset}/clustering")
def clustering(
    dataset: str,
    k: int = 5,
    station_id: Optional[int] = None,
    role: str = "either",
):
    from scipy.cluster.hierarchy import fcluster
    if dataset not in ("subway", "citibike"):
        return {"error": "invalid dataset"}
    if role not in ("origin", "destination", "either"):
        role = "either"
    dates, matrix = _daily_matrix(dataset, station_id, role)
    if len(dates) < 2:
        return {"dates": [], "date_to_cluster": {}, "clusters": [], "k": 0, "max_k": 0}
    Z = _linkage(dataset, station_id, role)
    k = max(1, min(k, len(dates)))
    labels = fcluster(Z, t=k, criterion="maxclust")

    clusters = {}
    for i, lbl in enumerate(labels):
        clusters.setdefault(int(lbl), []).append(i)

    # Build cluster summaries
    result_clusters = []
    for lbl, idxs in clusters.items():
        mean_pattern = matrix[idxs].mean(axis=0).tolist()
        result_clusters.append({
            "id": lbl,
            "size": len(idxs),
            "mean_pattern": mean_pattern,
            "dates": [dates[i] for i in idxs],
        })
    # Sort clusters by total daily ridership (so colors are consistent by magnitude)
    result_clusters.sort(key=lambda c: -sum(c["mean_pattern"]))
    # Reassign stable 1..k ids
    id_map = {c["id"]: new_id for new_id, c in enumerate(result_clusters, start=1)}
    for c in result_clusters:
        c["id"] = id_map[c["id"]]

    date_to_cluster = {}
    for c in result_clusters:
        for d in c["dates"]:
            date_to_cluster[d] = c["id"]

    return {
        "dates": dates,
        "date_to_cluster": date_to_cluster,
        "clusters": result_clusters,
        "k": k,
        "max_k": len(dates),
    }


@app.get("/clustering", response_class=HTMLResponse)
def clustering_page():
    return (Path(__file__).parent / "clustering.html").read_text()


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "index.html").read_text()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
