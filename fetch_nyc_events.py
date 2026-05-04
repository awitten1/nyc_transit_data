import requests
import time
import pandas as pd
import logging
import math
import os
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

# Set DEBUG_SETLIST=1 when you want verbose API diagnostics.
logging.basicConfig(
    level=logging.DEBUG if os.getenv("DEBUG_SETLIST") == "1" else logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ==========================================
# CONFIGURATION
# ==========================================
# Setlist.fm API key. Provide via the SETLIST_FM_API_KEY environment variable
# or a .setlist_fm_key file in this directory (single line, no quotes).
def _load_setlist_fm_key():
    env_key = os.getenv("SETLIST_FM_API_KEY")
    if env_key:
        return env_key.strip()
    key_file = os.path.join(os.path.dirname(__file__), ".setlist_fm_key")
    if os.path.isfile(key_file):
        with open(key_file) as f:
            return f.read().strip()
    return ""

SETLIST_FM_API_KEY = _load_setlist_fm_key()

# Setlist.fm specific venue IDs
SETLIST_VENUES = {
    "Madison Square Garden": "23d63cc7",
    "Barclays Center": "2bd77066",
    "Yankee Stadium": "5bd38390",
    "Citi Field": "63d1eed3",
}

NY_TZ = ZoneInfo("America/New_York")
# ESPN public site API team slugs.
# For NBA/NHL, the API uses the championship year (2025 = 24-25 season, 2026 = 25-26 season).
# For NFL, the API uses the start year (2024 = Jan '25 games, 2025 = Fall '25 games).
ESPN_SPORTS_CONFIG = [
    {
        "league": "NBA",
        "sport_path": "basketball/nba",
        "seasons": ["2025", "2026"],
        "genre": "Sports - Basketball",
        "teams": {
            "ny": {"name": "New York Knicks", "venue": "Madison Square Garden"},
            "bkn": {"name": "Brooklyn Nets", "venue": "Barclays Center"},
        },
    },
    {
        "league": "WNBA",
        "sport_path": "basketball/wnba",
        "seasons": ["2025", "2026"],
        "genre": "Sports - Basketball",
        "teams": {
            "ny": {"name": "New York Liberty", "venue": "Barclays Center"},
        },
    },
    {
        "league": "NFL",
        "sport_path": "football/nfl",
        "seasons": ["2024", "2025", "2026"],
        "genre": "Sports - Football",
        "teams": {},
    },
    {
        "league": "NHL",
        "sport_path": "hockey/nhl",
        "seasons": ["2025", "2026"],
        "genre": "Sports - Hockey",
        "teams": {
            "nyr": {"name": "New York Rangers", "venue": "Madison Square Garden"},
            "nyi": {"name": "New York Islanders", "venue": "UBS Arena"},
        },
    },
]

NYC_PERMITTED_EVENTS_ENDPOINT = "https://data.cityofnewyork.us/resource/bkfu-528j.json"
NYC_MEGA_EVENT_QUERIES = [
    {
        "label": "New York City Marathon",
        "patterns": ["%NEW YORK CITY MARATHON%", "%TCS%NEW YORK CITY MARATHON%"],
    },
    {
        "label": "Macy's Thanksgiving Day Parade",
        "patterns": ["%THANKSGIVING DAY PARADE%"],
    },
    {
        "label": "NYC Pride March",
        "patterns": ["%PRIDE MARCH%", "%NYC PRIDE MARCH%"],
    },
    {
        "label": "Five Boro Bike Tour",
        "patterns": ["%FIVE BORO BIKE TOUR%", "%5 BORO BIKE TOUR%"],
    },
    {
        "label": "St. Patrick's Day Parade",
        "patterns": ["%ST PATRICK%PARADE%", "%ST. PATRICK%PARADE%"],
    },
    {
        "label": "Puerto Rican Day Parade",
        "patterns": ["%PUERTO RICAN DAY PARADE%"],
    },
    {
        "label": "Village Halloween Parade",
        "patterns": ["%VILLAGE HALLOWEEN PARADE%", "%NYC HALLOWEEN PARADE%"],
    },
    {
        "label": "Veterans Day Parade",
        "patterns": ["%VETERANS DAY PARADE%"],
    },
    {
        "label": "Columbus Day Parade",
        "patterns": ["%COLUMBUS DAY PARADE%", "%ITALIAN HERITAGE%PARADE%"],
    },
    {
        "label": "West Indian Day Parade",
        "patterns": ["%WEST INDIAN%PARADE%"],
    },
]

# These are high-impact city events that are either not normal street permits or
# are easier to model as multi-day/location impacts than as individual permit rows.
MEGA_EVENT_FALLBACKS = [
    {
        "event_name": "UN General Assembly (High-Level Week)",
        "date": "2025-09-23",
        "time": "08:00:00",
        "venue": "Midtown Manhattan",
        "genre": "Mega-Event - Gridlock Alert",
    },
    {
        "event_name": "New Year's Eve Times Square",
        "date": "2025-12-31",
        "time": "15:00:00",
        "venue": "Times Square",
        "genre": "Mega-Event - Gridlock Alert",
    },
]

NYC_PERMIT_SETUP_KEYWORDS = {
    "GRANDSTAND",
    "GRANDSTANDS",
    "PRODUCTION",
    "PARKING",
    "PACKET PICK UP",
    "BIKE RENTAL",
}


def nth_weekday(year, month, weekday, n):
    current = date(int(year), month, 1)
    days_until_weekday = (weekday - current.weekday()) % 7
    return current + timedelta(days=days_until_weekday + 7 * (n - 1))


def canonical_mega_events(year="2025"):
    thanksgiving = nth_weekday(year, 11, 3, 4)  # fourth Thursday in November
    return {
        "Macy's Thanksgiving Day Parade": {
            "event_name": "Macy's Thanksgiving Day Parade",
            "date": thanksgiving.strftime("%Y-%m-%d"),
            "time": "08:30:00",
            "venue": "Central Park West / Sixth Avenue",
            "genre": "Mega-Event - Parade",
        }
    }


def is_setup_only_permit(event_name):
    normalized = event_name.upper()
    return any(keyword in normalized for keyword in NYC_PERMIT_SETUP_KEYWORDS)


# ==========================================
# 1. SPORTS DATA (MLB Stats API - Free/No Auth)
# ==========================================
def format_api_datetime_local(raw_datetime):
    if not raw_datetime:
        return None, "19:00:00"

    normalized = raw_datetime.replace("Z", "+00:00")
    try:
        local_dt = datetime.fromisoformat(normalized).astimezone(NY_TZ)
    except ValueError:
        if "T" in raw_datetime:
            date_part, time_part = raw_datetime.split("T", 1)
            return date_part, time_part.replace("Z", "")[:8]
        return raw_datetime, "19:00:00"

    return local_dt.strftime("%Y-%m-%d"), local_dt.strftime("%H:%M:%S")


def fetch_mlb_home_games(year="2025"):
    print("Fetching MLB Schedule for Yankees and Mets...")
    # teamId 147 = Yankees, 121 = Mets
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&season={year}&teamId=147,121"
    response = requests.get(url)

    if response.status_code != 200:
        print("Failed to fetch MLB data.")
        return []

    data = response.json()
    mlb_events = []
    nyc_home_venues = {
        "New York Yankees": "Yankee Stadium",
        "New York Mets": "Citi Field",
    }

    for date_data in data.get("dates", []):
        for game in date_data.get("games", []):
            try:
                home_team = game["teams"]["home"]["team"]["name"]
                away_team = game["teams"]["away"]["team"]["name"]
                actual_venue = game.get("venue", {}).get("name", "")

                # Keep only true NYC home games. Spring training games can list the
                # Yankees/Mets as home while being played at Florida venues.
                expected_venue = nyc_home_venues.get(home_team)
                if expected_venue and actual_venue == expected_venue:
                    event_date, event_time = format_api_datetime_local(
                        game.get("gameDate", "")
                    )

                    if event_date is None or not event_date.startswith(str(year)):
                        continue

                    mlb_events.append(
                        {
                            "event_name": f"MLB: {away_team} at {home_team}",
                            "date": event_date,
                            "time": event_time,
                            "venue": actual_venue,
                            "genre": "Sports - Baseball",
                        }
                    )
            except KeyError:
                continue

    print(f"     Added {len(mlb_events)} MLB games at Yankee Stadium/Citi Field.")
    return mlb_events


def fetch_espn_home_games(year="2025", sports_config=None):
    print("Fetching NBA, WNBA, NFL, and NHL home schedules from ESPN Scoreboard...")
    sports_config = sports_config or ESPN_SPORTS_CONFIG
    espn_events = []
    seen_event_ids = set()

    # Generate the 12 month date ranges for the target year
    # ESPN Scoreboard requires the format YYYYMMDD-YYYYMMDD
    month_ranges = []
    for month in range(1, 13):
        start_date = f"{year}{month:02d}01"
        if month in [4, 6, 9, 11]:
            end_day = 30
        elif month == 2:
            y = int(year)
            # Catch leap years just in case
            end_day = 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28
        else:
            end_day = 31
        month_ranges.append(f"{start_date}-{year}{month:02d}{end_day}")

    for sport_config in sports_config:
        league = sport_config["league"]
        sport_path = sport_config["sport_path"]
        genre = sport_config["genre"]

        # Build a quick lookup for this league's target home teams
        # e.g., {"New York Knicks": "Madison Square Garden"}
        target_teams = {
            team_info["name"]: team_info["venue"]
            for team_slug, team_info in sport_config["teams"].items()
        }

        print(f"  -> Pulling {league} events for {year} by month...")
        league_start_count = len(espn_events)

        for date_range in month_ranges:
            url = (
                f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/scoreboard"
            )
            # Limit 1000 ensures we don't paginate out of busy mid-season months
            params = {"dates": date_range, "limit": 1000}

            try:
                response = requests.get(url, params=params, timeout=30)
            except requests.RequestException as exc:
                logger.error(f"ESPN request failed for {league} {date_range}: {exc}")
                continue

            if response.status_code != 200:
                continue

            try:
                data = response.json()
            except ValueError:
                continue

            events = data.get("events", [])
            for event in events:
                event_id = event.get("id")
                if event_id in seen_event_ids:
                    continue

                competition = (event.get("competitions") or [{}])[0]
                competitors = competition.get("competitors", [])

                home = next(
                    (c for c in competitors if c.get("homeAway") == "home"), None
                )
                away = next(
                    (c for c in competitors if c.get("homeAway") == "away"), None
                )

                home_team_name = (home or {}).get("team", {}).get("displayName")
                away_team_name = (away or {}).get("team", {}).get("displayName")

                # If this isn't a home game for one of our target teams, skip it
                if home_team_name not in target_teams:
                    continue

                # Force our target team's expected venue
                # This prevents dropping games if ESPN adds ", Brooklyn" to the venue string
                expected_venue = target_teams[home_team_name]

                # Double check the calendar year just in case ESPN leaks adjacent dates
                event_date, event_time = format_api_datetime_local(event.get("date"))
                if event_date is None or not event_date.startswith(str(year)):
                    continue

                espn_events.append(
                    {
                        "event_name": f"{league}: {away_team_name} at {home_team_name}",
                        "date": event_date,
                        "time": event_time,
                        "venue": expected_venue,
                        "genre": genre,
                    }
                )

                if event_id:
                    seen_event_ids.add(event_id)

            time.sleep(0.2)

        league_event_count = len(espn_events) - league_start_count
        print(f"     Added {league_event_count} {league} home games.")

    print(f"ESPN total sports events added: {len(espn_events)}")
    return espn_events


# ==========================================
# 2. CONCERT DATA (Setlist.fm API)
# ==========================================
def fetch_setlist_concerts(api_key, venues_dict, year="2025"):
    if not api_key or api_key == "YOUR_SETLIST_FM_KEY":
        print("Setlist.fm API key missing. Set SETLIST_FM_API_KEY env var "
              "or create a .setlist_fm_key file. Skipping concert data.")
        return []

    print("Fetching concert history from Setlist.fm...")
    headers = {"x-api-key": api_key, "Accept": "application/json"}
    debug_enabled = logger.isEnabledFor(logging.DEBUG)

    setlist_events = []

    for venue_name, venue_id in venues_dict.items():
        print(f"  -> Pulling {year} shows for {venue_name}...")
        venue_start_count = len(setlist_events)
        page = 1
        total_pages = 1

        if debug_enabled:
            venue_url = f"https://api.setlist.fm/rest/1.0/venue/{venue_id}"
            logger.debug("Validating venue id for %s: %s", venue_name, venue_url)
            try:
                venue_response = requests.get(venue_url, headers=headers, timeout=30)
                logger.debug(
                    "Venue lookup response for %s: status=%s elapsed=%.2fs",
                    venue_name,
                    venue_response.status_code,
                    venue_response.elapsed.total_seconds(),
                )
                if venue_response.status_code == 200:
                    venue_data = venue_response.json()
                    logger.debug(
                        "Venue lookup matched %s -> name=%r city=%r country=%r",
                        venue_name,
                        venue_data.get("name"),
                        venue_data.get("city", {}).get("name"),
                        venue_data.get("city", {}).get("country", {}).get("code"),
                    )
                else:
                    logger.debug(
                        "Venue lookup body for %s: %s",
                        venue_name,
                        venue_response.text[:500],
                    )
            except requests.RequestException as exc:
                logger.debug("Venue lookup failed for %s: %s", venue_name, exc)

        while page <= total_pages:
            url = "https://api.setlist.fm/rest/1.0/search/setlists"
            params = {"venueId": venue_id, "year": year, "p": page}
            logger.debug(
                "Requesting setlists: venue=%s venue_id=%s year=%s page=%s",
                venue_name,
                venue_id,
                year,
                page,
            )

            try:
                response = requests.get(url, headers=headers, params=params, timeout=30)
            except requests.RequestException as exc:
                logger.error(
                    "Setlist.fm request failed for %s page %s: %s",
                    venue_name,
                    page,
                    exc,
                )
                break

            logger.debug(
                "Setlist.fm response: url=%s status=%s elapsed=%.2fs",
                response.url,
                response.status_code,
                response.elapsed.total_seconds(),
            )

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                print(f"     Rate limit hit. Sleeping {retry_after or 3} seconds...")
                logger.debug("429 response body: %s", response.text[:500])
                if retry_after and retry_after.isdigit():
                    time.sleep(int(retry_after))
                else:
                    time.sleep(3)
                continue
            elif response.status_code == 404:
                # 404 on Setlist.fm usually means no results for that query, but the
                # response body can also reveal bad parameters such as invalid venueId.
                print(f"     No setlists found for {venue_name} in {year} (404).")
                print(f"     Response body: {response.text[:300]}")
                logger.debug(
                    "404 response body for %s: %s", venue_name, response.text[:500]
                )
                break
            elif response.status_code != 200:
                print(f"     Error fetching {venue_name}: {response.status_code}")
                print(f"     Response body: {response.text[:300]}")
                logger.debug(
                    "Non-200 response body for %s page %s: %s",
                    venue_name,
                    page,
                    response.text[:500],
                )
                break

            try:
                data = response.json()
            except ValueError:
                logger.error(
                    "Setlist.fm returned non-JSON for %s page %s: %s",
                    venue_name,
                    page,
                    response.text[:500],
                )
                break

            page_items = data.get("setlist", [])
            total = int(data.get("total", 0) or 0)
            items_per_page = int(data.get("itemsPerPage", 0) or 0)
            current_page = int(data.get("page", page) or page)
            logger.debug(
                "Parsed page: venue=%s page=%s returned=%s total=%s items_per_page=%s",
                venue_name,
                current_page,
                len(page_items),
                total,
                items_per_page,
            )

            for item in page_items:
                # Setlist.fm returns dates as DD-MM-YYYY. Convert to YYYY-MM-DD to match DuckDB/Altair standards.
                raw_date = item.get("eventDate", "")
                try:
                    formatted_date = datetime.strptime(raw_date, "%d-%m-%Y").strftime(
                        "%Y-%m-%d"
                    )
                except ValueError:
                    formatted_date = raw_date

                artist_name = item.get("artist", {}).get("name", "Unknown Artist")

                setlist_events.append(
                    {
                        "event_name": f"Concert: {artist_name}",
                        "date": formatted_date,
                        "time": "",
                        "venue": venue_name,
                        "genre": "Concert - Live Music",
                    }
                )

            if items_per_page:
                total_pages = max(1, math.ceil(total / items_per_page))
            else:
                total_pages = page

            logger.debug(
                "Pagination state for %s: page=%s total_pages=%s",
                venue_name,
                page,
                total_pages,
            )

            page += 1
            # Setlist API has a strict 2 requests/second limit
            time.sleep(0.6)

        venue_event_count = len(setlist_events) - venue_start_count
        print(f"     Added {venue_event_count} concert events for {venue_name}.")

    print(f"Setlist.fm total concert events added: {len(setlist_events)}")
    return setlist_events


# ==========================================
# 4. CITY-WIDE MEGA EVENTS (Static Data)
# ==========================================
def normalize_permit_location(raw_location, borough):
    if not raw_location:
        return borough or "NYC"

    first_location = raw_location.split(",")[0].strip()
    if len(first_location) > 90:
        first_location = f"{first_location[:87]}..."
    return first_location or borough or "NYC"


def classify_permitted_event(event_type, street_closure_type):
    event_type = event_type or "Permitted Event"
    closure = (street_closure_type or "").upper()

    if "FULL" in closure or event_type in {
        "Parade",
        "Athletic Race / Tour",
        "Street Festival",
        "Street Event",
    }:
        return "Mega-Event - Street Closure"

    return "Mega-Event - Permitted Event"


def permit_name_matches_patterns(event_name, patterns):
    normalized_name = event_name.upper()
    for pattern in patterns:
        # Convert simple SQL LIKE patterns into ordered substring checks.
        parts = [part for part in pattern.upper().split("%") if part]
        position = 0
        matched = True
        for part in parts:
            found_at = normalized_name.find(part, position)
            if found_at == -1:
                matched = False
                break
            position = found_at + len(part)
        if matched:
            return True
    return False


def fetch_nyc_permitted_mega_events(year="2025"):
    print("Fetching NYC permitted mega-events from NYC Open Data...")
    start = f"{year}-01-01T00:00:00"
    end = f"{int(year) + 1}-01-01T00:00:00"
    where_clause = f'start_date_time between "{start}" and "{end}"'
    base_params = {
        "$limit": 200,
        "$select": (
            "event_id,event_name,start_date_time,event_type,event_borough,"
            "event_location,street_closure_type"
        ),
        "$where": where_clause,
        "$order": "start_date_time ASC",
    }

    rows = []
    seen_row_keys = set()
    failed_queries = []
    for query in NYC_MEGA_EVENT_QUERIES:
        label = query["label"]
        params = {**base_params, "$q": label}
        logger.debug("Requesting NYC Open Data permits for %s", label)

        try:
            response = requests.get(
                NYC_PERMITTED_EVENTS_ENDPOINT,
                params=params,
                timeout=20,
            )
        except requests.RequestException as exc:
            failed_queries.append(label)
            logger.error("NYC Open Data request failed for %s: %s", label, exc)
            continue

        logger.debug(
            "NYC Open Data response: query=%s url=%s status=%s elapsed=%.2fs",
            label,
            response.url,
            response.status_code,
            response.elapsed.total_seconds(),
        )

        if response.status_code != 200:
            failed_queries.append(label)
            print(f"     NYC Open Data error for {label}: {response.status_code}")
            print(f"     Response body: {response.text[:300]}")
            continue

        try:
            query_rows = response.json()
        except ValueError:
            failed_queries.append(label)
            logger.error(
                "NYC Open Data returned non-JSON for %s: %s",
                label,
                response.text[:500],
            )
            continue

        matched_rows = [
            row
            for row in query_rows
            if permit_name_matches_patterns(
                row.get("event_name", ""),
                query["patterns"],
            )
        ]
        print(f"     {label}: {len(matched_rows)} matched rows from {len(query_rows)}")
        for row in matched_rows:
            row_key = (
                row.get("event_id"),
                row.get("event_name"),
                row.get("start_date_time"),
            )
            if row_key in seen_row_keys:
                continue
            row["_mega_query_label"] = label
            rows.append(row)
            seen_row_keys.add(row_key)

    mega_events = []
    seen_event_ids = set()
    canonical_events = canonical_mega_events(year)

    for row in rows:
        event_id = row.get("event_id")
        if event_id and event_id in seen_event_ids:
            continue

        event_date, event_time = format_api_datetime_local(row.get("start_date_time"))
        if event_date is None or not event_date.startswith(str(year)):
            continue

        event_name = row.get("event_name", "NYC Permitted Event").strip()
        query_label = row.get("_mega_query_label")
        canonical_event = canonical_events.get(query_label)
        if canonical_event and (
            event_date != canonical_event["date"] or is_setup_only_permit(event_name)
        ):
            logger.info(
                "Skipping setup/non-event-date permit for %s: %s on %s",
                query_label,
                event_name,
                event_date,
            )
            continue
        event_type = row.get("event_type", "")
        street_closure_type = row.get("street_closure_type", "")
        borough = row.get("event_borough", "")
        venue = normalize_permit_location(row.get("event_location", ""), borough)

        mega_events.append(
            {
                "event_name": f"NYC Permit: {event_name}",
                "date": event_date,
                "time": event_time,
                "venue": venue,
                "genre": classify_permitted_event(event_type, street_closure_type),
            }
        )

        if event_id:
            seen_event_ids.add(event_id)

    fallback_keys = {(event["event_name"], event["date"]) for event in mega_events}
    for event in canonical_events.values():
        if (event["event_name"], event["date"]) not in fallback_keys:
            mega_events.append(event)
            fallback_keys.add((event["event_name"], event["date"]))

    for event in MEGA_EVENT_FALLBACKS:
        if (event["event_name"], event["date"]) not in fallback_keys:
            mega_events.append(event)

    print(
        f"     Added {len(mega_events)} NYC permitted/fallback mega-events "
        f"from {len(rows)} permit rows."
    )
    if failed_queries:
        print(
            "     NYC Open Data queries that failed or timed out: "
            + ", ".join(failed_queries)
        )
    return mega_events


# ==========================================
# EXECUTION PIPELINE
# ==========================================
if __name__ == "__main__":
    all_events = []

    # 1. Fetch MLB Sports Data
    all_events.extend(fetch_mlb_home_games("2025"))
    all_events.extend(fetch_espn_home_games())

    # 2. Fetch Concert Data
    all_events.extend(
        fetch_setlist_concerts(SETLIST_FM_API_KEY, SETLIST_VENUES, "2025")
    )

    # 4. Fetch City-Wide Mega Events
    all_events.extend(fetch_nyc_permitted_mega_events("2025"))

    # Create DataFrame
    df_events = pd.DataFrame(all_events)

    if not df_events.empty:
        # Sort chronologically
        df_events = df_events.sort_values(by=["date", "time"]).drop_duplicates(
            subset=["event_name", "date", "time", "venue", "genre"]
        )

        print(f"\nSuccessfully compiled {len(df_events)} major events for 2025.")
        print(df_events.head())

        # Save to CSV
        output_file = "nyc_unified_events_2025.csv"
        df_events.to_csv(output_file, index=False)
        print(f"\nData saved to {output_file}. Ready for clustering join!")
    else:
        print("\nNo events were compiled. Check your API key and network connection.")
