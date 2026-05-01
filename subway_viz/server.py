import json
import math
from functools import lru_cache

import duckdb
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pathlib import Path
from shapely.geometry import shape
from typing import Optional

app = FastAPI()

SUBWAY_DB = Path(__file__).parent.parent / "subway_2025"
SUBWAY_HOURLY_DB = Path(__file__).parent.parent / "subway_hourly_2025"
CITIBIKE_DB = Path(__file__).parent.parent / "citibike_data.duckdb"
TAXI_PARQUET_GLOB = str(
    Path(__file__).parent.parent / "data/yellow_taxi_records/yellow_taxi_*.parquet"
)
TAXI_ZONES_FILE = Path(__file__).parent.parent / "taxi_viz/zones_cache.geojson"
EVENTS_CSV = Path(__file__).parent.parent / "nyc_unified_events_2025.csv"

EVENT_VENUE_COORDS = {
    "Madison Square Garden": (40.7505, -73.9934),
    "Barclays Center": (40.6826, -73.9754),
    "Yankee Stadium": (40.8296, -73.9262),
    "Citi Field": (40.7571, -73.8458),
    "MetLife Stadium": (40.8135, -74.0745),
    "UBS Arena": (40.7117, -73.7260),
    "Prudential Center": (40.7335, -74.1710),
    "Times Square": (40.7580, -73.9855),
    "Midtown Manhattan": (40.7549, -73.9840),
    "Manhattan": (40.7831, -73.9712),
    "Lower Manhattan": (40.7075, -74.0113),
    "Citywide (All Boroughs)": (40.7128, -74.0060),
}

EVENT_VENUE_KEYWORD_COORDS = [
    ("Central Park", (40.7812, -73.9665)),
    ("Flushing Meadows", (40.7498, -73.8408)),
    ("Dag Hammarskjold Plaza", (40.7525, -73.9690)),
    ("John Paul Jones Park", (40.6117, -74.0350)),
    ("Cannonball Park", (40.6117, -74.0350)),
    ("Gordon Triangle", (40.7445, -73.9030)),
    ("Thomas Jefferson Park", (40.7931, -73.9356)),
    ("Graham Triangle", (40.8089, -73.9188)),
    ("Marcus Garvey Park", (40.8047, -73.9442)),
    ("Fifth Avenue", (40.7750, -73.9654)),
    ("5th Avenue", (40.7750, -73.9654)),
    ("Eastern Parkway", (40.6694, -73.9422)),
    ("Greenwich Village", (40.7336, -74.0027)),
    ("Rockaway Beach", (40.5865, -73.8115)),
    ("Battery Park", (40.7033, -74.0170)),
    ("Bryant Park", (40.7536, -73.9832)),
    ("Union Square", (40.7359, -73.9911)),
    ("Times Square", (40.7580, -73.9855)),
    ("LAFAYETTE AVENUE", (40.8229, -73.8466)),
    ("NEWPORT AVENUE", (40.5788, -73.8506)),
    ("FOREST AVENUE", (40.6273, -74.1089)),
    ("MARINE AVENUE", (40.6183, -74.0347)),
    ("WHITNEY AVENUE", (40.5885, -73.9309)),
    ("EAST   38 STREET", (40.7498, -73.9815)),
    ("BROAD STREET", (40.7033, -74.0110)),
    ("Franklin D Roosevelt Boardwalk", (40.5795, -74.0752)),
    ("Ravenswood Playground", (40.7607, -73.9365)),
    ("Queensbridge Park", (40.7568, -73.9487)),
    ("Murray Playground", (40.7477, -73.9483)),
    ("EAST   44 STREET", (40.7555, -73.9778)),
    ("KNICKERBOCKER AVENUE", (40.6992, -73.9183)),
    ("GRAND CONCOURSE", (40.8306, -73.9225)),
    ("Worth Square", (40.7420, -73.9884)),
    ("WEST   33 STREET", (40.7484, -73.9880)),
    ("St. Vincent's Triangle", (40.7373, -74.0018)),
    ("18 AVENUE", (40.6206, -73.9900)),
    ("Columbus Square", (40.7953, -73.9658)),
    ("34 AVENUE", (40.7568, -73.9287)),
    ("MORRIS PARK AVENUE", (40.8552, -73.8675)),
    ("EAST   43 STREET", (40.7547, -73.9785)),
    ("SULLIVAN STREET", (40.7248, -74.0028)),
    ("WEST   24 STREET", (40.7443, -73.9953)),
]

