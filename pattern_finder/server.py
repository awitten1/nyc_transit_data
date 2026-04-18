import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

import duckdb
import numpy as np
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import pdist

app = FastAPI()

SUBWAY_DB = Path(__file__).parent.parent / "subway_2025"
CITIBIKE_DB = Path(__file__).parent.parent / "citibike_data.duckdb"
MTA_DB = Path(__file__).parent.parent / "mta_ridership.db"
TAXI_PARQUET_GLOB = str(
    Path(__file__).parent.parent / "data/yellow_taxi_records/yellow_taxi_*.parquet"
)
TAXI_ZONES_FILE = Path(__file__).parent.parent / "taxi_viz/zones_cache.geojson"

DOW_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def get_subway_con():
    return duckdb.connect(str(SUBWAY_DB), read_only=True)


def get_citibike_con():
    return duckdb.connect(str(CITIBIKE_DB), read_only=True)

def get_mta_con():
    return duckdb.connect(str(MTA_DB), read_only=True)


# ── Clustering algorithms (pure numpy) ───────────────────────────────────────

def _pairwise_distances(X: np.ndarray) -> np.ndarray:
    """Euclidean distance matrix, shape (n, n)."""
    diff = X[:, None] - X[None]          # (n, n, d)
    return np.sqrt((diff ** 2).sum(axis=2))


# K-Means with k-means++ init
def _kmeans_plusplus_init(X: np.ndarray, k: int, rng: np.random.RandomState):
    n = len(X)
    centers = [X[rng.randint(0, n)].copy()]
    for _ in range(k - 1):
        D = np.array([min(np.sum((x - c) ** 2) for c in centers) for x in X])
        centers.append(X[rng.choice(n, p=D / D.sum())].copy())
    return np.array(centers)


def _kmeans(X: np.ndarray, k: int, seed: int = 42, max_iter: int = 200):
    k = min(k, len(X))
    rng = np.random.RandomState(seed)
    centroids = _kmeans_plusplus_init(X, k, rng)
    labels = np.zeros(len(X), dtype=int)
    for _ in range(max_iter):
        dists = np.sqrt(((X[:, None] - centroids[None]) ** 2).sum(axis=2))
        new_labels = dists.argmin(axis=1)
        if np.all(new_labels == labels):
            break
        labels = new_labels
        for i in range(k):
            mask = labels == i
            if mask.any():
                centroids[i] = X[mask].mean(axis=0)
    return labels, centroids


# K-Medoids (PAM): centroids are actual data points, robust to outliers
def _kmedoids(X: np.ndarray, k: int, seed: int = 42, max_iter: int = 100):
    k = min(k, len(X))
    n = len(X)
    rng = np.random.RandomState(seed)
    D = _pairwise_distances(X)
    medoid_idx = rng.choice(n, k, replace=False)
    labels = np.zeros(n, dtype=int)

    for _ in range(max_iter):
        # Assign each point to its nearest medoid
        new_labels = D[:, medoid_idx].argmin(axis=1)
        if np.all(new_labels == labels):
            break
        labels = new_labels
        # For each cluster, pick the point that minimises total distance to all others
        for i in range(k):
            mask = np.where(labels == i)[0]
            if len(mask) == 0:
                continue
            sub = D[np.ix_(mask, mask)].sum(axis=1)
            medoid_idx[i] = mask[sub.argmin()]

    centroids = X[medoid_idx]
    return labels, centroids


# Agglomerative clustering with Ward linkage via scipy (O(n²), compiled C)
def _agglomerative(X: np.ndarray, k: int):
    k = min(k, len(X))
    Z = linkage(X, method='ward', metric='euclidean')
    labels = fcluster(Z, t=k, criterion='maxclust') - 1  # 0-indexed
    centroids = np.array([X[labels == i].mean(axis=0) for i in range(k)])
    return labels, centroids


# DBSCAN: density-based, no k needed, labels noise as -1
def _dbscan(X: np.ndarray, eps: float, min_samples: int):
    n   = len(X)
    D   = _pairwise_distances(X)
    labels   = np.full(n, -1, dtype=int)
    visited  = np.zeros(n, dtype=bool)
    cluster_id = 0

    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True
        neighbors = np.where(D[i] <= eps)[0].tolist()

        if len(neighbors) < min_samples:
            continue  # noise for now; may be reassigned

        labels[i] = cluster_id
        seed_set = list(neighbors)
        j = 0
        while j < len(seed_set):
            q = seed_set[j]
            if not visited[q]:
                visited[q] = True
                q_neighbors = np.where(D[q] <= eps)[0].tolist()
                if len(q_neighbors) >= min_samples:
                    seed_set += [nb for nb in q_neighbors if nb not in seed_set]
            if labels[q] == -1:
                labels[q] = cluster_id
            j += 1

        cluster_id += 1

    # Compute centroids for each real cluster; noise (-1) gets its own entry
    actual_k = cluster_id
    centroids = []
    for ci in range(actual_k):
        mask = labels == ci
        centroids.append(X[mask].mean(axis=0) if mask.any() else np.zeros(X.shape[1]))
    return labels, np.array(centroids) if centroids else np.empty((0, X.shape[1]))


def _normalize(X_raw: np.ndarray) -> np.ndarray:
    amp = np.abs(X_raw).max(axis=1, keepdims=True)
    amp[amp == 0] = 1.0
    return X_raw / amp


def _cluster_and_package(
    station_label: str,
    entries: dict,
    k: int,
    normalize: bool,
    mode: str,
    algorithm: str = "kmeans",
    eps: float = 50.0,
    min_samples: int = 3,
):
    """Shared: build vectors, run chosen algorithm, return JSON response."""
    if not entries:
        return {"station_label": station_label, "mode": mode, "vectors": [], "centroids": []}

    if mode == "grid":
        keys = sorted(
            entries.keys(),
            key=lambda x: (x[0], DOW_ORDER.index(x[1]) if x[1] in DOW_ORDER else 99),
        )
    else:
        keys = sorted(entries.keys())

    X_raw = np.array([entries[k_] for k_ in keys])
    X = _normalize(X_raw) if normalize else X_raw

    if algorithm == "kmedoids":
        labels, centroids_arr = _kmedoids(X, k)
    elif algorithm == "agglomerative":
        labels, centroids_arr = _agglomerative(X, k)
    elif algorithm == "dbscan":
        labels, centroids_arr = _dbscan(X, eps, min_samples)
    else:
        labels, centroids_arr = _kmeans(X, k)

    # Unique cluster ids (may include -1 for DBSCAN noise)
    unique_clusters = sorted(set(labels.tolist()))

    if mode == "grid":
        vectors = [
            {
                "month": keys[i][0],
                "dow": keys[i][1],
                "cluster": int(labels[i]),
                "net_flow_vector": [round(v, 2) for v in X_raw[i]],
            }
            for i in range(len(keys))
        ]
    else:
        vectors = [
            {
                "date": keys[i],
                "cluster": int(labels[i]),
                "net_flow_vector": [round(v, 2) for v in X_raw[i]],
            }
            for i in range(len(keys))
        ]

    centroid_list = []
    for ci in unique_clusters:
        mask = labels == ci
        centroid_vec = (
            centroids_arr[ci].tolist()
            if ci >= 0 and ci < len(centroids_arr)
            else X_raw[mask].mean(axis=0).tolist()
        )
        centroid_list.append({
            "cluster": ci,                          # -1 = DBSCAN noise
            "centroid": [round(v, 2) for v in centroid_vec],
            "count": int(mask.sum()),
        })

    return {
        "station_label": station_label,
        "mode": mode,
        "vectors": vectors,
        "centroids": centroid_list,
    }


