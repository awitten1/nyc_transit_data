#!/bin/bash
#
# fetch_nyc_weather.sh
# Fetches daily weather data for NYC (Central Park) from the Open-Meteo API
# and saves it as a CSV file.
#
# Usage: ./fetch_nyc_weather.sh [start_date] [end_date] [output_file]
#   start_date  - Start date in YYYY-MM-DD format (default: 2020-01-01)
#   end_date    - End date in YYYY-MM-DD format (default: yesterday)
#   output_file - Output CSV filename (default: nyc_daily_weather.csv)
#
# Requirements: curl, duckdb
# API: Open-Meteo (free, no API key needed for non-commercial use)
#
# Example: ./fetch_nyc_weather.sh 2025-01-01 2026-03-01 nyc_weather_data.csv
#

set -euo pipefail

# --- Configuration ---
LATITUDE=40.7831
LONGITUDE=-73.9712
TIMEZONE="America/New_York"
API_BASE="https://archive-api.open-meteo.com/v1/archive"

# Daily variables to fetch
DAILY_VARS="temperature_2m_max,temperature_2m_min,temperature_2m_mean"
DAILY_VARS+=",apparent_temperature_max,apparent_temperature_min"
DAILY_VARS+=",precipitation_sum,rain_sum,snowfall_sum"
DAILY_VARS+=",windspeed_10m_max,windgusts_10m_max,winddirection_10m_dominant"
DAILY_VARS+=",shortwave_radiation_sum"
DAILY_VARS+=",weathercode"

# --- Parse arguments ---
START_DATE="${1:-2020-01-01}"
# Default end date: yesterday (works on both Linux and macOS)
if date -d 'yesterday' '+%Y-%m-%d' &>/dev/null; then
    DEFAULT_END=$(date -d 'yesterday' '+%Y-%m-%d')
else
    DEFAULT_END=$(date -v-1d '+%Y-%m-%d')
fi
END_DATE="${2:-$DEFAULT_END}"
OUTPUT_FILE="${3:-nyc_daily_weather.csv}"

echo "============================================"
echo "  NYC Daily Weather Data Fetcher"
echo "============================================"
echo "  Location:  NYC Central Park (${LATITUDE}, ${LONGITUDE})"
echo "  Period:    ${START_DATE} to ${END_DATE}"
echo "  Output:    ${OUTPUT_FILE}"
echo "============================================"
echo ""

# --- Check dependencies ---
for cmd in curl duckdb; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Error: '${cmd}' is required but not installed."
        echo "  curl:   sudo apt install curl"
        echo "  duckdb: https://duckdb.org/docs/installation"
        exit 1
    fi
done

# --- Build the API URL ---
URL="${API_BASE}?latitude=${LATITUDE}&longitude=${LONGITUDE}"
URL+="&start_date=${START_DATE}&end_date=${END_DATE}"
URL+="&daily=${DAILY_VARS}"
URL+="&timezone=${TIMEZONE}"
URL+="&temperature_unit=fahrenheit"
URL+="&windspeed_unit=mph"
URL+="&precipitation_unit=inch"

# --- Fetch data ---
TMPJSON="/tmp/nyc_weather_response.json"

echo "Fetching data from Open-Meteo API..."
HTTP_CODE=$(curl -s -o "$TMPJSON" -w "%{http_code}" "$URL")

if [ "$HTTP_CODE" -ne 200 ]; then
    echo "Error: API returned HTTP ${HTTP_CODE}"
    cat "$TMPJSON"
    rm -f "$TMPJSON"
    exit 1
fi

echo "Data received successfully."

# --- Transform JSON to CSV with DuckDB ---
echo "Transforming with DuckDB..."

duckdb -c "
    COPY (
        WITH raw AS (
            SELECT daily FROM read_json_auto('${TMPJSON}')
        ),
        unnested AS (
            SELECT
                unnest(daily.time)                         AS date,
                unnest(daily.temperature_2m_max)           AS temp_max_f,
                unnest(daily.temperature_2m_min)           AS temp_min_f,
                unnest(daily.temperature_2m_mean)          AS temp_mean_f,
                unnest(daily.apparent_temperature_max)     AS feels_like_max_f,
                unnest(daily.apparent_temperature_min)     AS feels_like_min_f,
                unnest(daily.precipitation_sum)            AS precipitation_in,
                unnest(daily.rain_sum)                     AS rain_in,
                unnest(daily.snowfall_sum)                 AS snowfall_in,
                unnest(daily.windspeed_10m_max)            AS wind_max_mph,
                unnest(daily.windgusts_10m_max)            AS wind_gust_max_mph,
                unnest(daily.winddirection_10m_dominant)   AS wind_dir_deg,
                unnest(daily.shortwave_radiation_sum)      AS solar_radiation_mjm2,
                unnest(daily.weathercode)                  AS weather_code
            FROM raw
        )
        SELECT * FROM unnested
        ORDER BY date
    ) TO '${OUTPUT_FILE}' (HEADER, DELIMITER ',');
"

# Clean up
rm -f "$TMPJSON"

NUM_ROWS=$(($(wc -l < "$OUTPUT_FILE") - 1))

echo ""
echo "Done! Saved ${NUM_ROWS} days of data to ${OUTPUT_FILE}"
echo ""
echo "Columns:"
echo "  date               - Date (YYYY-MM-DD)"
echo "  temp_max_f         - Max air temperature (°F)"
echo "  temp_min_f         - Min air temperature (°F)"
echo "  temp_mean_f        - Mean air temperature (°F)"
echo "  feels_like_max_f   - Max feels-like temperature (°F)"
echo "  feels_like_min_f   - Min feels-like temperature (°F)"
echo "  precipitation_in   - Total precipitation (inches)"
echo "  rain_in            - Rainfall only (inches)"
echo "  snowfall_in        - Snowfall (inches)"
echo "  wind_max_mph       - Max wind speed (mph)"
echo "  wind_gust_max_mph  - Max wind gusts (mph)"
echo "  wind_dir_deg       - Dominant wind direction (degrees)"
echo "  solar_radiation    - Solar radiation (MJ/m²)"
echo "  weather_code       - WMO weather interpretation code"
echo ""
echo "Preview (first 5 rows):"
head -6 "$OUTPUT_FILE" | column -t -s','