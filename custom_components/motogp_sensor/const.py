from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "motogp_sensor"
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.SWITCH,
    Platform.SELECT,
]

# ── Live source options ──────────────────────────────────────────────────────
LIVE_SOURCE_PULSELIVE = "pulselive"
LIVE_SOURCE_OFFICIAL = "official"
LIVE_SOURCE_AUTO = "auto"

# ── Polling intervals (seconds) ──────────────────────────────────────────────
LIVE_POLLING_ACTIVE_SEC = 10   # session in progress
LIVE_POLLING_IDLE_SEC = 300    # no active session

# ── HTTP request timeout (seconds) ──────────────────────────────────────────
REQUEST_TIMEOUT = 10

# ── Pulselive REST API ───────────────────────────────────────────────────────
PULSELIVE_BASE_URL = "https://api.motogp.pulselive.com/motogp/v1"

PULSELIVE_LIVE_TIMING_URL = f"{PULSELIVE_BASE_URL}/timing-gateway/livetiming-lite"
PULSELIVE_SEASONS_URL = f"{PULSELIVE_BASE_URL}/results/seasons"
PULSELIVE_EVENTS_URL = f"{PULSELIVE_BASE_URL}/results/events"
PULSELIVE_CATEGORIES_URL = f"{PULSELIVE_BASE_URL}/results/categories"
PULSELIVE_SESSIONS_URL = f"{PULSELIVE_BASE_URL}/results/sessions"

# Format with .format(uuid=<session_uuid>)
PULSELIVE_SESSION_CLASSIFICATION_URL = (
    f"{PULSELIVE_BASE_URL}/results/session/{{uuid}}/classification"
)

# Format with .format(season_uuid=..., category_uuid=...)
PULSELIVE_STANDINGS_URL = (
    f"{PULSELIVE_BASE_URL}/results/standings"
    "?seasonUuid={season_uuid}&categoryUuid={category_uuid}"
)

# Constructor/team standings (same endpoint, type=team)
PULSELIVE_TEAM_STANDINGS_URL = (
    f"{PULSELIVE_BASE_URL}/results/standings"
    "?seasonUuid={season_uuid}&categoryUuid={category_uuid}&type=team"
)

# ── Official / experimental source ──────────────────────────────────────────
OFFICIAL_LIVE_TIMING_URL = "https://www.motogp.com/en/json/live_timing"

# ── Open-Meteo free weather API (no API key required) ───────────────────────
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# ── MotoGP category identifier ───────────────────────────────────────────────
MOTOGP_CATEGORY_NAME = "MotoGP"

# ── Session status ID → human-readable string (Pulselive) ───────────────────
SESSION_STATUS_MAP: dict[str, str] = {
    "C": "Cancelled",
    "D": "Delayed",
    "F": "Finished",
    "I": "In Progress",
    "N": "Not Started",
    "R": "Red Flag",
}

# ── Config entry data keys ───────────────────────────────────────────────────
CONF_LIVE_SOURCE = "live_source"
CONF_SENSOR_NAME = "sensor_name"

# Race week window configuration
CONF_RACE_WEEK_START_DAY = "race_week_start_day"
RACE_WEEK_START_MONDAY = "monday"
RACE_WEEK_START_SATURDAY = "saturday"
RACE_WEEK_START_SUNDAY = "sunday"
DEFAULT_RACE_WEEK_START_DAY = RACE_WEEK_START_MONDAY

# Entity localized naming
CONF_ENTITY_NAME_MODE = "entity_name_mode"
CONF_ENTITY_NAME_LANGUAGE = "entity_name_language"
ENTITY_NAME_MODE_LEGACY = "legacy"
ENTITY_NAME_MODE_LOCALIZED = "localized"
DEFAULT_ENTITY_NAME_LANGUAGE = "en"

# ── hass.data storage keys ───────────────────────────────────────────────────
KEY_SEASON_COORDINATOR = "season_coordinator"
KEY_STANDINGS_COORDINATOR = "standings_coordinator"
KEY_CONSTRUCTOR_STANDINGS_COORDINATOR = "constructor_standings_coordinator"
KEY_LAST_RACE_COORDINATOR = "last_race_coordinator"
KEY_WEATHER_COORDINATOR = "weather_coordinator"
KEY_PULSELIVE_LIVE_COORDINATOR = "pulselive_live_coordinator"
KEY_OFFICIAL_LIVE_COORDINATOR = "official_live_coordinator"
KEY_ACTIVE_LIVE_SOURCE = "active_live_source"
KEY_ENABLED_SENSORS = "enabled_sensors"
KEY_NO_SPOILER_MANAGER = "no_spoiler_manager"

# ── TTL hints (used as update_interval seeds) ────────────────────────────────
TTL_SEASON = 24 * 3600
TTL_STANDINGS = 24 * 3600
TTL_LAST_RACE = 24 * 3600

# ── All supported sensor keys ────────────────────────────────────────────────
SUPPORTED_SENSOR_KEYS: frozenset[str] = frozenset(
    {
        # Static / REST sensors
        "next_race",
        "current_season",
        "rider_standings",
        "last_race_results",
        # Live timing sensors
        "session_status",
        "current_session",
        "race_lap_count",
        "rider_list",
        "rider_positions",
        "top_three",
        "leader",
        "fastest_lap",
        "session_time_remaining",
        # Diagnostic sensors
        "live_timing_source",
        "official_live_diagnostic",
        # Binary sensors
        "race_week",
        "live_timing_online",
        # Calendar entity
        "calendar",
        # Constructor standings
        "constructor_standings",
        # New live sensors
        "track_weather",
        "pit_stops",
    }
)