# ── Station list endpoints ────────────────────────────────────────────────────

@app.get("/api/stations")
def stations(dataset: str = "subway"):
    if dataset == "subway":
        con = get_subway_con()
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

    if dataset == "citibike":
        con = get_citibike_con()
        df = con.execute("""
            SELECT start_station_id AS station_id, start_station_name AS station_name,
                AVG(start_lat) AS lat, AVG(start_lng) AS lng
            FROM rides
            WHERE start_station_id IS NOT NULL AND start_lat IS NOT NULL
            GROUP BY 1, 2
            ORDER BY station_name
        """).fetchdf()
        con.close()
        return df.to_dict(orient="records")

    if dataset == "taxi":
        return _taxi_zone_list()

    if dataset == "mta":
        con = get_mta_con()
        df = con.execute("""
            SELECT
                station_complex_id AS station_id,
                station_complex     AS station_name,
                AVG(latitude)       AS lat,
                AVG(longitude)      AS lng
            FROM mta_data
            WHERE transit_mode = 'subway'
              AND latitude IS NOT NULL
            GROUP BY 1, 2
            ORDER BY station_name
        """).fetchdf()
        con.close()
        return df.to_dict(orient="records")

    return []


# ── Net-flow + clustering endpoint ───────────────────────────────────────────

@app.get("/api/net_flow")
def net_flow(
    station_ids: str = Query(...),
    dataset: str = "subway",
    k: int = 5,
    normalize: bool = False,
    algorithm: str = "kmeans",   # kmeans | kmedoids | agglomerative | dbscan
    eps: float = 50.0,           # DBSCAN neighbourhood radius
    min_samples: int = 3,        # DBSCAN minimum cluster size
):
    k = max(2, min(k, 12))
    ids = [x.strip() for x in station_ids.split(",") if x.strip()]
    if not ids:
        return {"error": "no station_ids provided"}

    kw = dict(k=k, normalize=normalize, algorithm=algorithm, eps=eps, min_samples=min_samples)

    if dataset == "subway":
        return _subway_net_flow([int(i) for i in ids], **kw)
    if dataset == "mta":
        return _mta_entry_clustering(ids, **kw)
    if dataset == "citibike":
        return _citibike_net_flow(ids, **kw)
    if dataset == "taxi":
        return _taxi_net_flow([int(i) for i in ids], **kw)
    return {"error": "unknown dataset"}


# ── Subway ────────────────────────────────────────────────────────────────────

def _subway_net_flow(station_ids: list[int], k: int, normalize: bool, algorithm: str = "kmeans", eps: float = 50.0, min_samples: int = 3):
    con = get_subway_con()
    placeholders = ",".join(str(i) for i in station_ids)

    # Build label from station names
    names_df = con.execute(f"""
        SELECT DISTINCT "Origin Station Complex ID" AS id,
                        "Origin Station Complex Name" AS name
        FROM subway_data
        WHERE "Origin Station Complex ID" IN ({placeholders})
    """).fetchdf()
    name_map = dict(zip(names_df["id"].tolist(), names_df["name"].tolist()))
    if len(station_ids) == 1:
        label = name_map.get(station_ids[0], str(station_ids[0]))
    else:
        label = f"{len(station_ids)} stations: " + ", ".join(
            name_map.get(i, str(i)) for i in station_ids[:3]
        ) + ("…" if len(station_ids) > 3 else "")

    df = con.execute(f"""
        WITH inflow AS (
            SELECT Month, "Day of Week" AS dow, "Hour of Day" AS hour,
                SUM("Estimated Average Ridership") AS inflow
            FROM subway_data
            WHERE "Destination Station Complex ID" IN ({placeholders})
            GROUP BY 1, 2, 3
        ),
        outflow AS (
            SELECT Month, "Day of Week" AS dow, "Hour of Day" AS hour,
                SUM("Estimated Average Ridership") AS outflow
            FROM subway_data
            WHERE "Origin Station Complex ID" IN ({placeholders})
            GROUP BY 1, 2, 3
        ),
        all_months AS (SELECT DISTINCT Month AS month FROM subway_data),
        all_dows   AS (SELECT DISTINCT "Day of Week" AS dow FROM subway_data),
        all_hours  AS (SELECT DISTINCT "Hour of Day" AS hour FROM subway_data),
        combos AS (
            SELECT m.month, d.dow, h.hour
            FROM all_months m CROSS JOIN all_dows d CROSS JOIN all_hours h
        )
        SELECT c.month, c.dow, c.hour,
            COALESCE(i.inflow, 0) - COALESCE(o.outflow, 0) AS net_flow
        FROM combos c
        LEFT JOIN inflow  i ON i.Month = c.month AND i.dow = c.dow AND i.hour = c.hour
        LEFT JOIN outflow o ON o.Month = c.month AND o.dow = c.dow AND o.hour = c.hour
        ORDER BY c.month, c.dow, c.hour
    """).fetchdf()
    con.close()

    entries: dict[tuple, list] = {}
    for _, row in df.iterrows():
        key = (int(row["month"]), str(row["dow"]))
        if key not in entries:
            entries[key] = [0.0] * 24
        h = int(row["hour"])
        if 0 <= h <= 23:
            entries[key][h] = float(row["net_flow"])

    return _cluster_and_package(label, entries, k, normalize, "grid", algorithm, eps, min_samples)


# ── MTA ridership (entry counts, actual dates) ───────────────────────────────

def _mta_entry_clustering(station_ids: list[str], k: int, normalize: bool, algorithm: str = "kmeans", eps: float = 50.0, min_samples: int = 3):
    con = get_mta_con()
    quoted = ",".join(f"'{i}'" for i in station_ids)

    # Station label
    names_df = con.execute(f"""
        SELECT station_complex_id AS id, station_complex AS name
        FROM mta_data
        WHERE station_complex_id IN ({quoted}) AND transit_mode = 'subway'
        GROUP BY 1, 2
    """).fetchdf()
    name_map = dict(zip(names_df["id"].tolist(), names_df["name"].tolist()))
    if len(station_ids) == 1:
        label = name_map.get(station_ids[0], station_ids[0])
    else:
        label = f"{len(station_ids)} stations: " + ", ".join(
            name_map.get(i, i) for i in station_ids[:3]
        ) + ("…" if len(station_ids) > 3 else "")

    # Sum entries across all fare classes per (date, hour) for the selected stations
    df = con.execute(f"""
        WITH daily_hourly AS (
            SELECT
                CAST(transit_timestamp AS DATE)  AS date,
                HOUR(transit_timestamp)          AS hour,
                SUM(TRY_CAST(ridership AS INTEGER)) AS entries
            FROM mta_data
            WHERE station_complex_id IN ({quoted})
              AND transit_mode = 'subway'
            GROUP BY 1, 2
        ),
        all_dates AS (
            SELECT DISTINCT CAST(transit_timestamp AS DATE) AS date
            FROM mta_data
            WHERE station_complex_id IN ({quoted}) AND transit_mode = 'subway'
        ),
        hours AS (SELECT unnest(range(24)) AS hour),
        combos AS (SELECT d.date, h.hour FROM all_dates d CROSS JOIN hours h)
        SELECT c.date, c.hour, COALESCE(dh.entries, 0) AS entries
        FROM combos c
        LEFT JOIN daily_hourly dh ON dh.date = c.date AND dh.hour = c.hour
        ORDER BY c.date, c.hour
    """).fetchdf()
    con.close()

    entries: dict[str, list] = {}
    for _, row in df.iterrows():
        date_str = str(row["date"])[:10]
        if date_str not in entries:
            entries[date_str] = [0.0] * 24
        h = int(row["hour"])
        if 0 <= h <= 23:
            entries[date_str][h] = float(row["entries"])

    return _cluster_and_package(label, entries, k, normalize, "calendar", algorithm, eps, min_samples)


