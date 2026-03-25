from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_LIVE_SOURCE,
    CONF_SENSOR_NAME,
    DOMAIN,
    KEY_ACTIVE_LIVE_SOURCE,
    KEY_CONSTRUCTOR_STANDINGS_COORDINATOR,
    KEY_ENABLED_SENSORS,
    KEY_LAST_RACE_COORDINATOR,
    KEY_NO_SPOILER_MANAGER,
    KEY_OFFICIAL_LIVE_COORDINATOR,
    KEY_PULSELIVE_LIVE_COORDINATOR,
    KEY_SEASON_COORDINATOR,
    KEY_STANDINGS_COORDINATOR,
    KEY_WEATHER_COORDINATOR,
    LIVE_POLLING_ACTIVE_SEC,
    LIVE_POLLING_IDLE_SEC,
    LIVE_SOURCE_OFFICIAL,
    LIVE_SOURCE_PULSELIVE,
    MOTOGP_CATEGORY_NAME,
    OFFICIAL_LIVE_TIMING_URL,
    OPEN_METEO_URL,
    PLATFORMS,
    PULSELIVE_CATEGORIES_URL,
    PULSELIVE_EVENTS_URL,
    PULSELIVE_LIVE_TIMING_URL,
    PULSELIVE_SEASONS_URL,
    PULSELIVE_SESSION_CLASSIFICATION_URL,
    PULSELIVE_SESSIONS_URL,
    PULSELIVE_STANDINGS_URL,
    PULSELIVE_TEAM_STANDINGS_URL,
    REQUEST_TIMEOUT,
    SESSION_STATUS_MAP,
    SUPPORTED_SENSOR_KEYS,
)
from .entity import (
    async_prepare_translation_names,
    register_entry_name_settings,
    unregister_entry_name_settings,
)
from .helpers import circuit_coords
from .no_spoiler import NoSpoilerModeManager

_LOGGER = logging.getLogger(__name__)

# Sensor keys that require each coordinator
_SEASON_SENSORS = frozenset(
    {
        "next_race",
        "current_season",
        "race_week",         # sensor (old)
        "race_week_binary",  # binary_sensor
        "rider_standings",
        "last_race_results",
        "calendar",
        "track_weather",            # depends on season events for circuit lookup
    }
)
_STANDINGS_SENSORS = frozenset({"rider_standings", "constructor_standings"})
_LAST_RACE_SENSORS = frozenset({"last_race_results"})
_WEATHER_SENSORS = frozenset({"track_weather"})
_LIVE_SENSORS = frozenset(
    {
        "session_status",
        "current_session",
        "race_lap_count",
        "rider_list",
        "rider_positions",
        "top_three",
        "leader",
        "fastest_lap",
        "session_time_remaining",
        "live_timing_source",
        "official_live_diagnostic",
        "pit_stops",
    }
)

# ── WMO weather code descriptions (Open-Meteo) ───────────────────────────────
_WMO_CONDITIONS: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail", 99: "Heavy thunderstorm with hail",
}


def _wmo_to_condition(code: int | None) -> str | None:
    """Convert a WMO weather code to a human-readable string."""
    if code is None:
        return None
    with suppress(Exception):
        return _WMO_CONDITIONS.get(int(code))
    return None


# ── Module-level helpers ──────────────────────────────────────────────────────


async def _fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    timeout: int = REQUEST_TIMEOUT,
) -> Any:
    """Fetch a URL and return parsed JSON. Raises aiohttp.ClientError on failure."""
    async with asyncio.timeout(timeout):
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)


def _find_current_season(seasons: list[dict]) -> dict | None:
    """Return the current season dict from a list of seasons."""
    if not seasons:
        return None
    current = next((s for s in seasons if s.get("current") is True), None)
    if current:
        return current
    # Fallback: season with highest year
    with suppress(Exception):
        return max(seasons, key=lambda s: int(s.get("year", 0) or 0))
    return seasons[-1]


def _find_last_completed_event(events: list[dict]) -> dict | None:
    """Return the most recent event that has finished."""
    if not events:
        return None
    today = date.today()
    completed: list[dict] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        status = str(ev.get("status") or "").strip()
        date_end_str = str(ev.get("date_end") or ev.get("dateEnd") or "").strip()
        if status.lower() in ("finished", "completed", "done"):
            completed.append(ev)
        elif date_end_str:
            with suppress(Exception):
                end = date.fromisoformat(date_end_str[:10])
                if end < today:
                    completed.append(ev)
    if not completed:
        # If nothing is marked completed and nothing is past, return last event
        return events[-1] if events else None
    # Return the most recently ended one
    def _sort_key(ev: dict) -> str:
        return str(
            ev.get("date_end") or ev.get("dateEnd") or ev.get("date_start") or ""
        )

    return max(completed, key=_sort_key)


