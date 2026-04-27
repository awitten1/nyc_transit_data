"""Build Penn Station profile clusters from local subway datasets.

Two source modes are supported:

* `od` keeps the representative month-by-weekday profiles from the local O-D table.
* `hourly_parquet` uses one 24-hour profile per literal calendar date from the
  station-level hourly parquet.

That lets the hourly-parquet workflow follow the same broad method from van Wijk
and van Selow (1999) while taking advantage of the fact that this source
contains actual dates: split the series into comparable daily profiles, cluster
them bottom-up, and visualize the clustered calendar.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import heapq
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parent
OD_DB_PATH = ROOT / "data" / "subway_od_2025"
HOURLY_PARQUET_PATH = ROOT / "data" / "subway_hourly_2025.parquet"
OD_OUTPUT_PATH = ROOT / "data" / "penn_station_clusters.json"
HOURLY_OUTPUT_PATH = ROOT / "data" / "penn_station_hourly_clusters_2025.json"
DUCKDB_BIN = shutil.which("duckdb")

PENN_STATION_IDS = (164, 318)
OD_PENN_LABEL = "Penn Station outbound (34 St-Penn ACE + 123)"
HOURLY_PENN_LABEL = "Penn Station entries (34 St-Penn ACE + 123)"
MEASURES = ("dnm", "drms", "dsh", "dma")
DEFAULT_CLUSTER_COUNTS = (2, 3, 4, 5, 6, 7, 8)
MONTH_NAMES = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}
WEEKDAY_ORDER = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6,
}


@dataclass
class ClusterProfile:
    profile_key: str
    label: str
    month: int
    month_name: str
    day_of_week: str
    hourly_ridership: np.ndarray
    total_ridership: float
    peak_hour: int
    top_destinations: List[dict]
    service_date: str | None = None
    day_of_month: int | None = None


@dataclass
class ClusterNode:
    cluster_id: int
    member_indexes: List[int]
    centroid: np.ndarray
    merge_distance: float
    left_id: int | None = None
    right_id: int | None = None

    @property
    def size(self) -> int:
        return len(self.member_indexes)


def run_duckdb_query(query: str, database_path: Path | None = None) -> List[dict]:
    if not DUCKDB_BIN:
        raise RuntimeError("The `duckdb` CLI is required but was not found on PATH.")

    if database_path is None:
        cmd = [DUCKDB_BIN, "-csv", "-c", query]
    else:
        cmd = [DUCKDB_BIN, "-readonly", "-csv", str(database_path), query]

    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return list(csv.DictReader(result.stdout.splitlines()))


def weekday_order_sql(column_name: str) -> str:
    return (
        f"CASE {column_name} "
        "WHEN 'Monday' THEN 1 "
        "WHEN 'Tuesday' THEN 2 "
        "WHEN 'Wednesday' THEN 3 "
        "WHEN 'Thursday' THEN 4 "
        "WHEN 'Friday' THEN 5 "
        "WHEN 'Saturday' THEN 6 "
        "ELSE 7 END"
    )


def weekday_name_sql(timestamp_expression: str) -> str:
    return (
        f"CASE strftime({timestamp_expression}, '%w') "
        "WHEN '0' THEN 'Sunday' "
        "WHEN '1' THEN 'Monday' "
        "WHEN '2' THEN 'Tuesday' "
        "WHEN '3' THEN 'Wednesday' "
        "WHEN '4' THEN 'Thursday' "
        "WHEN '5' THEN 'Friday' "
        "ELSE 'Saturday' END"
    )


def representative_profile_key(month: int, day_of_week: str) -> str:
    return f"{month:02d}-{day_of_week}"


def build_profile(
    *,
    profile_key: str,
    label: str,
    month: int,
    day_of_week: str,
    hourly_ridership: np.ndarray,
    top_destinations: Sequence[dict] | None = None,
    service_date: str | None = None,
    day_of_month: int | None = None,
) -> ClusterProfile:
    return ClusterProfile(
        profile_key=profile_key,
        label=label,
        month=month,
        month_name=MONTH_NAMES[month],
        day_of_week=day_of_week,
        hourly_ridership=hourly_ridership,
        total_ridership=float(hourly_ridership.sum()),
        peak_hour=int(hourly_ridership.argmax()),
        top_destinations=list(top_destinations or []),
        service_date=service_date,
        day_of_month=day_of_month,
    )


def assemble_representative_profiles(
    hourly_rows: Sequence[dict],
    *,
    top_destination_rows: Sequence[dict] | None = None,
    empty_error_message: str,
) -> List[ClusterProfile]:
    profiles_by_key: Dict[str, dict] = {}
    for row in hourly_rows:
        month = int(row["month_num"])
        day_of_week = row["day_of_week"]
        key = representative_profile_key(month, day_of_week)
        bucket = profiles_by_key.setdefault(
            key,
            {
                "profile_key": key,
                "label": f"{MONTH_NAMES[month]} {day_of_week}",
                "month": month,
                "day_of_week": day_of_week,
                "hourly_ridership": np.zeros(24, dtype=float),
                "top_destinations": [],
            },
        )
        bucket["hourly_ridership"][int(row["hour_of_day"])] = float(row["ridership"])

    if top_destination_rows:
        for row in top_destination_rows:
            key = representative_profile_key(int(row["month_num"]), row["day_of_week"])
            profiles_by_key[key]["top_destinations"].append(
                {
                    "destination_id": int(row["destination_id"]),
                    "destination_name": row["destination_name"],
                    "ridership": round(float(row["ridership"]), 4),
                }
            )

    profiles = [
        build_profile(
            profile_key=item["profile_key"],
            label=item["label"],
            month=item["month"],
            day_of_week=item["day_of_week"],
            hourly_ridership=item["hourly_ridership"],
            top_destinations=item["top_destinations"],
        )
        for item in sorted(
            profiles_by_key.values(),
            key=lambda value: (value["month"], WEEKDAY_ORDER[value["day_of_week"]]),
        )
    ]

    if not profiles:
        raise RuntimeError(empty_error_message)
    if len(profiles) != 84:
        raise RuntimeError(f"Expected 84 month-weekday profiles, found {len(profiles)}.")

    return profiles


def assemble_daily_profiles(
    hourly_rows: Sequence[dict],
    *,
    year: int,
    empty_error_message: str,
) -> List[ClusterProfile]:
    profiles_by_key: Dict[str, dict] = {}
    for row in hourly_rows:
        service_date = row["service_date"]
        month = int(row["month_num"])
        day_of_month = int(row["day_of_month"])
        day_of_week = row["day_of_week"]
        bucket = profiles_by_key.setdefault(
            service_date,
            {
                "profile_key": service_date,
                "label": f"{MONTH_NAMES[month]} {day_of_month}, {year}",
                "month": month,
                "day_of_month": day_of_month,
                "day_of_week": day_of_week,
                "service_date": service_date,
                "hourly_ridership": np.zeros(24, dtype=float),
                "top_destinations": [],
            },
        )
        bucket["hourly_ridership"][int(row["hour_of_day"])] = float(row["ridership"])

    profiles = [
        build_profile(
            profile_key=item["profile_key"],
            label=item["label"],
            month=item["month"],
            day_of_week=item["day_of_week"],
            hourly_ridership=item["hourly_ridership"],
            service_date=item["service_date"],
            day_of_month=item["day_of_month"],
        )
        for item in sorted(profiles_by_key.values(), key=lambda value: value["service_date"])
    ]

    if not profiles:
        raise RuntimeError(empty_error_message)

    expected_days = 366 if calendar.isleap(year) else 365
    if len(profiles) != expected_days:
        raise RuntimeError(f"Expected {expected_days} daily profiles, found {len(profiles)}.")

    return profiles


def load_representative_profiles_from_od_database(
    station_ids: Sequence[int],
    database_path: Path,
) -> List[ClusterProfile]:
    station_list = ",".join(str(station_id) for station_id in station_ids)
    hourly_rows = run_duckdb_query(
        f"""
        WITH hourly AS (
          SELECT
            Month AS month_num,
            "Day of Week" AS day_of_week,
            "Hour of Day" AS hour_of_day,
            SUM("Estimated Average Ridership") AS ridership
          FROM subway_data
          WHERE "Origin Station Complex ID" IN ({station_list})
          GROUP BY 1, 2, 3
        )
        SELECT
          month_num,
          day_of_week,
          hour_of_day,
          ridership
        FROM hourly
        ORDER BY month_num, {weekday_order_sql("day_of_week")}, hour_of_day;
        """,
        database_path=database_path,
    )

    top_destination_rows = run_duckdb_query(
        f"""
        WITH profile_destinations AS (
          SELECT
            Month AS month_num,
            "Day of Week" AS day_of_week,
            "Destination Station Complex ID" AS destination_id,
            ANY_VALUE("Destination Station Complex Name") AS destination_name,
            SUM("Estimated Average Ridership") AS ridership
          FROM subway_data
          WHERE "Origin Station Complex ID" IN ({station_list})
          GROUP BY 1, 2, 3
        ),
        ranked AS (
          SELECT
            *,
            ROW_NUMBER() OVER (
              PARTITION BY month_num, day_of_week
              ORDER BY ridership DESC, destination_id
            ) AS rank_in_profile
          FROM profile_destinations
        )
        SELECT
          month_num,
          day_of_week,
          destination_id,
          destination_name,
          ridership,
          rank_in_profile
        FROM ranked
        WHERE rank_in_profile <= 5
        ORDER BY month_num, {weekday_order_sql("day_of_week")}, rank_in_profile;
        """,
        database_path=database_path,
    )

    return assemble_representative_profiles(
        hourly_rows,
        top_destination_rows=top_destination_rows,
        empty_error_message="No Penn Station representative profiles were loaded from the O-D database.",
    )


def load_daily_profiles_from_hourly_parquet(
    station_ids: Sequence[int],
    parquet_path: Path,
    year: int,
) -> List[ClusterProfile]:
    station_list = ",".join(str(station_id) for station_id in station_ids)
    parquet_literal = parquet_path.as_posix().replace("'", "''")
    hourly_rows = run_duckdb_query(
        f"""
        WITH daily_hourly AS (
          SELECT
            CAST(transit_timestamp AS DATE) AS service_date,
            CAST(EXTRACT('month' FROM transit_timestamp) AS INTEGER) AS month_num,
            CAST(EXTRACT('day' FROM transit_timestamp) AS INTEGER) AS day_of_month,
            {weekday_name_sql("transit_timestamp")} AS day_of_week,
            CAST(EXTRACT('hour' FROM transit_timestamp) AS INTEGER) AS hour_of_day,
            SUM(ridership) AS ridership
          FROM read_parquet('{parquet_literal}')
          WHERE transit_mode = 'subway'
            AND TRY_CAST(station_complex_id AS INTEGER) IN ({station_list})
            AND transit_timestamp >= TIMESTAMP '{year}-01-01'
            AND transit_timestamp < TIMESTAMP '{year + 1}-01-01'
          GROUP BY 1, 2, 3, 4, 5
        )
        SELECT
          CAST(service_date AS VARCHAR) AS service_date,
          month_num,
          day_of_month,
          day_of_week,
          hour_of_day,
          ridership
        FROM daily_hourly
        ORDER BY service_date, hour_of_day;
        """
    )

    return assemble_daily_profiles(
        hourly_rows,
        year=year,
        empty_error_message="No Penn Station daily profiles were loaded from the hourly parquet.",
    )


def distance(a: np.ndarray, b: np.ndarray, measure: str) -> float:
    if measure == "drms":
        return float(np.sqrt(np.mean((a - b) ** 2)))
    if measure == "dnm":
        a_max = float(np.max(a))
        b_max = float(np.max(b))
        if a_max <= 0 and b_max <= 0:
            return 0.0
        if a_max <= 0 or b_max <= 0:
            return float("inf")
        return float(np.sqrt(np.mean(((a / a_max) - (b / b_max)) ** 2)))
    if measure == "dsh":
        offset = float(np.mean(a - b))
        return float(np.sqrt(np.mean((a - b - offset) ** 2)))
    if measure == "dma":
        return abs(float(np.max(a)) - float(np.max(b)))
    raise ValueError(f"Unsupported measure: {measure}")


def summarize_cluster(
    node: ClusterNode,
    profiles: Sequence[ClusterProfile],
    vectors: np.ndarray,
    measure: str,
) -> dict:
    members = [profiles[index] for index in node.member_indexes]
    member_vectors = vectors[node.member_indexes]
    representative_index = min(
        node.member_indexes,
        key=lambda idx: distance(vectors[idx], node.centroid, measure),
    )
    representative = profiles[representative_index]

    weekday_counts: Dict[str, int] = {}
    month_counts: Dict[int, int] = {}
    service_dates = [member.service_date for member in members if member.service_date]
    for member in members:
        weekday_counts[member.day_of_week] = weekday_counts.get(member.day_of_week, 0) + 1
        month_counts[member.month] = month_counts.get(member.month, 0) + 1

    ordered_weekday_counts = [
        {"day_of_week": name, "count": count}
        for name, count in sorted(weekday_counts.items(), key=lambda item: WEEKDAY_ORDER[item[0]])
    ]
    ordered_month_counts = [
        {"month": month, "month_name": MONTH_NAMES[month], "count": count}
        for month, count in sorted(month_counts.items())
    ]

    return {
        "cluster_id": node.cluster_id,
        "size": node.size,
        "profile_keys": [member.profile_key for member in members],
        "labels": [member.label for member in members],
        "average_profile": [round(float(value), 4) for value in node.centroid.tolist()],
        "average_total_ridership": round(float(np.mean([member.total_ridership for member in members])), 4),
        "average_peak_hour": round(float(np.mean([member.peak_hour for member in members])), 2),
        "weekday_counts": ordered_weekday_counts,
        "month_counts": ordered_month_counts,
        "service_date_range": (
            {"start": min(service_dates), "end": max(service_dates)}
            if service_dates
            else None
        ),
        "representative_profile": {
            "profile_key": representative.profile_key,
            "label": representative.label,
            "month": representative.month,
            "day_of_week": representative.day_of_week,
            "service_date": representative.service_date,
            "day_of_month": representative.day_of_month,
            "total_ridership": round(representative.total_ridership, 4),
        },
        "merge_distance": round(node.merge_distance, 6),
        "member_total_ridership_range": {
            "min": round(float(np.min(np.sum(member_vectors, axis=1))), 4),
            "max": round(float(np.max(np.sum(member_vectors, axis=1))), 4),
        },
    }


def agglomerative_cluster(
    vectors: np.ndarray,
    profiles: Sequence[ClusterProfile],
    measure: str,
    target_cluster_counts: Sequence[int],
) -> dict:
    active: Dict[int, ClusterNode] = {
        index: ClusterNode(
            cluster_id=index,
            member_indexes=[index],
            centroid=vectors[index].copy(),
            merge_distance=0.0,
        )
        for index in range(len(vectors))
    }
    merges: List[dict] = []
    partitions: Dict[int, dict] = {}
    next_cluster_id = len(vectors)
    wanted_counts = set(target_cluster_counts)
    heap: List[tuple[float, int, int]] = []

    active_ids = sorted(active)
    for i, left_id in enumerate(active_ids):
        left = active[left_id]
        for right_id in active_ids[i + 1 :]:
            right = active[right_id]
            heapq.heappush(heap, (distance(left.centroid, right.centroid, measure), left_id, right_id))

    while len(active) > 1:
        best_distance = None
        best_pair: tuple[int, int] | None = None
        while heap:
            candidate_distance, left_id, right_id = heapq.heappop(heap)
            if left_id in active and right_id in active:
                best_distance = float(candidate_distance)
                best_pair = (left_id, right_id)
                break

        if best_pair is None or best_distance is None:
            raise RuntimeError("Clustering failed to find a mergeable pair.")

        left_id, right_id = best_pair
        left = active.pop(left_id)
        right = active.pop(right_id)
        merged = ClusterNode(
            cluster_id=next_cluster_id,
            member_indexes=sorted(left.member_indexes + right.member_indexes),
            centroid=((left.centroid * left.size) + (right.centroid * right.size)) / (left.size + right.size),
            merge_distance=float(best_distance),
            left_id=left_id,
            right_id=right_id,
        )

        for other_id, other in active.items():
            heapq.heappush(
                heap,
                (distance(merged.centroid, other.centroid, measure), min(next_cluster_id, other_id), max(next_cluster_id, other_id)),
            )

        active[next_cluster_id] = merged
        merges.append(
            {
                "cluster_id": next_cluster_id,
                "left_id": left_id,
                "right_id": right_id,
                "distance": round(best_distance, 6),
                "size": merged.size,
            }
        )
        next_cluster_id += 1

        current_count = len(active)
        if current_count in wanted_counts and current_count not in partitions:
            cluster_ids = sorted(active)
            assignments = {}
            for cluster_id in cluster_ids:
                for member_index in active[cluster_id].member_indexes:
                    assignments[profiles[member_index].profile_key] = cluster_id
            partitions[current_count] = {
                "cluster_ids": cluster_ids,
                "assignments": assignments,
                "clusters": [
                    summarize_cluster(active[cluster_id], profiles, vectors, measure)
                    for cluster_id in cluster_ids
                ],
            }

    return {
        "measure": measure,
        "root_cluster_id": next_cluster_id - 1,
        "merges": merges,
        "partitions": {str(count): partitions[count] for count in sorted(partitions)},
        "distance_measure_notes": {
            "drms": "Root-mean-square distance on raw hourly ridership; emphasizes absolute volume differences.",
            "dnm": "Normalized RMS distance after dividing each profile by its own peak; emphasizes shape.",
            "dsh": "Shift-invariant RMS distance after subtracting the average offset; downweights level shifts.",
            "dma": "Absolute difference between daily peak values; emphasizes peak intensity only.",
        }[measure],
    }


def build_output(
    *,
    source: str,
    input_path: Path,
    target_cluster_counts: Sequence[int],
    year: int,
) -> dict:
    if source == "od":
        profiles = load_representative_profiles_from_od_database(PENN_STATION_IDS, input_path)
        station = {
            "label": OD_PENN_LABEL,
            "origin_station_complex_ids": list(PENN_STATION_IDS),
        }
        dataset_semantics = {
            "profile_unit": "representative_month_weekday",
            "calendar_layout": "representative_grid",
            "source": "subway_2025 DuckDB O-D table",
            "profile_count": len(profiles),
            "months": 12,
            "weekdays_per_month": 7,
            "profile_detail_note": "Top destinations come from the O-D source table.",
        }
        paper_mapping = {
            "source_paper": "van Wijk and van Selow (1999), Cluster and calendar based visualization of time series data",
            "profile_definition": "24-hour outbound ridership profile for Penn Station, aggregated across all destinations, keyed by month and weekday.",
            "clustering_method": "Bottom-up agglomerative clustering using cluster-average profiles.",
            "notes": [
                "The literal timestamps in this O-D dataset are representative placeholders from the first full week of each month.",
                "We therefore ignore calendar dates and cluster the 84 representative month-weekday profiles instead.",
                "Top destinations are preserved for interpretation, but the clustering remains univariate and based on the 24-hour ridership curve.",
            ],
        }
    elif source == "hourly_parquet":
        profiles = load_daily_profiles_from_hourly_parquet(PENN_STATION_IDS, input_path, year)
        station = {
            "label": HOURLY_PENN_LABEL,
            "station_complex_ids": list(PENN_STATION_IDS),
            "date_range": {
                "start": f"{year}-01-01",
                "end": f"{year}-12-31",
            },
        }
        dataset_semantics = {
            "profile_unit": "calendar_date",
            "calendar_layout": "calendar_dates",
            "calendar_year": year,
            "source": f"subway_hourly_{year} parquet",
            "profile_count": len(profiles),
            "profile_detail_note": "Destination breakdown is unavailable in the station-level hourly parquet.",
        }
        paper_mapping = {
            "source_paper": "van Wijk and van Selow (1999), Cluster and calendar based visualization of time series data",
            "profile_definition": f"One 24-hour Penn Station entry profile for each literal calendar date in {year}.",
            "clustering_method": "Bottom-up agglomerative clustering using cluster-average profiles.",
            "notes": [
                f"This station-level parquet contains literal hourly timestamps from January 1, {year} through December 31, {year}.",
                "Because this source has real dates, we split it into one 24-hour profile per actual calendar day before clustering.",
                "This keeps the paper's daily-profile clustering idea while using the fuller calendar detail now available.",
            ],
        }
    else:
        raise ValueError(f"Unsupported source: {source}")

    vectors = np.vstack([profile.hourly_ridership for profile in profiles])
    cluster_runs = {
        measure: agglomerative_cluster(vectors, profiles, measure, target_cluster_counts)
        for measure in MEASURES
    }

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "station": station,
        "dataset_semantics": dataset_semantics,
        "paper_mapping": paper_mapping,
        "profiles": [
            {
                "profile_key": profile.profile_key,
                "label": profile.label,
                "month": profile.month,
                "month_name": profile.month_name,
                "day_of_week": profile.day_of_week,
                "service_date": profile.service_date,
                "day_of_month": profile.day_of_month,
                "hourly_ridership": [round(float(value), 4) for value in profile.hourly_ridership.tolist()],
                "total_ridership": round(profile.total_ridership, 4),
                "peak_hour": profile.peak_hour,
                "top_destinations": profile.top_destinations,
            }
            for profile in profiles
        ],
        "cluster_runs": cluster_runs,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=("od", "hourly_parquet"),
        default="od",
        help="Which Penn Station dataset to cluster.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Optional input path. Defaults to the standard path for the selected source.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output JSON path. Defaults to the standard path for the selected source.",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=2025,
        help="Calendar year to filter when using --source hourly_parquet.",
    )
    parser.add_argument(
        "--cluster-counts",
        type=int,
        nargs="+",
        default=list(DEFAULT_CLUSTER_COUNTS),
        help="Cluster counts to materialize in the output JSON, e.g. --cluster-counts 3 4 5 6",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_path = args.input
    if input_path is None:
        input_path = OD_DB_PATH if args.source == "od" else HOURLY_PARQUET_PATH

    output_path = args.output
    if output_path is None:
        output_path = OD_OUTPUT_PATH if args.source == "od" else HOURLY_OUTPUT_PATH

    output = build_output(
        source=args.source,
        input_path=input_path,
        target_cluster_counts=sorted(set(args.cluster_counts)),
        year=args.year,
    )
    output_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    print(f"Source: {args.source}")
    print(f"Representative profiles clustered: {len(output['profiles'])}")
    print(f"Measures: {', '.join(output['cluster_runs'])}")


if __name__ == "__main__":
    main()