# ── Citibike ──────────────────────────────────────────────────────────────────

def _citibike_net_flow(station_ids: list[str], k: int, normalize: bool, algorithm: str = "kmeans", eps: float = 50.0, min_samples: int = 3):
    con = get_citibike_con()
    quoted = ",".join(f"'{i}'" for i in station_ids)

    names_df = con.execute(f"""
        SELECT start_station_id AS id, start_station_name AS name FROM rides
        WHERE start_station_id IN ({quoted}) AND start_station_name IS NOT NULL
        GROUP BY 1, 2
    """).fetchdf()
    name_map = dict(zip(names_df["id"].tolist(), names_df["name"].tolist()))
    if len(station_ids) == 1:
        label = name_map.get(station_ids[0], station_ids[0])
    else:
        label = f"{len(station_ids)} stations: " + ", ".join(
            name_map.get(i, i) for i in station_ids[:3]
        ) + ("…" if len(station_ids) > 3 else "")

    cols = [r[0] for r in con.execute("DESCRIBE rides").fetchall()]
    has_ended_at = "ended_at" in cols

    if has_ended_at:
        df = con.execute(f"""
            WITH inflow AS (
                SELECT DATE_TRUNC('day', ended_at) AS date,
                    HOUR(ended_at) AS hour, COUNT(*) AS inflow
                FROM rides
                WHERE end_station_id IN ({quoted}) AND ended_at IS NOT NULL
                GROUP BY 1, 2
            ),
            outflow AS (
                SELECT DATE_TRUNC('day', started_at) AS date,
                    HOUR(started_at) AS hour, COUNT(*) AS outflow
                FROM rides
                WHERE start_station_id IN ({quoted}) AND started_at IS NOT NULL
                GROUP BY 1, 2
            ),
            all_dates AS (
                SELECT DISTINCT DATE_TRUNC('day', started_at) AS date FROM rides
                WHERE start_station_id IN ({quoted}) OR end_station_id IN ({quoted})
            ),
            hours AS (SELECT unnest(range(24)) AS hour),
            combos AS (SELECT d.date, h.hour FROM all_dates d CROSS JOIN hours h)
            SELECT c.date, c.hour,
                COALESCE(i.inflow, 0) - COALESCE(o.outflow, 0) AS net_flow
            FROM combos c
            LEFT JOIN inflow  i ON i.date = c.date AND i.hour = c.hour
            LEFT JOIN outflow o ON o.date = c.date AND o.hour = c.hour
            ORDER BY c.date, c.hour
        """).fetchdf()
    else:
        df = con.execute(f"""
            WITH outflow AS (
                SELECT DATE_TRUNC('day', started_at) AS date,
                    HOUR(started_at) AS hour, COUNT(*) AS outflow
                FROM rides
                WHERE start_station_id IN ({quoted}) AND started_at IS NOT NULL
                GROUP BY 1, 2
            ),
            all_dates AS (
                SELECT DISTINCT DATE_TRUNC('day', started_at) AS date FROM rides
                WHERE start_station_id IN ({quoted})
            ),
            hours AS (SELECT unnest(range(24)) AS hour),
            combos AS (SELECT d.date, h.hour FROM all_dates d CROSS JOIN hours h)
            SELECT c.date, c.hour,
                -COALESCE(o.outflow, 0) AS net_flow
            FROM combos c
            LEFT JOIN outflow o ON o.date = c.date AND o.hour = c.hour
            ORDER BY c.date, c.hour
        """).fetchdf()

    con.close()

    entries: dict[str, list] = {}
    for _, row in df.iterrows():
        date_str = str(row["date"])[:10]
        if date_str not in entries:
            entries[date_str] = [0.0] * 24
        h = int(row["hour"])
        if 0 <= h <= 23:
            entries[date_str][h] = float(row["net_flow"])

    return _cluster_and_package(label, entries, k, normalize, "calendar", algorithm, eps, min_samples)


# ── Taxi ──────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _taxi_zone_list():
    with open(TAXI_ZONES_FILE) as f:
        gj = json.load(f)
    from shapely.geometry import shape
    result = []
    for feat in gj["features"]:
        props = feat["properties"]
        centroid = shape(feat["geometry"]).centroid
        result.append(
            {
                "station_id": props["LocationID"],
                "station_name": f"{props['zone']} ({props['borough']})",
                "lat": centroid.y,
                "lng": centroid.x,
            }
        )
    return result


def _taxi_net_flow(zone_ids: list[int], k: int, normalize: bool, algorithm: str = "kmeans", eps: float = 50.0, min_samples: int = 3):
    lookup = {z["station_id"]: z for z in _taxi_zone_list()}
    if len(zone_ids) == 1:
        label = lookup.get(zone_ids[0], {}).get("station_name", str(zone_ids[0]))
    else:
        label = f"{len(zone_ids)} zones: " + ", ".join(
            lookup.get(i, {}).get("station_name", str(i)) for i in zone_ids[:3]
        ) + ("…" if len(zone_ids) > 3 else "")

    placeholders = ",".join(str(i) for i in zone_ids)
    con = duckdb.connect()
    df = con.execute(f"""
        WITH inflow AS (
            SELECT DATE_TRUNC('day', tpep_dropoff_datetime) AS date,
                HOUR(tpep_dropoff_datetime) AS hour, COUNT(*) AS inflow
            FROM read_parquet('{TAXI_PARQUET_GLOB}')
            WHERE DOLocationID IN ({placeholders})
                AND trip_distance > 0 AND fare_amount > 0
                AND tpep_dropoff_datetime IS NOT NULL
            GROUP BY 1, 2
        ),
        outflow AS (
            SELECT DATE_TRUNC('day', tpep_pickup_datetime) AS date,
                HOUR(tpep_pickup_datetime) AS hour, COUNT(*) AS outflow
            FROM read_parquet('{TAXI_PARQUET_GLOB}')
            WHERE PULocationID IN ({placeholders})
                AND trip_distance > 0 AND fare_amount > 0
                AND tpep_pickup_datetime IS NOT NULL
            GROUP BY 1, 2
        ),
        all_dates AS (
            SELECT DISTINCT DATE_TRUNC('day', tpep_pickup_datetime) AS date
            FROM read_parquet('{TAXI_PARQUET_GLOB}')
            WHERE (PULocationID IN ({placeholders}) OR DOLocationID IN ({placeholders}))
                AND trip_distance > 0 AND fare_amount > 0
        ),
        hours AS (SELECT unnest(range(24)) AS hour),
        combos AS (SELECT d.date, h.hour FROM all_dates d CROSS JOIN hours h)
        SELECT c.date, c.hour,
            COALESCE(i.inflow, 0) - COALESCE(o.outflow, 0) AS net_flow
        FROM combos c
        LEFT JOIN inflow  i ON i.date = c.date AND i.hour = c.hour
        LEFT JOIN outflow o ON o.date = c.date AND o.hour = c.hour
        ORDER BY c.date, c.hour
    """).fetchdf()
    con.close()

    entries: dict[str, list] = {}
    for _, row in df.iterrows():
        date_str = str(row["date"])[:10]
        if date_str not in entries:
            entries[date_str] = [0.0] * 24
        h = int(row["hour"])
        if 0 <= h <= 23:
            entries[date_str][h] = float(row["net_flow"])

    return _cluster_and_package(label, entries, k, normalize, "calendar", algorithm, eps, min_samples)