def _find_next_event(events: list[dict]) -> dict | None:
    """Return the next upcoming event (first with date_start >= today)."""
    if not events:
        return None
    today = date.today()
    upcoming: list[tuple[date, dict]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        date_start_str = str(
            ev.get("date_start") or ev.get("dateStart") or ""
        ).strip()
        with suppress(Exception):
            start = date.fromisoformat(date_start_str[:10])
            if start >= today:
                upcoming.append((start, ev))
    if upcoming:
        return min(upcoming, key=lambda x: x[0])[1]
    # All events in the past — return last one
    return events[-1] if events else None


def _normalize_pulselive_live(raw: Any) -> dict[str, Any] | None:
    """
    Normalise a Pulselive livetiming-lite response into the F1-compatible model.

    Input shape:
        {
          "head": { session_status_id, circuit_name, session_name,
                    session_shortname, num_laps, event_tv_name, date, ... },
          "rider": {
            "1": { rider_number, rider_shortname, rider_name, rider_surname,
                   rider_nation, pos, gap_first, gap_prev, last_lap_time,
                   num_lap, on_pit, team_name, bike_name, color, ... }
          }
        }

    Returns None if the input is not a valid Pulselive live-timing payload.
    """
    if not isinstance(raw, dict):
        return None
    head = raw.get("head")
    if not isinstance(head, dict):
        return None

    num_laps_raw = head.get("num_laps")
    try:
        num_laps = int(num_laps_raw) if num_laps_raw is not None else None
    except (TypeError, ValueError):
        num_laps = None

    status_id = str(head.get("session_status_id") or head.get("session_status") or "")
    session_info: dict[str, Any] = {
        "circuit": head.get("circuit_name"),
        "event": head.get("event_tv_name"),
        "session_name": head.get("session_name"),
        "session_type": head.get("session_shortname"),
        "status": SESSION_STATUS_MAP.get(status_id, status_id) if status_id else None,
        "status_id": status_id or None,
        "date": head.get("date") or head.get("date_formated"),
        "num_laps": num_laps,
    }

    riders_raw = raw.get("rider") or {}
    if not isinstance(riders_raw, dict):
        riders_raw = {}

    riders: dict[str, dict[str, Any]] = {}
    for _idx, rider in riders_raw.items():
        if not isinstance(rider, dict):
            continue
        rn = str(rider.get("rider_number") or _idx).strip()
        if not rn:
            continue

        rider_name = str(rider.get("rider_name") or "").strip()
        rider_surname = str(rider.get("rider_surname") or "").strip()
        full_name = " ".join(p for p in (rider_name, rider_surname) if p) or None

        lap_current_raw = rider.get("num_lap")
        try:
            lap_current = int(lap_current_raw) if lap_current_raw is not None else None
        except (TypeError, ValueError):
            lap_current = None

        riders[rn] = {
            "identity": {
                "tla": rider.get("rider_shortname"),
                "name": full_name,
                "team": rider.get("team_name"),
                "racing_number": rn,
                "nation": rider.get("rider_nation"),
                "bike": rider.get("bike_name"),
                "color": rider.get("color"),
            },
            "timing": {
                "position": str(rider.get("pos") or "").strip() or None,
                "gap_to_leader": str(rider.get("gap_first") or "").strip() or None,
                "interval": str(rider.get("gap_prev") or "").strip() or None,
                "last_lap": str(rider.get("last_lap_time") or "").strip() or None,
                "in_pit": bool(rider.get("on_pit", False)),
                "status_code": rider.get("status_name"),
            },
            "laps": {
                "lap_current": lap_current,
                "lap_total": num_laps,
            },
            "sectors": None,  # Not available in livetiming-lite
        }

    # Compute leader_rn: rider with timing.position == "1"
    leader_rn: str | None = None
    for rn, info in riders.items():
        if (info.get("timing") or {}).get("position") == "1":
            leader_rn = rn
            break
    if leader_rn is None and riders:
        # Fallback: smallest numeric position
        best: tuple[int, str] | None = None
        for rn, info in riders.items():
            pos_str = str((info.get("timing") or {}).get("position") or "").strip()
            with suppress(Exception):
                pos_int = int(pos_str)
                if best is None or pos_int < best[0]:
                    best = (pos_int, rn)
        if best is not None:
            leader_rn = best[1]

    # Compute lap_current: max across all riders
    lap_current_vals = [
        info["laps"]["lap_current"]
        for info in riders.values()
        if isinstance(info.get("laps"), dict)
        and isinstance(info["laps"].get("lap_current"), int)
    ]
    lap_current = max(lap_current_vals) if lap_current_vals else None

    return {
        "session_info": session_info,
        "riders": riders,
        "leader_rn": leader_rn,
        "lap_current": lap_current,
        "lap_total": num_laps,
        "source": LIVE_SOURCE_PULSELIVE,
        "schema_known": True,
        "raw_head": dict(head),
    }


# ── Coordinators ──────────────────────────────────────────────────────────────


class MotoGPSeasonCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """
    Fetch the current MotoGP season, category UUID, and event calendar.

    Exposes:
        data = {
            "season_uuid": str,
            "season_year": int,
            "category_uuid": str,
            "events": list[dict],
        }
    """

    def __init__(self, hass: HomeAssistant, session: aiohttp.ClientSession) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="MotoGP Season Coordinator",
            update_interval=timedelta(hours=24),
        )
        self._session = session

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            season_uuid, season_year = await self._fetch_current_season()
            if not season_uuid:
                raise UpdateFailed("Could not determine current MotoGP season UUID")

            category_uuid = await self._fetch_motogp_category_uuid(season_uuid)
            if not category_uuid:
                raise UpdateFailed(
                    f"Could not find MotoGP category for season {season_uuid}"
                )

            events = await self._fetch_events(season_uuid)

            # Fetch sessions for the next scheduled event (for schedule attributes)
            next_event_sessions: list[dict] = []
            with suppress(Exception):
                next_ev = _find_next_event(events)
                if next_ev:
                    ev_uuid = str(next_ev.get("id") or next_ev.get("uuid") or "").strip()
                    if ev_uuid and category_uuid:
                        next_event_sessions = await self._fetch_event_sessions(
                            ev_uuid, category_uuid
                        )

            return {
                "season_uuid": season_uuid,
                "season_year": season_year,
                "category_uuid": category_uuid,
                "events": events,
                "next_event_sessions": next_event_sessions,
            }
        except UpdateFailed:
            raise
        except asyncio.TimeoutError as err:
            raise UpdateFailed("Timeout fetching MotoGP season data") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"HTTP error fetching MotoGP season data: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error fetching season data: {err}") from err

    async def _fetch_current_season(self) -> tuple[str | None, int | None]:
        data = await _fetch_json(self._session, PULSELIVE_SEASONS_URL)
        seasons: list[dict] = []
        if isinstance(data, list):
            seasons = data
        elif isinstance(data, dict):
            seasons = data.get("seasons") or data.get("items") or []

        season = _find_current_season(seasons)
        if not season:
            return None, None

        uuid = str(season.get("id") or season.get("uuid") or "").strip() or None
        year_raw = season.get("year")
        year: int | None = None
        with suppress(Exception):
            year = int(year_raw) if year_raw is not None else None
        return uuid, year

    async def _fetch_motogp_category_uuid(self, season_uuid: str) -> str | None:
        url = f"{PULSELIVE_CATEGORIES_URL}?seasonUuid={season_uuid}"
        data = await _fetch_json(self._session, url)
        categories: list[dict] = []
        if isinstance(data, list):
            categories = data
        elif isinstance(data, dict):
            categories = data.get("categories") or data.get("items") or []

        for cat in categories:
            if not isinstance(cat, dict):
                continue
            name = str(
                cat.get("name") or cat.get("legacy_name") or cat.get("shortname") or ""
            ).strip().lower()
            if name.startswith(MOTOGP_CATEGORY_NAME.lower()):
                uuid = str(cat.get("id") or cat.get("uuid") or "").strip()
                return uuid or None
        return None

    async def _fetch_events(self, season_uuid: str) -> list[dict]:
        url = f"{PULSELIVE_EVENTS_URL}?seasonUuid={season_uuid}"
        data = await _fetch_json(self._session, url)
        events: list[dict] = []
        if isinstance(data, list):
            events = data
        elif isinstance(data, dict):
            events = data.get("events") or data.get("items") or []
        return [e for e in events if isinstance(e, dict)]

    async def _fetch_event_sessions(
        self, event_uuid: str, category_uuid: str
    ) -> list[dict]:
        url = (
            f"{PULSELIVE_SESSIONS_URL}"
            f"?eventUuid={event_uuid}&categoryUuid={category_uuid}"
        )
        data = await _fetch_json(self._session, url)
        sessions: list[dict] = []
        if isinstance(data, list):
            sessions = data
        elif isinstance(data, dict):
            for key in ("sessions", "items"):
                candidate = data.get(key)
                if isinstance(candidate, list):
                    sessions = candidate
                    break
        return [s for s in sessions if isinstance(s, dict)]


class MotoGPStandingsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """
    Fetch the current MotoGP rider standings.

    Exposes:
        data = {
            "season": int,
            "round": int,
            "rider_standings": list of {
                "position": str,
                "positionText": str,
                "points": str,
                "wins": str,
                "Rider": {
                    "riderId": str,
                    "permanentNumber": str,
                    "code": str,
                    "givenName": str,
                    "familyName": str,
                    "nationality": str,
                },
                "Constructor": {
                    "constructorId": str,
                    "name": str,
                    "nationality": str,
                },
            }
        }
    """

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        season_coordinator: MotoGPSeasonCoordinator,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="MotoGP Standings Coordinator",
            update_interval=timedelta(hours=24),
        )
        self._session = session
        self._season_coord = season_coordinator

    async def _async_update_data(self) -> dict[str, Any]:
        season_data = self._season_coord.data
        if not season_data:
            raise UpdateFailed("Season data not yet available")
        season_uuid = season_data.get("season_uuid") or ""
        category_uuid = season_data.get("category_uuid") or ""
        if not season_uuid or not category_uuid:
            raise UpdateFailed("Missing season or category UUID for standings")

        # Derive season year and last completed round number
        season_year: int | None = season_data.get("season_year")
        events: list[dict] = season_data.get("events") or []
        round_number: int | None = None
        last_ev = _find_last_completed_event(events)
        if last_ev:
            legacy_id = last_ev.get("legacy_id")
            if isinstance(legacy_id, list):
                for entry in legacy_id:
                    if isinstance(entry, dict) and entry.get("categoryId") == 1:
                        with suppress(Exception):
                            round_number = int(entry["eventId"])
                        break
            if round_number is None:
                with suppress(Exception):
                    round_number = events.index(last_ev) + 1

        url = PULSELIVE_STANDINGS_URL.format(
            season_uuid=season_uuid, category_uuid=category_uuid
        )
        try:
            data = await _fetch_json(self._session, url)
        except asyncio.TimeoutError as err:
            raise UpdateFailed("Timeout fetching standings") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"HTTP error fetching standings: {err}") from err

        return self._normalize_standings(data, season_year, round_number)

    @staticmethod
    def _normalize_standings(
        raw: Any,
        season_year: int | None = None,
        round_number: int | None = None,
    ) -> dict[str, Any]:
        """Normalise the standings API response to an F1-compatible structure."""
        if raw is None:
            return {"season": season_year, "round": round_number, "rider_standings": []}
        entries: list[dict] = []
        if isinstance(raw, list):
            entries = raw
        elif isinstance(raw, dict):
            for key in ("classification", "standings", "items", "riders", "results"):
                candidate = raw.get(key)
                if isinstance(candidate, list):
                    entries = candidate
                    break
            if not entries:
                for val in raw.values():
                    if isinstance(val, list) and val:
                        entries = val
                        break

        result: list[dict[str, Any]] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            rider = item.get("rider") or item.get("Rider") or {}
            team_obj = item.get("team") or item.get("Team") or {}
            constructor_obj = item.get("constructor") or item.get("Constructor") or {}

            position_raw = item.get("position") or item.get("pos") or ""
            position_str = str(position_raw).strip() if position_raw is not None else ""
            points_raw = item.get("points") or item.get("total_points") or 0
            points_str = str(int(float(points_raw))) if points_raw is not None else "0"
            with suppress(Exception):
                points_str = str(int(float(points_raw)))
            wins_raw = (
                item.get("wins")
                or item.get("number_of_wins")
                or item.get("total_wins")
                or item.get("victories")
                or item.get("first_positions")
                or 0
            )
            wins_str = "0"
            with suppress(Exception):
                wins_str = str(int(wins_raw)) if wins_raw else "0"

            if isinstance(rider, dict):
                given_name = str(
                    rider.get("name")
                    or rider.get("given_name")
                    or rider.get("first_name")
                    or rider.get("given_names")
                    or ""
                ).strip()
                family_name = str(
                    rider.get("surname")
                    or rider.get("family_name")
                    or rider.get("last_name")
                    or rider.get("surnames")
                    or ""
                ).strip()
                # If separate names not found, try splitting full_name
                if not given_name and not family_name:
                    full = str(
                        rider.get("full_name") or rider.get("fullName") or ""
                    ).strip()
                    if full:
                        parts = full.rsplit(" ", 1)
                        given_name = parts[0] if len(parts) > 1 else ""
                        family_name = parts[-1]
                tla = str(
                    rider.get("short_name")
                    or rider.get("legacy_short_name")
                    or rider.get("abbreviation")
                    or rider.get("code")
                    or rider.get("tla")
                    or ""
                ).strip().upper()
                nation = (
                    rider.get("country", {}).get("iso")
                    if isinstance(rider.get("country"), dict)
                    else rider.get("nation") or rider.get("nationality") or ""
                )
                rider_number = str(
                    rider.get("number") or rider.get("bike_number") or ""
                ).strip()
                rider_id = str(
                    rider.get("id") or rider.get("uuid") or
                    f"{given_name}_{family_name}".lower().replace(" ", "_")
                ).strip()
            else:
                given_name = family_name = tla = nation = rider_number = rider_id = ""

            if isinstance(constructor_obj, dict):
                constructor_name = str(constructor_obj.get("name") or "").strip()
                constructor_id = str(
                    constructor_obj.get("id") or constructor_obj.get("uuid") or
                    constructor_name.lower().replace(" ", "_")
                ).strip()
                # Country info may be a nested dict, a flat string, or on the team object
                con_country = constructor_obj.get("country")
                if isinstance(con_country, dict):
                    constructor_nation = str(
                        con_country.get("name") or con_country.get("iso") or ""
                    ).strip()
                elif con_country:
                    constructor_nation = str(con_country).strip()
                else:
                    # Fallback: check team object
                    team_country = team_obj.get("country") if isinstance(team_obj, dict) else None
                    if isinstance(team_country, dict):
                        constructor_nation = str(
                            team_country.get("name") or team_country.get("iso") or ""
                        ).strip()
                    else:
                        constructor_nation = str(
                            constructor_obj.get("nation")
                            or constructor_obj.get("nationality")
                            or (team_obj.get("nation") if isinstance(team_obj, dict) else None)
                            or ""
                        ).strip()
            else:
                constructor_name = (
                    team_obj.get("name") if isinstance(team_obj, dict) else str(team_obj or "")
                ).strip()
                constructor_id = constructor_name.lower().replace(" ", "_")
                constructor_nation = ""

            result.append(
                {
                    "position": position_str,
                    "positionText": position_str,
                    "points": points_str,
                    "wins": wins_str,
                    "Rider": {
                        "riderId": rider_id,
                        "permanentNumber": rider_number,
                        "code": tla,
                        "givenName": given_name,
                        "familyName": family_name,
                        "nationality": nation,
                    },
                    "Constructor": {
                        "constructorId": constructor_id,
                        "name": constructor_name,
                        "nationality": constructor_nation,
                    },
                }
            )
        return {
            "season": season_year,
            "round": round_number,
            "rider_standings": result,
        }


class MotoGPConstructorStandingsCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """
    Fetch the current MotoGP constructor/team championship standings.

    Exposes:
        data = list of {
            "position": int,
            "team_name": str,
            "bike": str,
            "points": float,
        }
    """

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        season_coordinator: "MotoGPSeasonCoordinator",
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="MotoGP Constructor Standings Coordinator",
            update_interval=timedelta(hours=24),
        )
        self._session = session
        self._season_coord = season_coordinator

    async def _async_update_data(self) -> list[dict[str, Any]]:
        season_data = self._season_coord.data
        if not season_data:
            raise UpdateFailed("Season data not yet available")
        season_uuid = season_data.get("season_uuid") or ""
        category_uuid = season_data.get("category_uuid") or ""
        if not season_uuid or not category_uuid:
            raise UpdateFailed("Missing season or category UUID for constructor standings")

        url = PULSELIVE_TEAM_STANDINGS_URL.format(
            season_uuid=season_uuid, category_uuid=category_uuid
        )
        try:
            data = await _fetch_json(self._session, url)
        except asyncio.TimeoutError as err:
            raise UpdateFailed("Timeout fetching constructor standings") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"HTTP error fetching constructor standings: {err}") from err

        return self._normalize_constructor_standings(data)

    @staticmethod
    def _normalize_constructor_standings(raw: Any) -> list[dict[str, Any]]:
        """Normalise the constructor/team standings API response to a flat list."""
        if raw is None:
            return []
        entries: list[dict] = []
        if isinstance(raw, list):
            entries = raw
        elif isinstance(raw, dict):
            for key in ("classification", "standings", "items", "teams", "constructors", "results"):
                candidate = raw.get(key)
                if isinstance(candidate, list):
                    entries = candidate
                    break
            if not entries:
                for val in raw.values():
                    if isinstance(val, list) and val:
                        entries = val
                        break

        result: list[dict[str, Any]] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            team = item.get("team") or item.get("constructor") or {}
            position_raw = item.get("position") or item.get("pos")
            points_raw = item.get("points") or item.get("total_points") or 0
            position: int | None = None
            points: float = 0.0
            with suppress(Exception):
                position = int(position_raw) if position_raw is not None else None
            with suppress(Exception):
                points = float(points_raw) if points_raw is not None else 0.0

            team_name = (
                team.get("name") if isinstance(team, dict) else str(team)
            ) or item.get("team_name") or item.get("name") or None
            bike = (
                team.get("constructor", {}).get("name")
                if isinstance(team, dict) and isinstance(team.get("constructor"), dict)
                else None
            ) or item.get("constructor_name") or item.get("bike") or None

            result.append(
                {
                    "position": position,
                    "team_name": team_name,
                    "bike": bike,
                    "points": points,
                }
            )
        return result


class MotoGPLastRaceCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """
    Fetch the classification of the most recently completed MotoGP race.

    Exposes:
        data = {
            "event_name": str,
            "circuit": str,
            "date": str,
            "session_uuid": str,
            "results": list of {
                "position": int, "rider_name": str, "rider_number": str,
                "team": str, "bike": str, "time": str, "gap": str
            }
        }
    """

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        season_coordinator: MotoGPSeasonCoordinator,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="MotoGP Last Race Coordinator",
            update_interval=timedelta(hours=24),
        )
        self._session = session
        self._season_coord = season_coordinator

    async def _async_update_data(self) -> dict[str, Any]:
        season_data = self._season_coord.data
        if not season_data:
            raise UpdateFailed("Season data not yet available")
        category_uuid = season_data.get("category_uuid") or ""
        events: list[dict] = season_data.get("events") or []

        last_event = _find_last_completed_event(events)
        if not last_event:
            raise UpdateFailed("No completed events found for current season")

        event_uuid = str(last_event.get("id") or last_event.get("uuid") or "").strip()
        event_name = str(last_event.get("name") or last_event.get("shortName") or "")
        circuit_obj = last_event.get("circuit") or {}
        circuit_name = (
            circuit_obj.get("name") if isinstance(circuit_obj, dict) else str(circuit_obj)
        )
        event_date = str(
            last_event.get("date_end") or last_event.get("dateEnd") or ""
        )

        if not event_uuid or not category_uuid:
            raise UpdateFailed("Missing event or category UUID for last race lookup")

        try:
            session_uuid = await self._find_race_session_uuid(event_uuid, category_uuid)
        except Exception as err:
            raise UpdateFailed(f"Error fetching sessions: {err}") from err

        if not session_uuid:
            raise UpdateFailed("Could not find Race session for last event")

        try:
            classification = await self._fetch_classification(session_uuid)
        except Exception as err:
            raise UpdateFailed(f"Error fetching race classification: {err}") from err

        return {
            "event_name": event_name,
            "circuit": circuit_name,
            "date": event_date,
            "session_uuid": session_uuid,
            "results": classification,
        }

    async def _find_race_session_uuid(
        self, event_uuid: str, category_uuid: str
    ) -> str | None:
        url = (
            f"{PULSELIVE_SESSIONS_URL}"
            f"?eventUuid={event_uuid}&categoryUuid={category_uuid}"
        )
        data = await _fetch_json(self._session, url)
        sessions: list[dict] = []
        if isinstance(data, list):
            sessions = data
        elif isinstance(data, dict):
            for key in ("sessions", "items"):
                candidate = data.get(key)
                if isinstance(candidate, list):
                    sessions = candidate
                    break

        for s in sessions:
            if not isinstance(s, dict):
                continue
            stype = str(s.get("type") or s.get("session_type") or "").strip().upper()
            if stype in ("RAC", "RACE"):
                return str(s.get("id") or s.get("uuid") or "").strip() or None
        return None

    async def _fetch_classification(self, session_uuid: str) -> list[dict[str, Any]]:
        url = PULSELIVE_SESSION_CLASSIFICATION_URL.format(uuid=session_uuid)
        data = await _fetch_json(self._session, url)
        entries: list[dict] = []
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            for key in ("classification", "results", "items"):
                candidate = data.get(key)
                if isinstance(candidate, list):
                    entries = candidate
                    break

        result: list[dict[str, Any]] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            rider = item.get("rider") or {}
            team = item.get("team") or {}
            constructor = item.get("constructor") or {}
            if isinstance(rider, dict):
                full_name = (
                    rider.get("full_name")
                    or rider.get("fullName")
                    or (
                        (rider.get("name") or "") + " " + (rider.get("surname") or "")
                    ).strip()
                    or None
                )
                rider_number = str(
                    rider.get("number") or rider.get("bike_number") or ""
                ).strip() or None
            else:
                full_name = None
                rider_number = None

            position_raw = item.get("position") or item.get("pos")
            position: int | None = None
            with suppress(Exception):
                position = int(position_raw) if position_raw is not None else None

            result.append(
                {
                    "position": position,
                    "rider_name": full_name,
                    "rider_number": rider_number,
                    "team": (
                        team.get("name") if isinstance(team, dict) else str(team)
                    ) or None,
                    "bike": (
                        constructor.get("name")
                        if isinstance(constructor, dict)
                        else str(constructor)
                    ) or item.get("bike") or None,
                    "time": str(item.get("time") or item.get("total_time") or ""),
                    "gap": str(item.get("gap") or item.get("gap_from_first") or ""),
                }
            )
        return result


class MotoGPPulseliveLiveCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
    """
    Poll the Pulselive livetiming-lite endpoint.

    Adapts polling interval based on session status:
    - "I" (In Progress) → every 10 s
    - otherwise          → every 5 min

    Exposes: normalised F1-compatible model (see _normalize_pulselive_live)
    or None when no active session.
    """

    def __init__(self, hass: HomeAssistant, session: aiohttp.ClientSession) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="MotoGP Pulselive Live Coordinator",
            update_interval=timedelta(seconds=LIVE_POLLING_IDLE_SEC),
        )
        self._session = session

    async def _async_update_data(self) -> dict[str, Any] | None:
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.get(PULSELIVE_LIVE_TIMING_URL) as resp:
                    if resp.status == 204:
                        # No active session — normal outside race weekends
                        self.update_interval = timedelta(seconds=LIVE_POLLING_IDLE_SEC)
                        return None
                    resp.raise_for_status()
                    raw = await resp.json(content_type=None)
        except asyncio.TimeoutError:
            _LOGGER.debug("Pulselive live timing request timed out")
            self.update_interval = timedelta(seconds=LIVE_POLLING_IDLE_SEC)
            return None
        except aiohttp.ClientError as err:
            _LOGGER.debug("Pulselive live timing HTTP error: %s", err)
            self.update_interval = timedelta(seconds=LIVE_POLLING_IDLE_SEC)
            return None
        except Exception as err:
            _LOGGER.debug("Pulselive live timing unexpected error: %s", err)
            self.update_interval = timedelta(seconds=LIVE_POLLING_IDLE_SEC)
            return None

        data = _normalize_pulselive_live(raw)
        if data is None:
            self.update_interval = timedelta(seconds=LIVE_POLLING_IDLE_SEC)
            return None

        status_id = (data.get("session_info") or {}).get("status_id") or ""
        if status_id == "I":
            self.update_interval = timedelta(seconds=LIVE_POLLING_ACTIVE_SEC)
        else:
            self.update_interval = timedelta(seconds=LIVE_POLLING_IDLE_SEC)

        return data


class MotoGPOfficialLiveCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
    """
    Poll the official motogp.com live timing endpoint (experimental).

    This source may return the same JSON structure as Pulselive (they share
    the same backend) or a different schema. Schema detection is automatic:
    - If "head" and "rider" keys are present  → Pulselive normaliser is applied
    - Otherwise                                → raw dict stored with schema_known=False

    Exposes:
        - Normalised F1 model (schema_known=True)  — when schema matches Pulselive
        - {"schema_known": False, "raw": dict}       — when schema differs
        - None                                        — on HTTP error / no data
    """

    def __init__(self, hass: HomeAssistant, session: aiohttp.ClientSession) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="MotoGP Official Live Coordinator",
            update_interval=timedelta(seconds=LIVE_POLLING_IDLE_SEC),
        )
        self._session = session

    async def _async_update_data(self) -> dict[str, Any] | None:
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.get(OFFICIAL_LIVE_TIMING_URL) as resp:
                    if resp.status in (204, 404):
                        self.update_interval = timedelta(seconds=LIVE_POLLING_IDLE_SEC)
                        return None
                    resp.raise_for_status()
                    raw = await resp.json(content_type=None)
        except asyncio.TimeoutError:
            _LOGGER.debug("Official live timing request timed out")
            self.update_interval = timedelta(seconds=LIVE_POLLING_IDLE_SEC)
            return None
        except aiohttp.ClientError as err:
            _LOGGER.debug("Official live timing HTTP error: %s", err)
            self.update_interval = timedelta(seconds=LIVE_POLLING_IDLE_SEC)
            return None
        except Exception as err:
            _LOGGER.debug("Official live timing unexpected error: %s", err)
            self.update_interval = timedelta(seconds=LIVE_POLLING_IDLE_SEC)
            return None

        if not isinstance(raw, dict):
            self.update_interval = timedelta(seconds=LIVE_POLLING_IDLE_SEC)
            return {"schema_known": False, "raw": raw, "source": LIVE_SOURCE_OFFICIAL}

        # Schema detection: Pulselive compatible?
        if "head" in raw and "rider" in raw:
            data = _normalize_pulselive_live(raw)
            if data is not None:
                data["source"] = LIVE_SOURCE_OFFICIAL
                status_id = (data.get("session_info") or {}).get("status_id") or ""
                if status_id == "I":
                    self.update_interval = timedelta(seconds=LIVE_POLLING_ACTIVE_SEC)
                else:
                    self.update_interval = timedelta(seconds=LIVE_POLLING_IDLE_SEC)
                return data

        # Unknown schema — store raw for diagnostic purposes
        _LOGGER.debug(
            "Official live timing returned unexpected schema (keys: %s)",
            list(raw.keys())[:10],
        )
        self.update_interval = timedelta(seconds=LIVE_POLLING_IDLE_SEC)
        return {"schema_known": False, "raw": raw, "source": LIVE_SOURCE_OFFICIAL}


class MotoGPWeatherCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
    """
    Fetch current weather for the relevant MotoGP circuit via Open-Meteo.

    - During a race weekend: uses the ongoing event's circuit.
    - Between weekends: uses the next scheduled event's circuit.
    - Refreshes every 30 minutes (weather data changes slowly).
    - Returns None when no circuit coordinates are available.

    Exposes:
        data = {
            "condition": str,                          # human-readable WMO condition
            "weather_code": int,                       # raw WMO code
            "air_temp": float,                         # °C
            "humidity": int,                           # %
            "wind_speed": float,                       # km/h
            "wind_direction": int,                     # degrees
            "current_temperature": float,              # current temperature °C
            "current_precipitation_probability": int,  # current precip. probability %
            "current_wind_speed": float,               # current wind speed km/h
            "current_humidity": int,                   # current relative humidity %
            "race_temperature": float | None,          # forecasted max temp on race day
            "race_precipitation_probability": int | None, # forecasted precip. prob. on race day
            "race_wind_speed": float | None,           # forecasted max wind speed on race day
            "circuit": str,
            "short_name": str,
            "latitude": float,
            "longitude": float,
        }
    """

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        season_coordinator: "MotoGPSeasonCoordinator",
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="MotoGP Weather Coordinator",
            update_interval=timedelta(minutes=30),
        )
        self._session = session
        self._season_coord = season_coordinator

    async def _async_update_data(self) -> dict[str, Any] | None:
        season_data = self._season_coord.data
        if not isinstance(season_data, dict):
            return None

        events: list[dict] = season_data.get("events") or []
        event = self._find_relevant_event(events)
        if not event:
            return None

        coords = circuit_coords(event)
        if not coords:
            _LOGGER.debug(
                "No coordinates for circuit short_name=%s — weather unavailable",
                event.get("short_name"),
            )
            return None

        lat, lon = coords

        # Determine how many forecast days are needed to cover the race day
        race_date_str = str(event.get("date_end") or "")[:10]
        forecast_days = 1
        race_date: date | None = None
        with suppress(Exception):
            race_date = date.fromisoformat(race_date_str)
            days_ahead = (race_date - date.today()).days
            if days_ahead >= 0:
                forecast_days = min(16, days_ahead + 1)

        url = (
            f"{OPEN_METEO_URL}"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,weather_code,wind_speed_10m,"
            f"wind_direction_10m,relative_humidity_2m,precipitation_probability"
            f"&daily=temperature_2m_max,precipitation_probability_max,wind_speed_10m_max"
            f"&temperature_unit=celsius&wind_speed_unit=kmh"
            f"&forecast_days={forecast_days}"
        )
        try:
            data = await _fetch_json(self._session, url)
        except asyncio.TimeoutError as err:
            raise UpdateFailed("Timeout fetching weather from Open-Meteo") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"HTTP error fetching weather: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error fetching weather: {err}") from err

        current = data.get("current") or {} if isinstance(data, dict) else {}
        wmo = current.get("weather_code")
        circuit_obj = event.get("circuit") or {}
        circuit_name = (
            circuit_obj.get("name") if isinstance(circuit_obj, dict) else str(circuit_obj)
        ) or event.get("name") or None

        # Parse daily forecast to find race-day values
        race_temperature: float | None = None
        race_precip_probability: int | None = None
        race_wind_speed: float | None = None
        if isinstance(data, dict) and race_date is not None:
            daily = data.get("daily") or {}
            daily_times: list[str] = daily.get("time") or []
            daily_temp_max: list = daily.get("temperature_2m_max") or []
            daily_precip_prob: list = daily.get("precipitation_probability_max") or []
            daily_wind_max: list = daily.get("wind_speed_10m_max") or []
            with suppress(Exception):
                idx = daily_times.index(race_date_str)
                race_temperature = daily_temp_max[idx] if idx < len(daily_temp_max) else None
                race_precip_probability = daily_precip_prob[idx] if idx < len(daily_precip_prob) else None
                race_wind_speed = daily_wind_max[idx] if idx < len(daily_wind_max) else None

        return {
            "condition": _wmo_to_condition(wmo),
            "weather_code": wmo,
            "air_temp": current.get("temperature_2m"),
            "humidity": current.get("relative_humidity_2m"),
            "wind_speed": current.get("wind_speed_10m"),
            "wind_direction": current.get("wind_direction_10m"),
            "current_temperature": current.get("temperature_2m"),
            "current_precipitation_probability": current.get("precipitation_probability"),
            "current_wind_speed": current.get("wind_speed_10m"),
            "current_humidity": current.get("relative_humidity_2m"),
            "race_temperature": race_temperature,
            "race_precipitation_probability": race_precip_probability,
            "race_wind_speed": race_wind_speed,
            "circuit": circuit_name,
            "short_name": event.get("short_name"),
            "latitude": lat,
            "longitude": lon,
        }

    @staticmethod
    def _find_relevant_event(events: list[dict]) -> dict | None:
        """Return current (ongoing) event or the next upcoming one."""
        if not events:
            return None
        today = date.today()
        # 1. Ongoing event: date_start <= today <= date_end
        for ev in events:
            if not isinstance(ev, dict):
                continue
            with suppress(Exception):
                start = date.fromisoformat(str(ev.get("date_start") or "")[:10])
                end = date.fromisoformat(str(ev.get("date_end") or "")[:10])
                if start <= today <= end:
                    return ev
        # 2. Next upcoming event
        return _find_next_event(events)


