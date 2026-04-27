"""Build station-level daily clustering and anomaly summaries from hourly subway ridership.

The hourly parquet has literal timestamps, so this workflow uses one 24-hour
profile per station per calendar date. For each station we:

1. cluster daily hourly curves into a small set of day types;
2. compute anomaly scores using both distance-to-cluster and deviation from a
   weekday baseline;
3. write one JSON file per station plus a shared index for the browser UI.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parent
HOURLY_PARQUET_PATH = ROOT / "data" / "subway_hourly_2025.parquet"
OUTPUT_DIR = ROOT / "data" / "station_daytype_anomalies"
DUCKDB_BIN = shutil.which("duckdb")

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
HOUR_LABELS = [
    "12 AM",
    "1 AM",
    "2 AM",
    "3 AM",
    "4 AM",
    "5 AM",
    "6 AM",
    "7 AM",
    "8 AM",
    "9 AM",
    "10 AM",
    "11 AM",
    "12 PM",
    "1 PM",
    "2 PM",
    "3 PM",
    "4 PM",
    "5 PM",
    "6 PM",
    "7 PM",
    "8 PM",
    "9 PM",
    "10 PM",
    "11 PM",
]


@dataclass
class DailyProfile:
    service_date: str
    month: int
    month_name: str
    day_of_month: int
    day_of_week: str
    hourly_ridership: np.ndarray

    @property
    def label(self) -> str:
        return f"{self.month_name} {self.day_of_month}, {self.service_year}"

    @property
    def service_year(self) -> int:
        return int(self.service_date[:4])

    @property
    def total_ridership(self) -> float:
        return float(self.hourly_ridership.sum())

    @property
    def peak_hour(self) -> int:
        return int(self.hourly_ridership.argmax())


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


def run_duckdb_command(query: str) -> None:
    if not DUCKDB_BIN:
        raise RuntimeError("The `duckdb` CLI is required but was not found on PATH.")
    subprocess.run([DUCKDB_BIN, "-c", query], check=True)


def export_station_day_hour_rows(parquet_path: Path, year: int, csv_path: Path) -> None:
    parquet_literal = parquet_path.as_posix().replace("'", "''")
    csv_literal = csv_path.as_posix().replace("'", "''")
    expected_days = 366 if calendar.isleap(year) else 365
    query = f"""
    COPY (
      WITH station_hourly AS (
        SELECT
          TRY_CAST(station_complex_id AS INTEGER) AS station_id,
          station_complex,
          borough,
          CAST(transit_timestamp AS DATE) AS service_date,
          CAST(EXTRACT('month' FROM transit_timestamp) AS INTEGER) AS month_num,
          CAST(EXTRACT('day' FROM transit_timestamp) AS INTEGER) AS day_of_month,
          {weekday_name_sql("transit_timestamp")} AS day_of_week,
          CAST(EXTRACT('hour' FROM transit_timestamp) AS INTEGER) AS hour_of_day,
          SUM(ridership) AS ridership
        FROM read_parquet('{parquet_literal}')
        WHERE transit_mode = 'subway'
          AND transit_timestamp >= TIMESTAMP '{year}-01-01'
          AND transit_timestamp < TIMESTAMP '{year + 1}-01-01'
          AND TRY_CAST(station_complex_id AS INTEGER) IS NOT NULL
        GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
      ),
      eligible_stations AS (
        SELECT station_id
        FROM station_hourly
        GROUP BY 1
        HAVING COUNT(DISTINCT service_date) >= {expected_days}
      )
      SELECT
        station_hourly.station_id,
        station_hourly.station_complex,
        station_hourly.borough,
        CAST(station_hourly.service_date AS VARCHAR) AS service_date,
        station_hourly.month_num,
        station_hourly.day_of_month,
        station_hourly.day_of_week,
        station_hourly.hour_of_day,
        station_hourly.ridership
      FROM station_hourly
      JOIN eligible_stations USING (station_id)
      ORDER BY station_hourly.station_id, station_hourly.service_date, station_hourly.hour_of_day
    ) TO '{csv_literal}' WITH (FORMAT CSV, HEADER TRUE)
    """
    run_duckdb_command(query)


def robust_scale(values: np.ndarray, floor: float = 0.1) -> float:
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    return max(1.4826 * mad, floor)


def standardize_columns(values: np.ndarray) -> np.ndarray:
    means = values.mean(axis=0)
    stds = values.std(axis=0)
    stds[stds < 1e-6] = 1.0
    return (values - means) / stds


def kmeans_plus_plus_init(features: np.ndarray, cluster_count: int, rng: np.random.Generator) -> np.ndarray:
    sample_count = features.shape[0]
    first_index = int(rng.integers(sample_count))
    centroids = [features[first_index]]
    closest_sq = np.sum((features - centroids[0]) ** 2, axis=1)

    for _ in range(1, cluster_count):
        total = float(closest_sq.sum())
        if total <= 0:
            next_index = int(rng.integers(sample_count))
        else:
            probabilities = closest_sq / total
            next_index = int(rng.choice(sample_count, p=probabilities))
        centroids.append(features[next_index])
        next_sq = np.sum((features - centroids[-1]) ** 2, axis=1)
        closest_sq = np.minimum(closest_sq, next_sq)

    return np.vstack(centroids)


def kmeans(features: np.ndarray, cluster_count: int, *, seed: int = 2025, n_init: int = 4, max_iter: int = 40) -> tuple[np.ndarray, np.ndarray]:
    sample_count = features.shape[0]
    if sample_count < cluster_count:
        raise ValueError("Cluster count cannot exceed sample count.")

    best_labels = None
    best_centroids = None
    best_inertia = float("inf")
    rng = np.random.default_rng(seed)

    for init_index in range(n_init):
        centroids = kmeans_plus_plus_init(features, cluster_count, np.random.default_rng(rng.integers(1_000_000_000)))
        labels = np.zeros(sample_count, dtype=int)

        for _ in range(max_iter):
            distances = np.sum((features[:, None, :] - centroids[None, :, :]) ** 2, axis=2)
            next_labels = np.argmin(distances, axis=1)
            if np.array_equal(next_labels, labels):
                break
            labels = next_labels

            next_centroids = centroids.copy()
            for cluster_id in range(cluster_count):
                member_indexes = np.where(labels == cluster_id)[0]
                if len(member_indexes) == 0:
                    farthest_index = int(np.argmax(np.min(distances, axis=1)))
                    next_centroids[cluster_id] = features[farthest_index]
                else:
                    next_centroids[cluster_id] = features[member_indexes].mean(axis=0)

            if np.allclose(next_centroids, centroids, atol=1e-6):
                centroids = next_centroids
                break
            centroids = next_centroids

        distances = np.sum((features[:, None, :] - centroids[None, :, :]) ** 2, axis=2)
        labels = np.argmin(distances, axis=1)
        inertia = float(np.sum(np.min(distances, axis=1)))
        if inertia < best_inertia:
            best_inertia = inertia
            best_labels = labels.copy()
            best_centroids = centroids.copy()

    if best_labels is None or best_centroids is None:
        raise RuntimeError("K-means failed to produce a result.")
    return best_labels, best_centroids


def cluster_descriptor(average_profile: np.ndarray, weekday_counts: Dict[str, int]) -> str:
    total = float(average_profile.sum())
    if total <= 0:
        return "Low activity"

    peak_hour = int(average_profile.argmax())
    morning_share = float(average_profile[6:10].sum() / total)
    midday_share = float(average_profile[10:15].sum() / total)
    evening_share = float(average_profile[15:20].sum() / total)
    late_share = float((average_profile[:3].sum() + average_profile[21:].sum()) / total)
    weekend_share = (
        weekday_counts.get("Saturday", 0) + weekday_counts.get("Sunday", 0)
    ) / max(sum(weekday_counts.values()), 1)

    if late_share > 0.22 and (peak_hour >= 21 or peak_hour <= 2):
        core = "late-night spike"
    elif evening_share >= max(morning_share, midday_share) and peak_hour >= 15:
        core = "evening peak"
    elif morning_share > evening_share and peak_hour <= 10:
        core = "morning commute"
    elif midday_share > max(morning_share, evening_share):
        core = "midday-heavy"
    else:
        core = "balanced day"

    if weekend_share >= 0.45:
        return f"Weekend {core}"
    if weekend_share <= 0.2:
        return f"Weekday {core}"
    return f"Mixed {core}"


def percentile_ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(len(values), dtype=float)
    if len(values) <= 1:
        return np.zeros(len(values), dtype=float)
    return 100.0 * ranks / (len(values) - 1)


def build_cluster_run(
    profiles: Sequence[DailyProfile],
    *,
    cluster_count: int,
    top_anomaly_count: int,
    features: np.ndarray,
    totals: np.ndarray,
    residual_component: np.ndarray,
    peak_component: np.ndarray,
) -> dict:
    profile_keys = [profile.service_date for profile in profiles]
    vectors = np.vstack([profile.hourly_ridership for profile in profiles])
    cluster_count = min(cluster_count, len(profiles))
    labels, centroids = kmeans(features, cluster_count)

    counts = np.bincount(labels, minlength=cluster_count)
    cluster_order = sorted(range(cluster_count), key=lambda cluster_id: (-counts[cluster_id], cluster_id))
    label_map = {old_id: new_id for new_id, old_id in enumerate(cluster_order)}
    remapped_labels = np.array([label_map[label] for label in labels], dtype=int)
    remapped_centroids = np.vstack([centroids[old_id] for old_id in cluster_order])

    cluster_distances = np.sqrt(np.sum((features - remapped_centroids[remapped_labels]) ** 2, axis=1))
    cluster_distance_component = np.maximum(
        0.0,
        (cluster_distances - float(np.median(cluster_distances))) / robust_scale(cluster_distances),
    )
    anomaly_raw = 0.55 * cluster_distance_component + 0.35 * residual_component + 0.10 * peak_component
    anomaly_percentile = percentile_ranks(anomaly_raw)

    cluster_summaries = []
    cluster_labels = {}
    for cluster_id in range(cluster_count):
        member_indexes = np.where(remapped_labels == cluster_id)[0]
        member_profiles = [profiles[index] for index in member_indexes]
        average_profile = np.mean(vectors[member_indexes], axis=0)
        weekday_counts: Dict[str, int] = {}
        month_counts: Dict[int, int] = {}
        for profile in member_profiles:
            weekday_counts[profile.day_of_week] = weekday_counts.get(profile.day_of_week, 0) + 1
            month_counts[profile.month] = month_counts.get(profile.month, 0) + 1

        label = cluster_descriptor(average_profile, weekday_counts)
        cluster_labels[cluster_id] = label
        cluster_summaries.append(
            {
                "cluster_id": cluster_id,
                "label": label,
                "size": len(member_indexes),
                "average_profile": [round(float(value), 4) for value in average_profile.tolist()],
                "average_total_ridership": round(float(np.mean(totals[member_indexes])), 4),
                "average_peak_hour": round(float(np.mean([profiles[index].peak_hour for index in member_indexes])), 2),
                "weekday_counts": [
                    {"day_of_week": day, "count": weekday_counts.get(day, 0)}
                    for day in WEEKDAY_ORDER
                    if weekday_counts.get(day, 0) > 0
                ],
                "month_counts": [
                    {"month": month, "month_name": MONTH_NAMES[month], "count": month_counts[month]}
                    for month in sorted(month_counts)
                ],
                "representative_date": profiles[int(member_indexes[np.argmin(cluster_distances[member_indexes])])].service_date,
            }
        )

    profile_annotations = {}
    anomalies = []
    for index, profile in enumerate(profiles):
        annotation = {
            "cluster_id": int(remapped_labels[index]),
            "cluster_label": cluster_labels[int(remapped_labels[index])],
            "cluster_distance": round(float(cluster_distances[index]), 6),
            "anomaly_score_raw": round(float(anomaly_raw[index]), 6),
            "anomaly_score": round(float(anomaly_percentile[index]), 2),
        }
        profile_annotations[profile_keys[index]] = annotation
        anomalies.append(
            {
                "profile_key": profile.service_date,
                "cluster_id": annotation["cluster_id"],
                "cluster_label": annotation["cluster_label"],
                "cluster_distance": annotation["cluster_distance"],
                "anomaly_score_raw": annotation["anomaly_score_raw"],
                "anomaly_score": annotation["anomaly_score"],
            }
        )

    return {
        "cluster_count": cluster_count,
        "cluster_summaries": cluster_summaries,
        "profile_annotations": profile_annotations,
        "top_anomalies": anomalies[:top_anomaly_count],
    }


def build_station_payload(
    station_id: int,
    station_name: str,
    borough: str,
    profiles: Sequence[DailyProfile],
    *,
    cluster_counts: Sequence[int],
    default_cluster_count: int,
    top_anomaly_count: int,
) -> tuple[dict, dict]:
    vectors = np.vstack([profile.hourly_ridership for profile in profiles])
    log_vectors = np.log1p(vectors)
    features = standardize_columns(log_vectors)
    totals = np.sum(vectors, axis=1)

    weekday_baselines: Dict[str, np.ndarray] = {}
    weekday_scales: Dict[str, np.ndarray] = {}
    for weekday in WEEKDAY_ORDER:
        indexes = [index for index, profile in enumerate(profiles) if profile.day_of_week == weekday]
        weekday_vectors = vectors[indexes]
        baseline = np.median(weekday_vectors, axis=0)
        mad = np.median(np.abs(weekday_vectors - baseline), axis=0)
        scale = np.maximum.reduce(
            [
                1.4826 * mad,
                np.sqrt(np.maximum(baseline, 1.0)),
                np.full(24, 3.0, dtype=float),
            ]
        )
        weekday_baselines[weekday] = baseline
        weekday_scales[weekday] = scale

    residual_z = np.vstack(
        [
            (profile.hourly_ridership - weekday_baselines[profile.day_of_week]) / weekday_scales[profile.day_of_week]
            for profile in profiles
        ]
    )
    residual_component = np.mean(np.maximum(np.abs(residual_z) - 2.5, 0.0), axis=1)
    peak_component = np.maximum(0.0, np.max(np.abs(residual_z), axis=1) - 3.0)

    profile_rows = []
    base_anomaly_info = {}
    for index, profile in enumerate(profiles):
        hourly = profile.hourly_ridership
        baseline = weekday_baselines[profile.day_of_week]
        z_scores = residual_z[index]
        anomaly_direction = "mixed"
        positive_excess = float(np.maximum(z_scores, 0.0).sum())
        negative_excess = float(np.maximum(-z_scores, 0.0).sum())
        if positive_excess > negative_excess * 1.15:
            anomaly_direction = "surge"
        elif negative_excess > positive_excess * 1.15:
            anomaly_direction = "drop"

        top_hour_indexes = np.argsort(np.abs(z_scores))[::-1][:4]
        top_hours = [
            {
                "hour": int(hour),
                "label": HOUR_LABELS[int(hour)],
                "z_score": round(float(z_scores[int(hour)]), 3),
                "actual_ridership": int(round(float(hourly[int(hour)]))),
                "expected_ridership": round(float(baseline[int(hour)]), 4),
                "direction": "surge" if z_scores[int(hour)] >= 0 else "drop",
            }
            for hour in top_hour_indexes
        ]

        profile_rows.append(
            {
                "service_date": profile.service_date,
                "profile_key": profile.service_date,
                "label": f"{profile.month_name} {profile.day_of_month}, {profile.service_year}",
                "month": profile.month,
                "month_name": profile.month_name,
                "day_of_month": profile.day_of_month,
                "day_of_week": profile.day_of_week,
                "hourly_ridership": [int(round(float(value))) for value in hourly.tolist()],
                "total_ridership": int(round(profile.total_ridership)),
                "peak_hour": profile.peak_hour,
                "weekday_residual_score": round(float(residual_component[index]), 6),
                "anomaly_direction": anomaly_direction,
                "top_anomalous_hours": top_hours,
            }
        )
        base_anomaly_info[profile.service_date] = {
            "service_date": profile.service_date,
            "profile_key": profile.service_date,
            "label": f"{profile.month_name} {profile.day_of_month}, {profile.service_year}",
            "month": profile.month,
            "month_name": profile.month_name,
            "day_of_month": profile.day_of_month,
            "day_of_week": profile.day_of_week,
            "total_ridership": int(round(profile.total_ridership)),
            "peak_hour": profile.peak_hour,
            "weekday_residual_score": round(float(residual_component[index]), 6),
            "anomaly_direction": anomaly_direction,
            "top_anomalous_hours": top_hours,
        }

    valid_cluster_counts = sorted({min(int(count), len(profiles)) for count in cluster_counts if int(count) >= 2})
    if not valid_cluster_counts:
        raise RuntimeError("At least one cluster count >= 2 is required.")
    default_cluster_count = default_cluster_count if default_cluster_count in valid_cluster_counts else valid_cluster_counts[0]

    cluster_runs = {}
    for cluster_count in valid_cluster_counts:
        run = build_cluster_run(
            profiles,
            cluster_count=cluster_count,
            top_anomaly_count=top_anomaly_count,
            features=features,
            totals=totals,
            residual_component=residual_component,
            peak_component=peak_component,
        )
        top_anomalies = []
        for item in run["top_anomalies"]:
            combined = dict(base_anomaly_info[item["profile_key"]])
            combined.update(item)
            top_anomalies.append(combined)
        run["top_anomalies"] = top_anomalies
        cluster_runs[str(cluster_count)] = run

    default_run = cluster_runs[str(default_cluster_count)]
    station_payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "station": {
            "station_id": station_id,
            "label": station_name,
            "borough": borough,
        },
        "dataset_semantics": {
            "source": "subway_hourly_2025 parquet",
            "profile_unit": "calendar_date",
            "profile_definition": "One 24-hour station entry profile per actual 2025 calendar date.",
            "anomaly_definition": "Combination of distance to nearest learned day-type cluster and deviation from a weekday baseline.",
            "profile_count": len(profiles),
            "calendar_year": profiles[0].service_year if profiles else None,
            "available_cluster_counts": valid_cluster_counts,
            "default_cluster_count": default_cluster_count,
        },
        "profiles": profile_rows,
        "cluster_runs": cluster_runs,
    }

    index_row = {
        "station_id": station_id,
        "label": station_name,
        "borough": borough,
        "profile_count": len(profiles),
        "avg_daily_ridership": round(float(np.mean(totals)), 4),
        "peak_daily_ridership": round(float(np.max(totals)), 4),
        "default_cluster_count": default_cluster_count,
        "top_anomaly": {
            "service_date": default_run["top_anomalies"][0]["service_date"],
            "label": default_run["top_anomalies"][0]["label"],
            "anomaly_score": default_run["top_anomalies"][0]["anomaly_score"],
            "cluster_label": default_run["top_anomalies"][0]["cluster_label"],
        },
        "output_path": f"stations/{station_id}.json",
    }
    return station_payload, index_row


def parse_station_rows(csv_path: Path) -> Iterable[tuple[int, str, str, List[DailyProfile]]]:
    current_station_id = None
    current_station_name = None
    current_borough = None
    profiles_by_date: Dict[str, dict] = {}

    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            station_id = int(row["station_id"])
            if current_station_id is None:
                current_station_id = station_id
                current_station_name = row["station_complex"]
                current_borough = row["borough"]

            if station_id != current_station_id:
                yield (
                    current_station_id,
                    current_station_name or "",
                    current_borough or "",
                    finalize_profiles(profiles_by_date),
                )
                profiles_by_date = {}
                current_station_id = station_id
                current_station_name = row["station_complex"]
                current_borough = row["borough"]

            service_date = row["service_date"]
            month_num = int(row["month_num"])
            day_of_month = int(row["day_of_month"])
            bucket = profiles_by_date.setdefault(
                service_date,
                {
                    "service_date": service_date,
                    "month": month_num,
                    "day_of_month": day_of_month,
                    "day_of_week": row["day_of_week"],
                    "hourly_ridership": np.zeros(24, dtype=float),
                },
            )
            bucket["hourly_ridership"][int(row["hour_of_day"])] = float(row["ridership"])

    if current_station_id is not None:
        yield (
            current_station_id,
            current_station_name or "",
            current_borough or "",
            finalize_profiles(profiles_by_date),
        )


def finalize_profiles(profiles_by_date: Dict[str, dict]) -> List[DailyProfile]:
    profiles = []
    for item in sorted(profiles_by_date.values(), key=lambda value: value["service_date"]):
        profiles.append(
            DailyProfile(
                service_date=item["service_date"],
                month=item["month"],
                month_name=MONTH_NAMES[item["month"]],
                day_of_month=item["day_of_month"],
                day_of_week=item["day_of_week"],
                hourly_ridership=item["hourly_ridership"],
            )
        )
    return profiles


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=HOURLY_PARQUET_PATH,
        help=f"Hourly parquet path (default: {HOURLY_PARQUET_PATH})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help=f"Output directory (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=2025,
        help="Calendar year to analyze.",
    )
    parser.add_argument(
        "--cluster-counts",
        type=int,
        nargs="+",
        default=[3, 4, 5, 6],
        help="Cluster counts to materialize per station, e.g. --cluster-counts 3 4 5 6",
    )
    parser.add_argument(
        "--default-cluster-count",
        type=int,
        default=4,
        help="Default cluster count for the browser view.",
    )
    parser.add_argument(
        "--top-anomalies",
        type=int,
        default=20,
        help="How many anomalous dates to keep in each station summary.",
    )
    parser.add_argument(
        "--limit-stations",
        type=int,
        default=None,
        help="Optional cap on how many stations to process; useful for smoke tests.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stations_dir = args.output_dir / "stations"
    stations_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="station_daytype_") as tmp_dir:
        csv_path = Path(tmp_dir) / "station_day_hour_rows.csv"
        print("Exporting hourly station-day rows from parquet...")
        export_station_day_hour_rows(args.input, args.year, csv_path)

        index_rows = []
        processed_count = 0
        for station_id, station_name, borough, profiles in parse_station_rows(csv_path):
            station_payload, index_row = build_station_payload(
                station_id,
                station_name,
                borough,
                profiles,
                cluster_counts=args.cluster_counts,
                default_cluster_count=args.default_cluster_count,
                top_anomaly_count=args.top_anomalies,
            )
            output_path = stations_dir / f"{station_id}.json"
            output_path.write_text(json.dumps(station_payload, indent=2) + "\n", encoding="utf-8")
            index_rows.append(index_row)

            processed_count += 1
            if processed_count % 25 == 0:
                print(f"Processed {processed_count} stations...")
            if args.limit_stations is not None and processed_count >= args.limit_stations:
                break

    index_payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_semantics": {
            "source": "subway_hourly_2025 parquet",
            "calendar_year": args.year,
            "profile_unit": "calendar_date",
            "available_cluster_counts": sorted(set(args.cluster_counts)),
            "default_cluster_count": args.default_cluster_count,
            "anomaly_definition": "Distance-to-cluster plus weekday-baseline residual score.",
        },
        "stations": sorted(index_rows, key=lambda row: row["label"]),
    }
    (args.output_dir / "index.json").write_text(json.dumps(index_payload, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote station anomaly index to {args.output_dir / 'index.json'}")
    print(f"Stations processed: {len(index_rows)}")


if __name__ == "__main__":
    main()