# ── Per-dataset entry-fetch helpers (used by /api/multi_flow) ────────────────

def _fetch_mta_entries(station_ids: list[str]) -> dict[str, list]:
    con = get_mta_con()
    quoted = ",".join(f"'{i}'" for i in station_ids)
    df = con.execute(f"""
        WITH daily_hourly AS (
            SELECT CAST(transit_timestamp AS DATE) AS date,
                   HOUR(transit_timestamp) AS hour,
                   SUM(TRY_CAST(ridership AS INTEGER)) AS entries
            FROM mta_data
            WHERE station_complex_id IN ({quoted}) AND transit_mode = 'subway'
            GROUP BY 1, 2
        ),
        all_dates AS (
            SELECT DISTINCT CAST(transit_timestamp AS DATE) AS date
            FROM mta_data WHERE station_complex_id IN ({quoted}) AND transit_mode = 'subway'
        ),
        hours AS (SELECT unnest(range(24)) AS hour),
        combos AS (SELECT d.date, h.hour FROM all_dates d CROSS JOIN hours h)
        SELECT c.date, c.hour, COALESCE(dh.entries, 0) AS entries
        FROM combos c
        LEFT JOIN daily_hourly dh ON dh.date = c.date AND dh.hour = c.hour
        ORDER BY c.date, c.hour
    """).fetchdf()
    con.close()
    entries: dict[str, list] = {}
    for _, row in df.iterrows():
        d = str(row["date"])[:10]
        if d not in entries:
            entries[d] = [0.0] * 24
        h = int(row["hour"])
        if 0 <= h <= 23:
            entries[d][h] = float(row["entries"])
    return entries


def _fetch_citibike_entries(station_ids: list[str]) -> dict[str, list]:
    con = get_citibike_con()
    quoted = ",".join(f"'{i}'" for i in station_ids)
    cols = [r[0] for r in con.execute("DESCRIBE rides").fetchall()]
    has_ended_at = "ended_at" in cols
    if has_ended_at:
        df = con.execute(f"""
            WITH inflow AS (
                SELECT DATE_TRUNC('day', ended_at) AS date,
                       HOUR(ended_at) AS hour, COUNT(*) AS inflow
                FROM rides WHERE end_station_id IN ({quoted}) AND ended_at IS NOT NULL
                GROUP BY 1, 2
            ),
            outflow AS (
                SELECT DATE_TRUNC('day', started_at) AS date,
                       HOUR(started_at) AS hour, COUNT(*) AS outflow
                FROM rides WHERE start_station_id IN ({quoted}) AND started_at IS NOT NULL
                GROUP BY 1, 2
            ),
            all_dates AS (
                SELECT DISTINCT DATE_TRUNC('day', started_at) AS date FROM rides
                WHERE start_station_id IN ({quoted}) OR end_station_id IN ({quoted})
            ),
            hours AS (SELECT unnest(range(24)) AS hour),
            combos AS (SELECT d.date, h.hour FROM all_dates d CROSS JOIN hours h)
            SELECT c.date, c.hour,
                COALESCE(i.inflow, 0) - COALESCE(o.outflow, 0) AS net_flow
            FROM combos c
            LEFT JOIN inflow  i ON i.date = c.date AND i.hour = c.hour
            LEFT JOIN outflow o ON o.date = c.date AND o.hour = c.hour
            ORDER BY c.date, c.hour
        """).fetchdf()
    else:
        df = con.execute(f"""
            WITH outflow AS (
                SELECT DATE_TRUNC('day', started_at) AS date,
                       HOUR(started_at) AS hour, COUNT(*) AS outflow
                FROM rides WHERE start_station_id IN ({quoted}) AND started_at IS NOT NULL
                GROUP BY 1, 2
            ),
            all_dates AS (
                SELECT DISTINCT DATE_TRUNC('day', started_at) AS date FROM rides
                WHERE start_station_id IN ({quoted})
            ),
            hours AS (SELECT unnest(range(24)) AS hour),
            combos AS (SELECT d.date, h.hour FROM all_dates d CROSS JOIN hours h)
            SELECT c.date, c.hour, -COALESCE(o.outflow, 0) AS net_flow
            FROM combos c
            LEFT JOIN outflow o ON o.date = c.date AND o.hour = c.hour
            ORDER BY c.date, c.hour
        """).fetchdf()
    con.close()
    entries: dict[str, list] = {}
    for _, row in df.iterrows():
        d = str(row["date"])[:10]
        if d not in entries:
            entries[d] = [0.0] * 24
        h = int(row["hour"])
        if 0 <= h <= 23:
            entries[d][h] = float(row["net_flow"])
    return entries


def _fetch_taxi_entries(zone_ids: list[int]) -> dict[str, list]:
    placeholders = ",".join(str(i) for i in zone_ids)
    con = duckdb.connect()
    df = con.execute(f"""
        WITH inflow AS (
            SELECT DATE_TRUNC('day', tpep_dropoff_datetime) AS date,
                   HOUR(tpep_dropoff_datetime) AS hour, COUNT(*) AS inflow
            FROM read_parquet('{TAXI_PARQUET_GLOB}')
            WHERE DOLocationID IN ({placeholders})
              AND trip_distance > 0 AND fare_amount > 0 AND tpep_dropoff_datetime IS NOT NULL
            GROUP BY 1, 2
        ),
        outflow AS (
            SELECT DATE_TRUNC('day', tpep_pickup_datetime) AS date,
                   HOUR(tpep_pickup_datetime) AS hour, COUNT(*) AS outflow
            FROM read_parquet('{TAXI_PARQUET_GLOB}')
            WHERE PULocationID IN ({placeholders})
              AND trip_distance > 0 AND fare_amount > 0 AND tpep_pickup_datetime IS NOT NULL
            GROUP BY 1, 2
        ),
        all_dates AS (
            SELECT DISTINCT DATE_TRUNC('day', tpep_pickup_datetime) AS date
            FROM read_parquet('{TAXI_PARQUET_GLOB}')
            WHERE (PULocationID IN ({placeholders}) OR DOLocationID IN ({placeholders}))
              AND trip_distance > 0 AND fare_amount > 0
        ),
        hours AS (SELECT unnest(range(24)) AS hour),
        combos AS (SELECT d.date, h.hour FROM all_dates d CROSS JOIN hours h)
        SELECT c.date, c.hour,
            COALESCE(i.inflow, 0) - COALESCE(o.outflow, 0) AS net_flow
        FROM combos c
        LEFT JOIN inflow  i ON i.date = c.date AND i.hour = c.hour
        LEFT JOIN outflow o ON o.date = c.date AND o.hour = c.hour
        ORDER BY c.date, c.hour
    """).fetchdf()
    con.close()
    entries: dict[str, list] = {}
    for _, row in df.iterrows():
        d = str(row["date"])[:10]
        if d not in entries:
            entries[d] = [0.0] * 24
        h = int(row["hour"])
        if 0 <= h <= 23:
            entries[d][h] = float(row["net_flow"])
    return entries


DATASET_LABELS = {"mta": "MTA Entries", "citibike": "Citibike", "taxi": "Taxi"}
DOW_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# DAYOFWEEK() in DuckDB returns Sunday=0 … Saturday=6.
# DOW_ORDER is Monday-first (Mon=0 … Sun=6).
# Mapping: dow_order_idx = (dayofweek - 1) % 7
_DOW_FROM_INT = {i: DOW_ORDER[(i - 1) % 7] for i in range(7)}   # 0→"Sunday", 1→"Monday" …


# ── Anomaly detection helpers ─────────────────────────────────────────────────

