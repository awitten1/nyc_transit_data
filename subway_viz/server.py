import duckdb
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pathlib import Path
from typing import Optional

app = FastAPI()

SUBWAY_DB = Path(__file__).parent.parent / "subway_2025"
CITIBIKE_DB = Path(__file__).parent.parent / "citibike_data.duckdb"


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
    if day_of_week is not None:
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
    if day_of_week is not None:
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


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "index.html").read_text()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