# ── HA lifecycle ──────────────────────────────────────────────────────────────


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up MotoGP Sensor domain."""
    hass.data.setdefault(DOMAIN, {})
    # Initialize the global No Spoiler Mode manager once per HA instance.
    if KEY_NO_SPOILER_MANAGER not in hass.data[DOMAIN]:
        manager = NoSpoilerModeManager(hass)
        await manager.async_load()
        hass.data[DOMAIN][KEY_NO_SPOILER_MANAGER] = manager
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MotoGP Sensor from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Register naming settings so entity.py can resolve localized names.
    register_entry_name_settings(entry.entry_id, entry.data)

    raw_disabled = entry.data.get("disabled_sensors") or []
    disabled: set[str] = {k for k in raw_disabled if k in SUPPORTED_SENSOR_KEYS}
    enabled: set[str] = SUPPORTED_SENSOR_KEYS - disabled

    session = async_get_clientsession(hass)

    need_season = bool(enabled & _SEASON_SENSORS)
    need_standings = bool(enabled & _STANDINGS_SENSORS)
    need_last_race = bool(enabled & _LAST_RACE_SENSORS)
    need_weather = bool(enabled & _WEATHER_SENSORS)
    need_live = bool(enabled & _LIVE_SENSORS)

    # ── Season coordinator (foundation for standings & last-race) ────────────
    season_coordinator: MotoGPSeasonCoordinator | None = None
    if need_season:
        season_coordinator = MotoGPSeasonCoordinator(hass, session)
        try:
            await season_coordinator.async_refresh()
            if not season_coordinator.last_update_success:
                _LOGGER.warning(
                    "MotoGP season data could not be fetched on startup; will retry"
                )
        except Exception as err:
            _LOGGER.warning("Error during initial MotoGP season fetch: %s", err)

    # ── Standings coordinator ────────────────────────────────────────────────
    standings_coordinator: MotoGPStandingsCoordinator | None = None
    constructor_standings_coordinator: MotoGPConstructorStandingsCoordinator | None = None
    if need_standings and season_coordinator is not None:
        standings_coordinator = MotoGPStandingsCoordinator(
            hass, session, season_coordinator
        )
        with suppress(Exception):
            await standings_coordinator.async_refresh()

        if "constructor_standings" in enabled:
            constructor_standings_coordinator = MotoGPConstructorStandingsCoordinator(
                hass, session, season_coordinator
            )
            with suppress(Exception):
                await constructor_standings_coordinator.async_refresh()

    # ── Last race coordinator ────────────────────────────────────────────────
    last_race_coordinator: MotoGPLastRaceCoordinator | None = None
    if need_last_race and season_coordinator is not None:
        last_race_coordinator = MotoGPLastRaceCoordinator(
            hass, session, season_coordinator
        )
        with suppress(Exception):
            await last_race_coordinator.async_refresh()

    # ── Live coordinators (both always created when any live sensor enabled) ─
    pulselive_coordinator: MotoGPPulseliveLiveCoordinator | None = None
    official_coordinator: MotoGPOfficialLiveCoordinator | None = None

    if need_live:
        pulselive_coordinator = MotoGPPulseliveLiveCoordinator(hass, session)
        with suppress(Exception):
            await pulselive_coordinator.async_refresh()

        official_coordinator = MotoGPOfficialLiveCoordinator(hass, session)
        with suppress(Exception):
            await official_coordinator.async_refresh()

    # ── Weather coordinator (Open-Meteo, every 30 min) ───────────────────────
    weather_coordinator: MotoGPWeatherCoordinator | None = None
    if need_weather and season_coordinator is not None:
        weather_coordinator = MotoGPWeatherCoordinator(hass, session, season_coordinator)
        with suppress(Exception):
            await weather_coordinator.async_refresh()

    active_source = entry.data.get(CONF_LIVE_SOURCE, LIVE_SOURCE_PULSELIVE)

    hass.data[DOMAIN][entry.entry_id] = {
        KEY_SEASON_COORDINATOR: season_coordinator,
        KEY_STANDINGS_COORDINATOR: standings_coordinator,
        KEY_CONSTRUCTOR_STANDINGS_COORDINATOR: constructor_standings_coordinator,
        KEY_LAST_RACE_COORDINATOR: last_race_coordinator,
        KEY_WEATHER_COORDINATOR: weather_coordinator,
        KEY_PULSELIVE_LIVE_COORDINATOR: pulselive_coordinator,
        KEY_OFFICIAL_LIVE_COORDINATOR: official_coordinator,
        KEY_ACTIVE_LIVE_SOURCE: active_source,
        KEY_ENABLED_SENSORS: enabled,
    }

    # Preload translation files before entity setup.
    await async_prepare_translation_names(hass, entry.entry_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Re-read active source when entry is updated (e.g. after reconfigure)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Propagate live_source changes into hass.data without a full reload."""
    reg = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if isinstance(reg, dict):
        reg[KEY_ACTIVE_LIVE_SOURCE] = entry.data.get(
            CONF_LIVE_SOURCE, LIVE_SOURCE_PULSELIVE
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        unregister_entry_name_settings(entry.entry_id)
    return unload_ok