def _anomaly_scores(entries: dict[str, list], date_dow: dict[str, int]) -> dict:
    """
    Given {date: [24-dim vector]} and {date: dow_idx (0=Mon…6=Sun)},
    compute per-date anomaly score = normalised RMS deviation from the
    day-of-week median.  Returns the full result dict.
    """
    # Per-dow stacked matrices
    dow_vecs: dict[int, list] = {i: [] for i in range(7)}
    for d, vec in entries.items():
        dow_vecs[date_dow[d]].append(np.array(vec))

    # Median + MAD per (dow, hour)
    dow_median: dict[int, np.ndarray] = {}
    dow_mad:    dict[int, np.ndarray] = {}
    for dow, vecs in dow_vecs.items():
        if not vecs:
            continue
        arr = np.array(vecs)                     # (n_dates, 24)
        med = np.median(arr, axis=0)
        dow_median[dow] = med
        dow_mad[dow]    = np.median(np.abs(arr - med), axis=0)

    # Anomaly score per date
    scores: dict[str, float] = {}
    for d, vec in entries.items():
        dow = date_dow[d]
        if dow not in dow_median:
            continue
        residual = (np.array(vec) - dow_median[dow]) / (dow_mad[dow] + 1.0)
        scores[d] = float(np.sqrt(np.mean(residual ** 2)))

    return scores, dow_median, dow_mad


def _package_anomaly(dataset_name, entries, date_dow, threshold_pct):
    scores, dow_median, _ = _anomaly_scores(entries, date_dow)
    all_scores = sorted(scores.values())
    threshold = float(np.percentile(all_scores, threshold_pct)) if all_scores else 0.0
    max_score  = max(all_scores) if all_scores else 1.0

    result = []
    for d in sorted(entries.keys()):
        dow  = date_dow.get(d, 0)
        score = scores.get(d, 0.0)
        exp   = dow_median.get(dow, np.zeros(24)).tolist()
        result.append({
            "date":            d,
            "dow":             DOW_SHORT[dow],
            "score":           round(score, 3),
            "is_outlier":      score >= threshold,
            "net_flow_vector": [round(v, 1) for v in entries[d]],
            "expected_vector": [round(v, 1) for v in exp],
        })

    return {
        "mode":      "anomaly",
        "dataset":   dataset_name,
        "dates":     result,
        "threshold": round(threshold, 3),
        "max_score": round(max_score, 3),
    }


def _mta_anomaly(station_ids: list[str], threshold_pct: float):
    con = get_mta_con()
    where = "transit_mode = 'subway'"
    if station_ids:
        quoted = ",".join(f"'{i}'" for i in station_ids)
        where += f" AND station_complex_id IN ({quoted})"

    df = con.execute(f"""
        SELECT
            CAST(transit_timestamp AS DATE)    AS date,
            DAYOFWEEK(transit_timestamp)       AS dow_int,
            HOUR(transit_timestamp)            AS hour,
            SUM(TRY_CAST(ridership AS INTEGER)) AS entries
        FROM mta_data WHERE {where}
        GROUP BY 1, 2, 3
        ORDER BY 1, 3
    """).fetchdf()
    con.close()

    entries: dict[str, list] = {}
    date_dow: dict[str, int] = {}
    for _, row in df.iterrows():
        d = str(row["date"])[:10]
        if d not in entries:
            entries[d] = [0.0] * 24
            date_dow[d] = (int(row["dow_int"]) - 1) % 7
        h = int(row["hour"])
        if 0 <= h <= 23:
            entries[d][h] = float(row["entries"] or 0)

    return _package_anomaly("mta", entries, date_dow, threshold_pct)


def _citibike_anomaly(station_ids: list[str], threshold_pct: float):
    con = get_citibike_con()
    where = "started_at IS NOT NULL"
    if station_ids:
        quoted = ",".join(f"'{i}'" for i in station_ids)
        where += f" AND start_station_id IN ({quoted})"

    df = con.execute(f"""
        SELECT
            DATE_TRUNC('day', started_at)::DATE AS date,
            DAYOFWEEK(started_at)               AS dow_int,
            HOUR(started_at)                    AS hour,
            COUNT(*)                            AS trips
        FROM rides WHERE {where}
        GROUP BY 1, 2, 3
        ORDER BY 1, 3
    """).fetchdf()
    con.close()

    entries: dict[str, list] = {}
    date_dow: dict[str, int] = {}
    for _, row in df.iterrows():
        d = str(row["date"])[:10]
        if d not in entries:
            entries[d] = [0.0] * 24
            date_dow[d] = (int(row["dow_int"]) - 1) % 7
        h = int(row["hour"])
        if 0 <= h <= 23:
            entries[d][h] = float(row["trips"])

    return _package_anomaly("citibike", entries, date_dow, threshold_pct)


def _taxi_anomaly(zone_ids: list[int], threshold_pct: float):
    where_pu = "trip_distance > 0 AND fare_amount > 0 AND tpep_pickup_datetime BETWEEN '2020-01-01' AND '2026-01-01'"
    if zone_ids:
        placeholders = ",".join(str(i) for i in zone_ids)
        where_pu += f" AND PULocationID IN ({placeholders})"

    con = duckdb.connect()
    df = con.execute(f"""
        SELECT
            DATE_TRUNC('day', tpep_pickup_datetime)::DATE AS date,
            DAYOFWEEK(tpep_pickup_datetime)               AS dow_int,
            HOUR(tpep_pickup_datetime)                    AS hour,
            COUNT(*)                                      AS trips
        FROM read_parquet('{TAXI_PARQUET_GLOB}')
        WHERE {where_pu}
        GROUP BY 1, 2, 3
        ORDER BY 1, 3
    """).fetchdf()
    con.close()

    entries: dict[str, list] = {}
    date_dow: dict[str, int] = {}
    for _, row in df.iterrows():
        d = str(row["date"])[:10]
        if d not in entries:
            entries[d] = [0.0] * 24
            date_dow[d] = (int(row["dow_int"]) - 1) % 7
        h = int(row["hour"])
        if 0 <= h <= 23:
            entries[d][h] = float(row["trips"])

    return _package_anomaly("taxi", entries, date_dow, threshold_pct)


@app.get("/api/anomaly_dates")
def anomaly_dates_endpoint(
    dataset:       str   = "mta",
    station_ids:   str   = Query(default=""),   # empty = all stations
    threshold_pct: float = 95.0,                # percentile above which = outlier
):
    ids = [x.strip() for x in station_ids.split(",") if x.strip()]
    if dataset == "mta":
        return _mta_anomaly(ids, threshold_pct)
    if dataset == "citibike":
        return _citibike_anomaly(ids, threshold_pct)
    if dataset == "taxi":
        return _taxi_anomaly([int(i) for i in ids], threshold_pct)
    return {"error": "Anomaly detection only supports mta, citibike, taxi"}


# ── Station × day-type clustering helpers ─────────────────────────────────────