EVENT_NAME_KEYWORD_COORDS = [
    ("New York City Marathon", (40.7812, -73.9665)),
    ("TCS New York City Marathon", (40.7812, -73.9665)),
    ("Thanksgiving Day Parade", (40.7750, -73.9654)),
    ("Veterans Day Parade", (40.7527, -73.9810)),
    ("Columbus Day Parade", (40.7750, -73.9654)),
    ("Italian Heritage", (40.7750, -73.9654)),
    ("Puerto Rican Day Parade", (40.7750, -73.9654)),
    ("West Indian", (40.6694, -73.9422)),
    ("Halloween Parade", (40.7336, -74.0027)),
    ("Five Boro Bike Tour", (40.7033, -74.0170)),
    ("St Patrick", (40.7750, -73.9654)),
    ("St. Patrick", (40.7750, -73.9654)),
]


def get_con(dataset: str):
    path = SUBWAY_DB if dataset == "subway" else CITIBIKE_DB
    return duckdb.connect(str(path), read_only=True)


def _haversine_miles(lat1, lng1, lat2, lng2):
    radius_miles = 3958.8
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius_miles * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _event_coords_for_venue(venue: str):
    if venue in EVENT_VENUE_COORDS:
        return EVENT_VENUE_COORDS[venue]
    for keyword, coords in EVENT_VENUE_KEYWORD_COORDS:
        if keyword.lower() in venue.lower():
            return coords
    return None


def _event_coords_for_row(event_name: str, venue: str):
    coords = _event_coords_for_venue(venue)
    if coords:
        return coords
    for keyword, coords in EVENT_NAME_KEYWORD_COORDS:
        if keyword.lower() in event_name.lower():
            return coords
    return None


# ── Subway endpoints ──


@app.get("/api/subway/stations")
def subway_stations():
    con = get_con("subway")
    df = con.execute(
        """
        SELECT DISTINCT
            "Origin Station Complex ID" AS station_id,
            "Origin Station Complex Name" AS station_name,
            "Origin Latitude" AS lat,
            "Origin Longitude" AS lng
        FROM subway_data
        WHERE "Origin Latitude" IS NOT NULL
        ORDER BY station_name
    """
    ).fetchdf()
    con.close()
    return df.to_dict(orient="records")


@lru_cache(maxsize=1)
def _subway_station_lookup():
    return {
        str(station["station_id"]): station
        for station in subway_stations()
    }


@lru_cache(maxsize=1)
def _events():
    import csv

    out = []
    if not EVENTS_CSV.exists():
        return out

    with open(EVENTS_CSV) as f:
        for row in csv.DictReader(f):
            event_name = row.get("event_name", "")
            venue = row.get("venue", "")
            coords = _event_coords_for_row(event_name, venue)
            if not coords:
                continue
            out.append(
                {
                    "event_name": event_name,
                    "date": row.get("date", ""),
                    "time": row.get("time", ""),
                    "venue": venue,
                    "genre": row.get("genre", ""),
                    "lat": coords[0],
                    "lng": coords[1],
                }
            )
    return out


@lru_cache(maxsize=1)
def _event_geocode_report():
    import csv
    from collections import Counter

    total = 0
    geocoded = 0
    missing = Counter()
    with open(EVENTS_CSV) as f:
        for row in csv.DictReader(f):
            total += 1
            event_name = row.get("event_name", "")
            venue = row.get("venue", "")
            if _event_coords_for_row(event_name, venue):
                geocoded += 1
            else:
                missing[venue] += 1

    return {
        "total_rows": total,
        "geocoded_rows": geocoded,
        "missing_rows": total - geocoded,
        "missing_venues": [
            {"venue": venue, "count": count}
            for venue, count in missing.most_common()
        ],
    }


@app.get("/api/events")
def events():
    return _events()


@app.get("/api/events/geocode_report")
def event_geocode_report():
    return _event_geocode_report()