def _cluster_station_vectors(station_ids, X_raw, station_meta, k, normalize,
                              algorithm, eps, min_samples, dataset_name):
    """Cluster rows of X_raw (one per station) and return the JSON response."""
    k = min(k, len(station_ids))
    X = _normalize(X_raw) if normalize else X_raw

    if algorithm == "kmedoids":
        labels, centroids_arr = _kmedoids(X, k)
    elif algorithm == "agglomerative":
        labels, centroids_arr = _agglomerative(X, k)
    elif algorithm == "dbscan":
        labels, centroids_arr = _dbscan(X, eps, min_samples)
    else:
        labels, centroids_arr = _kmeans(X, k)

    unique_clusters = sorted(set(labels.tolist()))

    station_list = []
    for i, sid in enumerate(station_ids):
        meta = station_meta.get(sid, {})
        station_list.append({
            "station_id":   sid,
            "station_name": meta.get("station_name", str(sid)),
            "lat":          float(meta.get("lat") or 0),
            "lng":          float(meta.get("lng") or 0),
            "cluster":      int(labels[i]),
            "pattern":      [round(v, 2) for v in X_raw[i].tolist()],
        })

    centroid_list = []
    for ci in unique_clusters:
        mask = labels == ci
        cv = (centroids_arr[ci].tolist()
              if ci >= 0 and ci < len(centroids_arr)
              else X[mask].mean(axis=0).tolist())
        centroid_list.append({
            "cluster":  ci,
            "centroid": [round(v, 2) for v in cv],
            "count":    int(mask.sum()),
        })

    return {
        "mode":           "station",
        "dataset":        dataset_name,
        "stations":       station_list,
        "centroids":      centroid_list,
        "segment_labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "segment_size":   24,
    }


def _subway_station_clusters(k, normalize, algorithm, eps, min_samples):
    con = get_subway_con()
    meta_df = con.execute("""
        SELECT DISTINCT
            "Origin Station Complex ID"   AS station_id,
            "Origin Station Complex Name" AS station_name,
            AVG("Origin Latitude")        AS lat,
            AVG("Origin Longitude")       AS lng
        FROM subway_data WHERE "Origin Latitude" IS NOT NULL
        GROUP BY 1, 2
    """).fetchdf()

    # Average ridership per (station, dow, hour) across all months
    df = con.execute("""
        SELECT
            "Origin Station Complex ID" AS station_id,
            "Day of Week"               AS dow,
            "Hour of Day"               AS hour,
            AVG("Estimated Average Ridership") AS val
        FROM subway_data
        GROUP BY 1, 2, 3
    """).fetchdf()
    con.close()

    station_meta = {
        row["station_id"]: {
            "station_name": row["station_name"],
            "lat": row["lat"], "lng": row["lng"]
        }
        for _, row in meta_df.iterrows()
    }

    station_ids = sorted(set(int(x) for x in df["station_id"].tolist()))
    vectors: dict[int, list] = {sid: [0.0] * 168 for sid in station_ids}
    for _, row in df.iterrows():
        sid = int(row["station_id"])
        dow = str(row["dow"])
        if dow not in DOW_ORDER:
            continue
        idx = DOW_ORDER.index(dow) * 24 + int(row["hour"])
        if 0 <= idx < 168:
            vectors[sid][idx] = float(row["val"])

    valid_ids = [sid for sid in station_ids if max(vectors[sid]) > 0]
    X_raw = np.array([vectors[sid] for sid in valid_ids])
    return _cluster_station_vectors(valid_ids, X_raw, station_meta,
                                    k, normalize, algorithm, eps, min_samples, "subway")


def _mta_station_clusters(k, normalize, algorithm, eps, min_samples):
    con = get_mta_con()
    meta_df = con.execute("""
        SELECT station_complex_id AS station_id, station_complex AS station_name,
               AVG(latitude) AS lat, AVG(longitude) AS lng
        FROM mta_data WHERE transit_mode = 'subway' AND latitude IS NOT NULL
        GROUP BY 1, 2
    """).fetchdf()

    df = con.execute("""
        SELECT
            station_complex_id AS station_id,
            DAYOFWEEK(transit_timestamp) AS dow_int,
            HOUR(transit_timestamp)      AS hour,
            AVG(TRY_CAST(ridership AS INTEGER)) AS val
        FROM mta_data WHERE transit_mode = 'subway'
        GROUP BY 1, 2, 3
    """).fetchdf()
    con.close()

    station_meta = {
        row["station_id"]: {
            "station_name": row["station_name"],
            "lat": row["lat"], "lng": row["lng"]
        }
        for _, row in meta_df.iterrows()
    }

    station_ids = sorted(set(str(x) for x in df["station_id"].tolist()))
    vectors: dict[str, list] = {sid: [0.0] * 168 for sid in station_ids}
    for _, row in df.iterrows():
        sid  = str(row["station_id"])
        didx = (int(row["dow_int"]) - 1) % 7   # Mon=0 … Sun=6
        idx  = didx * 24 + int(row["hour"])
        if sid in vectors and 0 <= idx < 168:
            vectors[sid][idx] = float(row["val"] or 0)

    valid_ids = [sid for sid in station_ids if max(vectors[sid]) > 0]
    X_raw = np.array([vectors[sid] for sid in valid_ids])
    return _cluster_station_vectors(valid_ids, X_raw, station_meta,
                                    k, normalize, algorithm, eps, min_samples, "mta")


def _citibike_station_clusters(k, normalize, algorithm, eps, min_samples):
    con = get_citibike_con()
    meta_df = con.execute("""
        SELECT start_station_id AS station_id, start_station_name AS station_name,
               AVG(start_lat) AS lat, AVG(start_lng) AS lng
        FROM rides WHERE start_station_id IS NOT NULL AND start_lat IS NOT NULL
        GROUP BY 1, 2
    """).fetchdf()

    df = con.execute("""
        SELECT
            start_station_id          AS station_id,
            DAYOFWEEK(started_at)     AS dow_int,
            HOUR(started_at)          AS hour,
            COUNT(*)                  AS val
        FROM rides WHERE start_station_id IS NOT NULL AND started_at IS NOT NULL
        GROUP BY 1, 2, 3
    """).fetchdf()
    con.close()

    station_meta = {
        row["station_id"]: {
            "station_name": row["station_name"],
            "lat": row["lat"], "lng": row["lng"]
        }
        for _, row in meta_df.iterrows()
    }

    station_ids = sorted(set(str(x) for x in df["station_id"].tolist()))
    vectors: dict[str, list] = {sid: [0.0] * 168 for sid in station_ids}
    for _, row in df.iterrows():
        sid  = str(row["station_id"])
        didx = (int(row["dow_int"]) - 1) % 7
        idx  = didx * 24 + int(row["hour"])
        if sid in vectors and 0 <= idx < 168:
            vectors[sid][idx] = float(row["val"])

    valid_ids = [sid for sid in station_ids if max(vectors[sid]) > 0]
    X_raw = np.array([vectors[sid] for sid in valid_ids])
    return _cluster_station_vectors(valid_ids, X_raw, station_meta,
                                    k, normalize, algorithm, eps, min_samples, "citibike")


def _taxi_station_clusters(k, normalize, algorithm, eps, min_samples):
    zone_list  = _taxi_zone_list()
    station_meta = {
        z["station_id"]: {
            "station_name": z["station_name"],
            "lat": z["lat"], "lng": z["lng"]
        }
        for z in zone_list
    }

    con = duckdb.connect()
    df  = con.execute(f"""
        SELECT
            PULocationID                  AS station_id,
            DAYOFWEEK(tpep_pickup_datetime) AS dow_int,
            HOUR(tpep_pickup_datetime)      AS hour,
            COUNT(*)                        AS val
        FROM read_parquet('{TAXI_PARQUET_GLOB}')
        WHERE trip_distance > 0 AND fare_amount > 0
          AND tpep_pickup_datetime BETWEEN '2020-01-01' AND '2026-01-01'
        GROUP BY 1, 2, 3
    """).fetchdf()
    con.close()

    station_ids = sorted(set(int(x) for x in df["station_id"].tolist()))
    vectors: dict[int, list] = {sid: [0.0] * 168 for sid in station_ids}
    for _, row in df.iterrows():
        sid  = int(row["station_id"])
        didx = (int(row["dow_int"]) - 1) % 7
        idx  = didx * 24 + int(row["hour"])
        if sid in vectors and 0 <= idx < 168:
            vectors[sid][idx] = float(row["val"])

    valid_ids = [sid for sid in station_ids if max(vectors[sid]) > 0]
    X_raw = np.array([vectors[sid] for sid in valid_ids])
    return _cluster_station_vectors(valid_ids, X_raw, station_meta,
                                    k, normalize, algorithm, eps, min_samples, "taxi")


@app.get("/api/station_clusters")
def station_clusters_endpoint(
    dataset:     str   = "subway",
    k:           int   = 5,
    normalize:   bool  = False,
    algorithm:   str   = "kmeans",
    eps:         float = 50.0,
    min_samples: int   = 3,
):
    k = max(2, min(k, 12))
    if dataset == "subway":
        return _subway_station_clusters(k, normalize, algorithm, eps, min_samples)
    if dataset == "mta":
        return _mta_station_clusters(k, normalize, algorithm, eps, min_samples)
    if dataset == "citibike":
        return _citibike_station_clusters(k, normalize, algorithm, eps, min_samples)
    if dataset == "taxi":
        return _taxi_station_clusters(k, normalize, algorithm, eps, min_samples)
    return {"error": "unknown dataset"}


# ── Range-aware fetch helpers (multi-flow only) ───────────────────────────────
# Use dataset-global all_dates within the given date range so that every day
# the dataset was recording is included; station data is filled with 0 for
# days when the selected station(s) had no activity.

def _fetch_mta_entries_in_range(station_ids: list[str], start: str, end: str) -> dict[str, list]:
    con = get_mta_con()
    quoted = ",".join(f"'{i}'" for i in station_ids)
    df = con.execute(f"""
        WITH station_data AS (
            SELECT CAST(transit_timestamp AS DATE) AS date,
                   HOUR(transit_timestamp) AS hour,
                   SUM(TRY_CAST(ridership AS INTEGER)) AS entries
            FROM mta_data
            WHERE station_complex_id IN ({quoted}) AND transit_mode = 'subway'
              AND CAST(transit_timestamp AS DATE) BETWEEN '{start}' AND '{end}'
            GROUP BY 1, 2
        ),
        all_dates AS (
            SELECT DISTINCT CAST(transit_timestamp AS DATE) AS date
            FROM mta_data
            WHERE transit_mode = 'subway'
              AND CAST(transit_timestamp AS DATE) BETWEEN '{start}' AND '{end}'
        ),
        hours AS (SELECT unnest(range(24)) AS hour),
        combos AS (SELECT d.date, h.hour FROM all_dates d CROSS JOIN hours h)
        SELECT c.date, c.hour, COALESCE(sd.entries, 0) AS entries
        FROM combos c
        LEFT JOIN station_data sd ON sd.date = c.date AND sd.hour = c.hour
        ORDER BY c.date, c.hour
    """).fetchdf()
    con.close()
    entries: dict[str, list] = {}
    for _, row in df.iterrows():
        d = str(row["date"])[:10]
        if d not in entries:
            entries[d] = [0.0] * 24
        h = int(row["hour"])
        if 0 <= h <= 23:
            entries[d][h] = float(row["entries"])
    return entries


def _fetch_citibike_entries_in_range(station_ids: list[str], start: str, end: str) -> dict[str, list]:
    con = get_citibike_con()
    quoted = ",".join(f"'{i}'" for i in station_ids)
    cols = [r[0] for r in con.execute("DESCRIBE rides").fetchall()]
    has_ended_at = "ended_at" in cols
    if has_ended_at:
        df = con.execute(f"""
            WITH inflow AS (
                SELECT DATE_TRUNC('day', ended_at)::DATE AS date,
                       HOUR(ended_at) AS hour, COUNT(*) AS inflow
                FROM rides WHERE end_station_id IN ({quoted}) AND ended_at IS NOT NULL
                  AND ended_at::DATE BETWEEN '{start}' AND '{end}'
                GROUP BY 1, 2
            ),
            outflow AS (
                SELECT DATE_TRUNC('day', started_at)::DATE AS date,
                       HOUR(started_at) AS hour, COUNT(*) AS outflow
                FROM rides WHERE start_station_id IN ({quoted}) AND started_at IS NOT NULL
                  AND started_at::DATE BETWEEN '{start}' AND '{end}'
                GROUP BY 1, 2
            ),
            all_dates AS (
                SELECT DISTINCT DATE_TRUNC('day', started_at)::DATE AS date FROM rides
                WHERE started_at::DATE BETWEEN '{start}' AND '{end}'
            ),
            hours AS (SELECT unnest(range(24)) AS hour),
            combos AS (SELECT d.date, h.hour FROM all_dates d CROSS JOIN hours h)
            SELECT c.date, c.hour,
                COALESCE(i.inflow, 0) - COALESCE(o.outflow, 0) AS net_flow
            FROM combos c
            LEFT JOIN inflow  i ON i.date = c.date AND i.hour = c.hour
            LEFT JOIN outflow o ON o.date = c.date AND o.hour = c.hour
            ORDER BY c.date, c.hour
        """).fetchdf()
    else:
        df = con.execute(f"""
            WITH outflow AS (
                SELECT DATE_TRUNC('day', started_at)::DATE AS date,
                       HOUR(started_at) AS hour, COUNT(*) AS outflow
                FROM rides WHERE start_station_id IN ({quoted}) AND started_at IS NOT NULL
                  AND started_at::DATE BETWEEN '{start}' AND '{end}'
                GROUP BY 1, 2
            ),
            all_dates AS (
                SELECT DISTINCT DATE_TRUNC('day', started_at)::DATE AS date FROM rides
                WHERE started_at::DATE BETWEEN '{start}' AND '{end}'
            ),
            hours AS (SELECT unnest(range(24)) AS hour),
            combos AS (SELECT d.date, h.hour FROM all_dates d CROSS JOIN hours h)
            SELECT c.date, c.hour, -COALESCE(o.outflow, 0) AS net_flow
            FROM combos c
            LEFT JOIN outflow o ON o.date = c.date AND o.hour = c.hour
            ORDER BY c.date, c.hour
        """).fetchdf()
    con.close()
    entries: dict[str, list] = {}
    for _, row in df.iterrows():
        d = str(row["date"])[:10]
        if d not in entries:
            entries[d] = [0.0] * 24
        h = int(row["hour"])
        if 0 <= h <= 23:
            entries[d][h] = float(row["net_flow"])
    return entries