@app.get("/api/events/nearby")
def nearby_events(
    station_ids: Optional[str] = Query(None),
    radius_miles: float = 0.75,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    if not station_ids or station_ids.lower() in ("all", "system-wide"):
        events_out = [
            event
            for event in _events()
            if (not start_date or event["date"] >= start_date)
            and (not end_date or event["date"] <= end_date)
        ]
        return {
            "stations": [],
            "events": sorted(
                events_out,
                key=lambda event: (event["date"], event["time"], event["venue"]),
            ),
            "scope": "all",
        }

    ids = [x.strip() for x in station_ids.split(",") if x.strip()]
    stations = [
        _subway_station_lookup()[station_id]
        for station_id in ids
        if station_id in _subway_station_lookup()
    ]
    if not stations:
        return {"stations": [], "events": [], "scope": "none"}

    matched = {}
    for event in _events():
        if start_date and event["date"] < start_date:
            continue
        if end_date and event["date"] > end_date:
            continue

        nearest = None
        for station in stations:
            distance = _haversine_miles(
                float(station["lat"]),
                float(station["lng"]),
                event["lat"],
                event["lng"],
            )
            if nearest is None or distance < nearest["distance_miles"]:
                nearest = {
                    "station_id": station["station_id"],
                    "station_name": station["station_name"],
                    "distance_miles": distance,
                }

        if nearest and nearest["distance_miles"] <= radius_miles:
            key = (
                event["event_name"],
                event["date"],
                event["time"],
                event["venue"],
                event["genre"],
            )
            if key not in matched or nearest["distance_miles"] < matched[key]["distance_miles"]:
                matched[key] = {
                    **event,
                    "station_id": nearest["station_id"],
                    "station_name": nearest["station_name"],
                    "distance_miles": round(nearest["distance_miles"], 2),
                }

    events_out = sorted(
        matched.values(),
        key=lambda event: (event["date"], event["time"], event["distance_miles"]),
    )
    return {"stations": stations, "events": events_out, "scope": "nearby"}


def _subway_where(direction, ids, month, day_of_week, hour_start, hour_end):
    id_col = (
        "Destination Station Complex ID"
        if direction == "dest"
        else "Origin Station Complex ID"
    )
    placeholders = ",".join(str(i) for i in ids)
    clauses = [f'"{id_col}" IN ({placeholders})']
    if month is not None:
        clauses.append(f"Month = {month}")
    if day_of_week == "Weekday":
        clauses.append(
            "\"Day of Week\" IN ('Monday','Tuesday','Wednesday','Thursday','Friday')"
        )
    elif day_of_week == "Weekend":
        clauses.append("\"Day of Week\" IN ('Saturday','Sunday')")
    elif day_of_week is not None:
        clauses.append(f"\"Day of Week\" = '{day_of_week}'")
    if hour_start is not None and hour_end is not None:
        if hour_start <= hour_end:
            clauses.append(
                f'"Hour of Day" >= {hour_start} AND "Hour of Day" <= {hour_end}'
            )
        else:
            clauses.append(
                f'("Hour of Day" >= {hour_start} OR "Hour of Day" <= {hour_end})'
            )
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
    df = con.execute(
        f"""
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
    """
    ).fetchdf()
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
    df = con.execute(
        f"""
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
    """
    ).fetchdf()
    con.close()
    return df.to_dict(orient="records")


# ── Citibike endpoints ──


@app.get("/api/citibike/stations")
def citibike_stations():
    con = get_con("citibike")
    df = con.execute(
        """
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
    """
    ).fetchdf()
    con.close()
    return df.to_dict(orient="records")


def _citibike_where(
    direction, ids, month, day_of_week, hour_start, hour_end, member_casual
):
    id_col = "end_station_id" if direction == "dest" else "start_station_id"
    quoted = ",".join(f"'{i}'" for i in ids)
    clauses = [f"{id_col} IN ({quoted})"]
    if month is not None:
        clauses.append(f"MONTH(started_at) = {month}")
    if day_of_week == "Weekday":
        clauses.append(
            "DAYNAME(started_at) IN ('Monday','Tuesday','Wednesday','Thursday','Friday')"
        )
    elif day_of_week == "Weekend":
        clauses.append("DAYNAME(started_at) IN ('Saturday','Sunday')")
    elif day_of_week is not None:
        clauses.append(f"DAYNAME(started_at) = '{day_of_week}'")
    if hour_start is not None and hour_end is not None:
        if hour_start <= hour_end:
            clauses.append(
                f"HOUR(started_at) >= {hour_start} AND HOUR(started_at) <= {hour_end}"
            )
        else:
            clauses.append(
                f"(HOUR(started_at) >= {hour_start} OR HOUR(started_at) <= {hour_end})"
            )
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
    where = _citibike_where(
        "dest", ids, month, day_of_week, hour_start, hour_end, member_casual
    )
    df = con.execute(
        f"""
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
    """
    ).fetchdf()
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
    where = _citibike_where(
        "origin", ids, month, day_of_week, hour_start, hour_end, member_casual
    )
    df = con.execute(
        f"""
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
    """
    ).fetchdf()
    con.close()
    return df.to_dict(orient="records")


# ── Taxi endpoints ──


@lru_cache(maxsize=1)
def _taxi_zone_list():
    with open(TAXI_ZONES_FILE) as f:
        gj = json.load(f)
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


@lru_cache(maxsize=1)
def _taxi_zone_lookup():
    return {z["station_id"]: z for z in _taxi_zone_list()}


@app.get("/api/taxi/stations")
def taxi_stations():
    return _taxi_zone_list()


@app.get("/api/taxi/zones")
def taxi_zones():
    with open(TAXI_ZONES_FILE) as f:
        return json.load(f)


def _taxi_filter_clauses(month, day_of_week, hour_start, hour_end):
    clauses = ["trip_distance > 0", "fare_amount > 0"]
    if month is not None:
        clauses.append(f"MONTH(tpep_pickup_datetime) = {month}")
    if day_of_week == "Weekday":
        clauses.append(
            "DAYNAME(tpep_pickup_datetime) IN ('Monday','Tuesday','Wednesday','Thursday','Friday')"
        )
    elif day_of_week == "Weekend":
        clauses.append("DAYNAME(tpep_pickup_datetime) IN ('Saturday','Sunday')")
    elif day_of_week is not None:
        clauses.append(f"DAYNAME(tpep_pickup_datetime) = '{day_of_week}'")
    if hour_start is not None and hour_end is not None:
        if hour_start <= hour_end:
            clauses.append(
                f"HOUR(tpep_pickup_datetime) >= {hour_start} AND HOUR(tpep_pickup_datetime) <= {hour_end}"
            )
        else:
            clauses.append(
                f"(HOUR(tpep_pickup_datetime) >= {hour_start} OR HOUR(tpep_pickup_datetime) <= {hour_end})"
            )
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
    clauses = [f"DOLocationID IN ({placeholders})"] + _taxi_filter_clauses(
        month, day_of_week, hour_start, hour_end
    )
    where = " AND ".join(clauses)
    con = duckdb.connect()
    df = con.execute(
        f"""
        SELECT PULocationID AS station_id, COUNT(*) AS total_ridership
        FROM read_parquet('{TAXI_PARQUET_GLOB}')
        WHERE {where}
        GROUP BY 1
        ORDER BY total_ridership DESC
    """
    ).fetchdf()
    con.close()
    lookup = _taxi_zone_lookup()
    return [
        {**lookup[int(row.station_id)], "total_ridership": int(row.total_ridership)}
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
    clauses = [f"PULocationID IN ({placeholders})"] + _taxi_filter_clauses(
        month, day_of_week, hour_start, hour_end
    )
    where = " AND ".join(clauses)
    con = duckdb.connect()
    df = con.execute(
        f"""
        SELECT DOLocationID AS station_id, COUNT(*) AS total_ridership
        FROM read_parquet('{TAXI_PARQUET_GLOB}')
        WHERE {where}
        GROUP BY 1
        ORDER BY total_ridership DESC
    """
    ).fetchdf()
    con.close()
    lookup = _taxi_zone_lookup()
    return [
        {**lookup[int(row.station_id)], "total_ridership": int(row.total_ridership)}
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
    id_col = (
        '"Origin Station Complex ID"'
        if origin_ids
        else '"Destination Station Complex ID"'
    )
    placeholders = ",".join(str(i) for i in ids)
    clauses = [f"{id_col} IN ({placeholders})"]
    if month is not None:
        clauses.append(f"Month = {month}")
    if day_of_week == "Weekday":
        clauses.append(
            "\"Day of Week\" IN ('Monday','Tuesday','Wednesday','Thursday','Friday')"
        )
    elif day_of_week == "Weekend":
        clauses.append("\"Day of Week\" IN ('Saturday','Sunday')")
    elif day_of_week is not None:
        clauses.append(f"\"Day of Week\" = '{day_of_week}'")
    where = " AND ".join(clauses)
    con = get_con("subway")
    df = con.execute(
        f"""
        SELECT "Hour of Day" AS hour, SUM("Estimated Average Ridership") AS total
        FROM subway_data
        WHERE {where}
        GROUP BY 1 ORDER BY 1
    """
    ).fetchdf()
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
    if day_of_week == "Weekday":
        clauses.append(
            "DAYNAME(started_at) IN ('Monday','Tuesday','Wednesday','Thursday','Friday')"
        )
    elif day_of_week == "Weekend":
        clauses.append("DAYNAME(started_at) IN ('Saturday','Sunday')")
    elif day_of_week is not None:
        clauses.append(f"DAYNAME(started_at) = '{day_of_week}'")
    if member_casual:
        clauses.append(f"member_casual = '{member_casual}'")
    where = " AND ".join(clauses)
    con = get_con("citibike")
    df = con.execute(
        f"""
        SELECT HOUR(started_at) AS hour, COUNT(*) AS total
        FROM rides
        WHERE {where}
        GROUP BY 1 ORDER BY 1
    """
    ).fetchdf()
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
    clauses = [f"{id_col} IN ({placeholders})", "trip_distance > 0", "fare_amount > 0"]
    if month is not None:
        clauses.append(f"MONTH(tpep_pickup_datetime) = {month}")
    if day_of_week == "Weekday":
        clauses.append(
            "DAYNAME(tpep_pickup_datetime) IN ('Monday','Tuesday','Wednesday','Thursday','Friday')"
        )
    elif day_of_week == "Weekend":
        clauses.append("DAYNAME(tpep_pickup_datetime) IN ('Saturday','Sunday')")
    elif day_of_week is not None:
        clauses.append(f"DAYNAME(tpep_pickup_datetime) = '{day_of_week}'")
    where = " AND ".join(clauses)
    con = duckdb.connect()
    df = con.execute(
        f"""
        SELECT HOUR(tpep_pickup_datetime) AS hour, COUNT(*) AS total
        FROM read_parquet('{TAXI_PARQUET_GLOB}')
        WHERE {where}
        GROUP BY 1 ORDER BY 1
    """
    ).fetchdf()
    con.close()
    return df.to_dict(orient="records")


@app.get("/api/subway/pair_hourly")
def subway_pair_hourly(
    origin_ids: str = Query(...),
    dest_ids: str = Query(...),
    month: Optional[int] = None,
    day_of_week: Optional[str] = None,
):
    origins = [int(x) for x in origin_ids.split(",") if x.strip()]
    dests = [int(x) for x in dest_ids.split(",") if x.strip()]
    if not origins or not dests:
        return []
    origin_placeholders = ",".join(str(i) for i in origins)
    dest_placeholders = ",".join(str(i) for i in dests)
    clauses = [
        f'"Origin Station Complex ID" IN ({origin_placeholders})',
        f'"Destination Station Complex ID" IN ({dest_placeholders})',
    ]
    if month is not None:
        clauses.append(f"Month = {month}")
    if day_of_week == "Weekday":
        clauses.append(
            "\"Day of Week\" IN ('Monday','Tuesday','Wednesday','Thursday','Friday')"
        )
    elif day_of_week == "Weekend":
        clauses.append("\"Day of Week\" IN ('Saturday','Sunday')")
    elif day_of_week is not None:
        clauses.append(f"\"Day of Week\" = '{day_of_week}'")
    where = " AND ".join(clauses)
    con = get_con("subway")
    df = con.execute(
        f"""
        SELECT "Hour of Day" AS hour, SUM("Estimated Average Ridership") AS total
        FROM subway_data
        WHERE {where}
        GROUP BY 1 ORDER BY 1
    """
    ).fetchdf()
    con.close()
    return df.to_dict(orient="records")


@app.get("/api/citibike/pair_hourly")
def citibike_pair_hourly(
    origin_ids: str = Query(...),
    dest_ids: str = Query(...),
    month: Optional[int] = None,
    day_of_week: Optional[str] = None,
    member_casual: Optional[str] = None,
):
    origins = [x.strip() for x in origin_ids.split(",") if x.strip()]
    dests = [x.strip() for x in dest_ids.split(",") if x.strip()]
    if not origins or not dests:
        return []
    origin_quoted = ",".join(f"'{i}'" for i in origins)
    dest_quoted = ",".join(f"'{i}'" for i in dests)
    clauses = [
        f"start_station_id IN ({origin_quoted})",
        f"end_station_id IN ({dest_quoted})",
    ]
    if month is not None:
        clauses.append(f"MONTH(started_at) = {month}")
    if day_of_week == "Weekday":
        clauses.append(
            "DAYNAME(started_at) IN ('Monday','Tuesday','Wednesday','Thursday','Friday')"
        )
    elif day_of_week == "Weekend":
        clauses.append("DAYNAME(started_at) IN ('Saturday','Sunday')")
    elif day_of_week is not None:
        clauses.append(f"DAYNAME(started_at) = '{day_of_week}'")
    if member_casual:
        clauses.append(f"member_casual = '{member_casual}'")
    where = " AND ".join(clauses)
    con = get_con("citibike")
    df = con.execute(
        f"""
        SELECT HOUR(started_at) AS hour, COUNT(*) AS total
        FROM rides
        WHERE {where}
        GROUP BY 1 ORDER BY 1
    """
    ).fetchdf()
    con.close()
    return df.to_dict(orient="records")


@app.get("/api/taxi/pair_hourly")
def taxi_pair_hourly(
    origin_ids: str = Query(...),
    dest_ids: str = Query(...),
    month: Optional[int] = None,
    day_of_week: Optional[str] = None,
):
    origins = [int(x) for x in origin_ids.split(",") if x.strip()]
    dests = [int(x) for x in dest_ids.split(",") if x.strip()]
    if not origins or not dests:
        return []
    origin_placeholders = ",".join(str(i) for i in origins)
    dest_placeholders = ",".join(str(i) for i in dests)
    clauses = [
        f"PULocationID IN ({origin_placeholders})",
        f"DOLocationID IN ({dest_placeholders})",
        "trip_distance > 0",
        "fare_amount > 0",
    ]
    if month is not None:
        clauses.append(f"MONTH(tpep_pickup_datetime) = {month}")
    if day_of_week == "Weekday":
        clauses.append(
            "DAYNAME(tpep_pickup_datetime) IN ('Monday','Tuesday','Wednesday','Thursday','Friday')"
        )
    elif day_of_week == "Weekend":
        clauses.append("DAYNAME(tpep_pickup_datetime) IN ('Saturday','Sunday')")
    elif day_of_week is not None:
        clauses.append(f"DAYNAME(tpep_pickup_datetime) = '{day_of_week}'")
    where = " AND ".join(clauses)
    con = duckdb.connect()
    df = con.execute(
        f"""
        SELECT HOUR(tpep_pickup_datetime) AS hour, COUNT(*) AS total
        FROM read_parquet('{TAXI_PARQUET_GLOB}')
        WHERE {where}
        GROUP BY 1 ORDER BY 1
    """
    ).fetchdf()
    con.close()
    return df.to_dict(orient="records")


# ── Clustering (system-wide daily patterns) ──


@lru_cache(maxsize=128)
def _daily_matrix(
    dataset: str,
    station_id: Optional[int] = None,
    role: str = "either",
    station_ids: tuple = (),
    normalize: bool = False,
):
    """Return (dates, matrix) where matrix is [n_days, 24] of ridership.
    station_ids=() means system-wide; otherwise sum across the listed station ids.
    role in {'origin', 'destination', 'either'} — reserved for future use.
    """
    if dataset == "subway":
        con = duckdb.connect(str(SUBWAY_HOURLY_DB), read_only=True)
        clauses = ["YEAR(transit_timestamp) = 2025"]
        if station_ids:
            quoted_ids = ",".join(f"'{int(sid)}'" for sid in station_ids)
            clauses.append(f"station_complex_id IN ({quoted_ids})")
        elif station_id is not None:
            clauses.append(f"station_complex_id = '{station_id}'")
        where = "WHERE " + " AND ".join(clauses)
        df = con.execute(
            f"""
            SELECT CAST(transit_timestamp AS DATE) AS date,
                   HOUR(transit_timestamp) AS hour,
                   SUM(ridership) AS ridership
            FROM subway_hourly_2025
            {where}
            GROUP BY 1, 2
            ORDER BY 1, 2
        """
        ).fetchdf()
    else:
        con = get_con(dataset)
        df = con.execute(
            """
            SELECT CAST(started_at AS DATE) AS date,
                   HOUR(started_at) AS hour,
                   COUNT(*) AS ridership
            FROM rides
            GROUP BY 1, 2
            ORDER BY 1, 2
        """
        ).fetchdf()
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
    if normalize:
        row_sums = matrix.sum(axis=1, keepdims=True)
        matrix = matrix / row_sums.clip(min=1)
    return dates, matrix


@lru_cache(maxsize=128)
def _linkage(
    dataset: str,
    station_id: Optional[int] = None,
    role: str = "either",
    station_ids: tuple = (),
    normalize: bool = False,
):
    from scipy.cluster.hierarchy import linkage

    _, matrix = _daily_matrix(dataset, station_id, role, station_ids, normalize)
    if len(matrix) < 2:
        return None
    Z = linkage(matrix, method="ward")
    return Z


def _linkage_to_tree(Z, dates, date_to_cluster):
    n = len(dates)
    nodes = {
        i: {
            "name": dates[i],
            "date": dates[i],
            "cluster": date_to_cluster.get(dates[i]),
            "size": 1,
        }
        for i in range(n)
    }
    if Z is None:
        return nodes[0] if nodes else None

    for i, row in enumerate(Z):
        left_idx = int(row[0])
        right_idx = int(row[1])
        node_idx = n + i
        nodes[node_idx] = {
            "name": f"node-{node_idx}",
            "distance": float(row[2]),
            "size": int(row[3]),
            "children": [nodes[left_idx], nodes[right_idx]],
        }

    return nodes[n + len(Z) - 1] if len(Z) else nodes[0]


def _cluster_embedding(result_clusters):
    import numpy as np

    if not result_clusters:
        return []

    points = np.array([c["_feature_mean"] for c in result_clusters], dtype=float)
    n = len(points)
    if n == 1:
        c = result_clusters[0]
        return [{"id": c["id"], "x": 0.0, "y": 0.0, "size": c["size"]}]

    diffs = points[:, None, :] - points[None, :, :]
    distances = np.sqrt((diffs * diffs).sum(axis=2))
    centered = np.eye(n) - np.ones((n, n)) / n
    gram = -0.5 * centered @ (distances * distances) @ centered
    vals, vecs = np.linalg.eigh(gram)
    order = np.argsort(vals)[::-1]

    coords = np.zeros((n, 2))
    for out_dim, eig_idx in enumerate(order[:2]):
        val = vals[eig_idx]
        if val > 0:
            coords[:, out_dim] = vecs[:, eig_idx] * np.sqrt(val)

    return [
        {
            "id": c["id"],
            "x": float(coords[i, 0]),
            "y": float(coords[i, 1]),
            "size": c["size"],
        }
        for i, c in enumerate(result_clusters)
    ]


@app.get("/api/{dataset}/clustering")
def clustering(
    dataset: str,
    k: int = 5,
    station_id: Optional[int] = None,
    station_ids: Optional[str] = None,
    role: str = "either",
    normalize: bool = False,
):
    from scipy.cluster.hierarchy import fcluster

    if dataset not in ("subway", "citibike"):
        return {"error": "invalid dataset"}
    if role not in ("origin", "destination", "either"):
        role = "either"
    station_id_tuple = tuple(
        int(x) for x in station_ids.split(",") if x.strip()
    ) if station_ids else ()
    dates, matrix = _daily_matrix(dataset, station_id, role, station_id_tuple, normalize)
    _, display_matrix = _daily_matrix(dataset, station_id, role, station_id_tuple, False)
    if len(dates) < 2:
        return {"dates": [], "date_to_cluster": {}, "clusters": [], "k": 0, "max_k": 0}
    Z = _linkage(dataset, station_id, role, station_id_tuple, normalize)
    k = max(1, min(k, len(dates)))
    labels = fcluster(Z, t=k, criterion="maxclust")

    clusters = {}
    for i, lbl in enumerate(labels):
        clusters.setdefault(int(lbl), []).append(i)

    # Build cluster summaries
    result_clusters = []
    for lbl, idxs in clusters.items():
        mean_pattern = display_matrix[idxs].mean(axis=0).tolist()
        result_clusters.append(
            {
                "id": lbl,
                "size": len(idxs),
                "mean_pattern": mean_pattern,
                "_feature_mean": matrix[idxs].mean(axis=0).tolist(),
                "dates": [dates[i] for i in idxs],
            }
        )
    # Sort clusters by number of dates so small/special clusters get lower IDs.
    result_clusters.sort(key=lambda c: (c["size"], -sum(c["mean_pattern"])))
    # Reassign stable 1..k ids
    id_map = {c["id"]: new_id for new_id, c in enumerate(result_clusters, start=1)}
    for c in result_clusters:
        c["id"] = id_map[c["id"]]

    cluster_embedding = _cluster_embedding(result_clusters)
    for c in result_clusters:
        c.pop("_feature_mean", None)

    date_to_cluster = {}
    for c in result_clusters:
        for d in c["dates"]:
            date_to_cluster[d] = c["id"]

    date_totals = {dates[i]: float(display_matrix[i].sum()) for i in range(len(dates))}
    date_patterns = {
        dates[i]: [float(v) for v in display_matrix[i].tolist()]
        for i in range(len(dates))
    }

    return {
        "dates": dates,
        "date_to_cluster": date_to_cluster,
        "clusters": result_clusters,
        "cluster_embedding": cluster_embedding,
        "tree": _linkage_to_tree(Z, dates, date_to_cluster),
        "date_totals": date_totals,
        "date_patterns": date_patterns,
        "k": k,
        "max_k": len(dates),
        "normalize": normalize,
    }


WEATHER_DB = Path(__file__).parent.parent / "nyc_weather_data"
WEATHER_CSV = Path(__file__).parent.parent / "nyc_weather_data.csv"


@lru_cache(maxsize=1)
def _weather():
    import csv

    out = {}
    if WEATHER_DB.exists():
        con = duckdb.connect(str(WEATHER_DB), read_only=True)
        df = con.execute(
            """
            WITH zone_day AS (
                SELECT
                    CAST(datetime AS DATE) AS date,
                    zone_id,
                    MAX(temp_f) AS temp_max_f,
                    SUM(rain_in) AS rain_in,
                    SUM(snowfall_in) AS snowfall_in,
                    MAX(weather_code) AS zone_weather_code
                FROM weather
                GROUP BY 1, 2
            ),
            code_counts AS (
                SELECT
                    date,
                    zone_weather_code AS weather_code,
                    COUNT(*) AS n,
                    ROW_NUMBER() OVER (
                        PARTITION BY date
                        ORDER BY COUNT(*) DESC, zone_weather_code DESC
                    ) AS rn
                FROM zone_day
                WHERE zone_weather_code IS NOT NULL
                GROUP BY 1, 2
            )
            SELECT
                CAST(zone_day.date AS VARCHAR) AS date,
                AVG(zone_day.rain_in) AS rain_in,
                AVG(zone_day.snowfall_in) AS snowfall_in,
                AVG(zone_day.temp_max_f) AS temp_max_f,
                code_counts.weather_code
            FROM zone_day
            LEFT JOIN code_counts
              ON zone_day.date = code_counts.date
             AND code_counts.rn = 1
            GROUP BY 1, 5
            ORDER BY 1
            """
        ).fetchdf()
        con.close()
        for row in df.to_dict(orient="records"):
            out[row["date"]] = {
                "rain_in": (
                    float(row["rain_in"]) if row["rain_in"] is not None else None
                ),
                "snowfall_in": (
                    float(row["snowfall_in"])
                    if row["snowfall_in"] is not None
                    else None
                ),
                "temp_max_f": (
                    float(row["temp_max_f"])
                    if row["temp_max_f"] is not None
                    else None
                ),
                "weather_code": (
                    int(row["weather_code"])
                    if row["weather_code"] is not None
                    else None
                ),
            }
        return out

    with open(WEATHER_CSV) as f:
        for row in csv.DictReader(f):
            try:
                out[row["date"]] = {
                    "rain_in": float(row["rain_in"]) if row["rain_in"] else None,
                    "snowfall_in": (
                        float(row["snowfall_in"])
                        if row.get("snowfall_in")
                        else None
                    ),
                    "temp_max_f": (
                        float(row["temp_max_f"]) if row["temp_max_f"] else None
                    ),
                    "weather_code": (
                        int(float(row["weather_code"]))
                        if row.get("weather_code")
                        else None
                    ),
                }
            except ValueError:
                pass
    return out


@app.get("/api/weather")
def weather():
    return _weather()


@app.get("/clustering", response_class=HTMLResponse)
def clustering_page():
    return (Path(__file__).parent / "clustering.html").read_text()


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "index.html").read_text()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8765)