def _fetch_taxi_entries_in_range(zone_ids: list[int], start: str, end: str) -> dict[str, list]:
    placeholders = ",".join(str(i) for i in zone_ids)
    con = duckdb.connect()
    df = con.execute(f"""
        WITH inflow AS (
            SELECT DATE_TRUNC('day', tpep_dropoff_datetime)::DATE AS date,
                   HOUR(tpep_dropoff_datetime) AS hour, COUNT(*) AS inflow
            FROM read_parquet('{TAXI_PARQUET_GLOB}')
            WHERE DOLocationID IN ({placeholders})
              AND trip_distance > 0 AND fare_amount > 0
              AND tpep_dropoff_datetime::DATE BETWEEN '{start}' AND '{end}'
            GROUP BY 1, 2
        ),
        outflow AS (
            SELECT DATE_TRUNC('day', tpep_pickup_datetime)::DATE AS date,
                   HOUR(tpep_pickup_datetime) AS hour, COUNT(*) AS outflow
            FROM read_parquet('{TAXI_PARQUET_GLOB}')
            WHERE PULocationID IN ({placeholders})
              AND trip_distance > 0 AND fare_amount > 0
              AND tpep_pickup_datetime::DATE BETWEEN '{start}' AND '{end}'
            GROUP BY 1, 2
        ),
        all_dates AS (
            SELECT DISTINCT DATE_TRUNC('day', tpep_pickup_datetime)::DATE AS date
            FROM read_parquet('{TAXI_PARQUET_GLOB}')
            WHERE trip_distance > 0 AND fare_amount > 0
              AND tpep_pickup_datetime::DATE BETWEEN '{start}' AND '{end}'
        ),
        hours AS (SELECT unnest(range(24)) AS hour),
        combos AS (SELECT d.date, h.hour FROM all_dates d CROSS JOIN hours h)
        SELECT c.date, c.hour,
            COALESCE(i.inflow, 0) - COALESCE(o.outflow, 0) AS net_flow
        FROM combos c
        LEFT JOIN inflow  i ON i.date = c.date AND i.hour = c.hour
        LEFT JOIN outflow o ON o.date = c.date AND o.hour = c.hour
        ORDER BY c.date, c.hour
    """).fetchdf()
    con.close()
    entries: dict[str, list] = {}
    for _, row in df.iterrows():
        d = str(row["date"])[:10]
        if d not in entries:
            entries[d] = [0.0] * 24
        h = int(row["hour"])
        if 0 <= h <= 23:
            entries[d][h] = float(row["net_flow"])
    return entries


@app.get("/api/multi_flow")
def multi_flow(
    mta_ids:      str   = Query(default=""),
    citibike_ids: str   = Query(default=""),
    taxi_ids:     str   = Query(default=""),
    k:            int   = 5,
    normalize:    bool  = True,
    algorithm:    str   = "kmeans",
    eps:          float = 50.0,
    min_samples:  int   = 3,
):
    k = max(2, min(k, 12))
    selected: dict[str, list] = {}
    if mta_ids.strip():
        selected["mta"] = [x.strip() for x in mta_ids.split(",") if x.strip()]
    if citibike_ids.strip():
        selected["citibike"] = [x.strip() for x in citibike_ids.split(",") if x.strip()]
    if taxi_ids.strip():
        selected["taxi"] = [int(x.strip()) for x in taxi_ids.split(",") if x.strip()]

    if len(selected) < 2:
        return {"error": "Select stations from at least 2 datasets"}

    # ── Step 1: get global date range per dataset ─────────────────────────────
    global_ranges: dict[str, tuple[str, str]] = {}
    if "mta" in selected:
        con = get_mta_con()
        r = con.execute("""
            SELECT MIN(CAST(transit_timestamp AS DATE)), MAX(CAST(transit_timestamp AS DATE))
            FROM mta_data WHERE transit_mode = 'subway'
        """).fetchone()
        con.close()
        global_ranges["mta"] = (str(r[0])[:10], str(r[1])[:10])
    if "citibike" in selected:
        con = get_citibike_con()
        r = con.execute("SELECT MIN(started_at::DATE), MAX(started_at::DATE) FROM rides").fetchone()
        con.close()
        global_ranges["citibike"] = (str(r[0])[:10], str(r[1])[:10])
    if "taxi" in selected:
        # Taxi has bad timestamps outside 2020-2027; use fixed reasonable bounds
        global_ranges["taxi"] = ("2020-01-01", "2026-12-31")

    # ── Step 2: intersection of global date ranges ────────────────────────────
    print(f"[multi_flow] global_ranges: {global_ranges}")
    range_start = max(v[0] for v in global_ranges.values())
    range_end   = min(v[1] for v in global_ranges.values())
    print(f"[multi_flow] intersection range: {range_start} → {range_end}")
    if range_start > range_end:
        return {"error": "No overlapping date range across selected datasets"}

    # ── Step 3: fetch using dataset-global all_dates within that range ────────
    entries_per_ds: dict[str, dict] = {}
    if "mta" in selected:
        entries_per_ds["mta"] = _fetch_mta_entries_in_range(selected["mta"], range_start, range_end)
        dates_mta = sorted(entries_per_ds["mta"].keys())
        print(f"[multi_flow] MTA dates: {len(dates_mta)}  first={dates_mta[0] if dates_mta else '-'}  last={dates_mta[-1] if dates_mta else '-'}")
    if "citibike" in selected:
        entries_per_ds["citibike"] = _fetch_citibike_entries_in_range(selected["citibike"], range_start, range_end)
        dates_cb = sorted(entries_per_ds["citibike"].keys())
        print(f"[multi_flow] Citibike dates: {len(dates_cb)}  first={dates_cb[0] if dates_cb else '-'}  last={dates_cb[-1] if dates_cb else '-'}")
    if "taxi" in selected:
        entries_per_ds["taxi"] = _fetch_taxi_entries_in_range(selected["taxi"], range_start, range_end)
        dates_tx = sorted(entries_per_ds["taxi"].keys())
        print(f"[multi_flow] Taxi dates: {len(dates_tx)}  first={dates_tx[0] if dates_tx else '-'}  last={dates_tx[-1] if dates_tx else '-'}")

    # Inner-join on dates — only keep dates present in ALL selected datasets
    date_sets = [set(e.keys()) for e in entries_per_ds.values()]
    common_dates = sorted(date_sets[0].intersection(*date_sets[1:]))
    print(f"[multi_flow] common_dates: {len(common_dates)}  first={common_dates[0] if common_dates else '-'}  last={common_dates[-1] if common_dates else '-'}")

    if len(common_dates) < k:
        return {
            "error": (
                f"Only {len(common_dates)} dates in common across datasets "
                f"(need ≥ k={k}). Try different stations or lower k."
            )
        }

    # Build joint vectors: normalise each 24-dim segment independently, then concatenate
    datasets_order = list(entries_per_ds.keys())
    X_segments = []
    for ds in datasets_order:
        X_ds = np.array([entries_per_ds[ds][d] for d in common_dates])  # (n, 24)
        if normalize:
            X_ds = _normalize(X_ds)
        X_segments.append(X_ds)

    X = np.hstack(X_segments)  # (n, 24 * n_datasets)

    # Cluster
    if algorithm == "kmedoids":
        labels, centroids_arr = _kmedoids(X, k)
    elif algorithm == "agglomerative":
        labels, centroids_arr = _agglomerative(X, k)
    elif algorithm == "dbscan":
        labels, centroids_arr = _dbscan(X, eps, min_samples)
    else:
        labels, centroids_arr = _kmeans(X, k)

    unique_clusters = sorted(set(labels.tolist()))

    vectors = [
        {
            "date":            common_dates[i],
            "cluster":         int(labels[i]),
            "net_flow_vector": [round(v, 2) for v in X[i].tolist()],
        }
        for i in range(len(common_dates))
    ]

    centroid_list = []
    for ci in unique_clusters:
        mask = labels == ci
        cv = (
            centroids_arr[ci].tolist()
            if ci >= 0 and ci < len(centroids_arr)
            else X[mask].mean(axis=0).tolist()
        )
        centroid_list.append({
            "cluster":  ci,
            "centroid": [round(v, 2) for v in cv],
            "count":    int(mask.sum()),
        })

    return {
        "station_label":  " + ".join(DATASET_LABELS[ds] for ds in datasets_order),
        "mode":           "calendar",
        "multi":          True,
        "datasets":       datasets_order,
        "segment_labels": [DATASET_LABELS[ds] for ds in datasets_order],
        "segment_size":   24,
        "vectors":        vectors,
        "centroids":      centroid_list,
    }


# ── HTML ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "index.html").read_text()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8766)
