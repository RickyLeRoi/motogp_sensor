"""MotoGP sensor platform — all sensor entity classes."""
from __future__ import annotations

from contextlib import suppress
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_SENSOR_NAME,
    DOMAIN,
    KEY_ACTIVE_LIVE_SOURCE,
    KEY_CONSTRUCTOR_STANDINGS_COORDINATOR,
    KEY_ENABLED_SENSORS,
    KEY_LAST_RACE_COORDINATOR,
    KEY_OFFICIAL_LIVE_COORDINATOR,
    KEY_PULSELIVE_LIVE_COORDINATOR,
    KEY_SEASON_COORDINATOR,
    KEY_STANDINGS_COORDINATOR,
    KEY_WEATHER_COORDINATOR,
    LIVE_SOURCE_OFFICIAL,
    LIVE_SOURCE_PULSELIVE,
)
from .entity import MotoGPAuxEntity, MotoGPBaseEntity, default_object_id, set_suggested_object_id
from .helpers import circuit_name, compute_fastest_lap, extract_session_schedule, find_next_event




# ── Base classes ──────────────────────────────────────────────────────────────


class MotoGPBaseSensor(MotoGPBaseEntity, SensorEntity):
    """Base for static sensors bound to a single DataUpdateCoordinator."""

    def __init__(
        self, coordinator: Any, entry: ConfigEntry, translation_key: str
    ) -> None:
        MotoGPBaseEntity.__init__(
            self,
            coordinator,
            unique_id=f"{entry.entry_id}_{translation_key}",
            entry_id=entry.entry_id,
            device_name=entry.data.get(CONF_SENSOR_NAME, "MotoGP"),
        )
        self._attr_translation_key = translation_key


class MotoGPLiveSensorBase(MotoGPAuxEntity, SensorEntity):
    """
    Base for live sensors that listen to both live coordinators.

    Entities that inherit this class must NOT set _attr_should_poll = True
    because updates are triggered via coordinator listeners.
    """

    def __init__(self, entry: ConfigEntry, translation_key: str) -> None:
        MotoGPAuxEntity.__init__(
            self,
            unique_id=f"{entry.entry_id}_{translation_key}",
            entry_id=entry.entry_id,
            device_name=entry.data.get(CONF_SENSOR_NAME, "MotoGP"),
        )
        self._attr_translation_key = translation_key

    @property
    def available(self) -> bool:
        return self._get_live_data() is not None

    def _get_live_data(self) -> dict[str, Any] | None:
        """Return data from the currently active live coordinator, with fallback."""
        reg = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        source = reg.get(KEY_ACTIVE_LIVE_SOURCE, LIVE_SOURCE_PULSELIVE)
        if source == LIVE_SOURCE_OFFICIAL:
            coord = reg.get(KEY_OFFICIAL_LIVE_COORDINATOR)
            if coord is not None and isinstance(coord.data, dict):
                # Only use official data when schema is known
                if coord.data.get("schema_known", True):
                    return coord.data
        # Pulselive is the default / fallback
        coord = reg.get(KEY_PULSELIVE_LIVE_COORDINATOR)
        return coord.data if (coord is not None) else None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        reg = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        for key in (KEY_PULSELIVE_LIVE_COORDINATOR, KEY_OFFICIAL_LIVE_COORDINATOR):
            coord = reg.get(key)
            if coord is not None:
                self.async_on_remove(
                    coord.async_add_listener(self._handle_coordinator_update)
                )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


# ── Static sensors ────────────────────────────────────────────────────────────


class MotoGPNextRaceSensor(MotoGPBaseSensor):
    """Next scheduled MotoGP race event."""

    _attr_icon = "mdi:flag-checkered"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        event = find_next_event(data.get("events") or [])
        if not event:
            return None
        return event.get("name") or event.get("officialName") or event.get("short_name")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        events: list[dict] = data.get("events") or []
        event = find_next_event(events)
        if not event:
            return None

        # country is a top-level event field: {"iso": "TH", "name": "Thailand", ...}
        country_obj = event.get("country")
        country: str | None = None
        if isinstance(country_obj, dict):
            country = country_obj.get("name") or country_obj.get("iso")
        elif country_obj:
            country = str(country_obj)

        # round: derive from legacy_id (eventId for MotoGP categoryId==1),
        # fall back to 1-based position in the events list
        round_number: int | None = None
        legacy_id = event.get("legacy_id")
        if isinstance(legacy_id, list):
            for entry in legacy_id:
                if isinstance(entry, dict) and entry.get("categoryId") == 1:
                    with suppress(Exception):
                        round_number = int(entry["eventId"])
                    break
        if round_number is None:
            with suppress(Exception):
                round_number = events.index(event) + 1

        # circuit details
        circuit_obj = event.get("circuit") or {}
        c_name: str | None = None
        c_locality: str | None = None
        c_country: str | None = None
        if isinstance(circuit_obj, dict):
            c_name = circuit_obj.get("name")
            c_locality = circuit_obj.get("place") or circuit_obj.get("locality")
            # circuit country: try nested country dict, then nation ISO
            c_country_raw = circuit_obj.get("country")
            if isinstance(c_country_raw, dict):
                c_country = c_country_raw.get("name") or c_country_raw.get("iso")
            elif c_country_raw:
                c_country = str(c_country_raw)
            elif circuit_obj.get("nation"):
                c_country = str(circuit_obj["nation"])

        # season year
        season_year: int | None = None
        season_obj = event.get("season")
        if isinstance(season_obj, dict):
            with suppress(Exception):
                season_year = int(season_obj["year"])
        if season_year is None:
            season_year = data.get("season_year")

        # session schedule
        next_sessions: list[dict] = data.get("next_event_sessions") or []
        schedule = extract_session_schedule(next_sessions)

        return {
            "round": round_number,
            "race_name": event.get("name") or event.get("officialName") or event.get("short_name"),
            "season": season_year,
            "short_name": event.get("short_name") or event.get("shortName"),
            "circuit": circuit_name(event),
            "circuit_name": c_name,
            "circuit_locality": c_locality,
            "circuit_country": c_country,
            "country": country,
            "date_start": event.get("date_start") or event.get("dateStart"),
            "date_end": event.get("date_end") or event.get("dateEnd"),
            "first_practice_start_local": schedule["first_practice_start_local"],
            "first_practice_start_utc": schedule["first_practice_start_utc"],
            "second_practice_start_local": schedule["second_practice_start_local"],
            "second_practice_start_utc": schedule["second_practice_start_utc"],
            "third_practice_start_local": schedule["third_practice_start_local"],
            "third_practice_start_utc": schedule["third_practice_start_utc"],
            "qualifying_start_local": schedule["qualifying_start_local"],
            "qualifying_start_utc": schedule["qualifying_start_utc"],
            "race_start_local": schedule["race_start_local"],
            "race_start_utc": schedule["race_start_utc"],
        }


class MotoGPCurrentSeasonSensor(MotoGPBaseSensor):
    """Current MotoGP season year."""

    _attr_icon = "mdi:calendar-star"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        year = data.get("season_year")
        with suppress(Exception):
            return int(year) if year is not None else None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        return {
            "season_uuid": data.get("season_uuid"),
            "category_uuid": data.get("category_uuid"),
            "total_events": len(data.get("events") or []),
        }


class MotoGPRiderStandingsSensor(MotoGPBaseSensor):
    """MotoGP rider standings — state is rider count, attributes hold full list."""

    _attr_icon = "mdi:podium"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        standings = data.get("rider_standings") or []
        return len(standings)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        return {
            "season": data.get("season"),
            "round": data.get("round"),
            "rider_standings": data.get("rider_standings") or [],
        }


class MotoGPLastRaceResultsSensor(MotoGPBaseSensor):
    """Results of the last completed MotoGP race."""

    _attr_icon = "mdi:trophy"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        return data.get("event_name")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        return {
            "circuit": data.get("circuit"),
            "date": data.get("date"),
            "results": data.get("results") or [],
        }




# ── Live sensors ──────────────────────────────────────────────────────────────


class MotoGPSessionStatusSensor(MotoGPLiveSensorBase):
    """Current session status (In Progress, Finished, etc.)."""

    _attr_icon = "mdi:traffic-light"

    @property
    def native_value(self) -> str | None:
        data = self._get_live_data()
        if not data:
            return None
        session_info = data.get("session_info") or {}
        return session_info.get("status")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self._get_live_data()
        if not data:
            return None
        session_info = data.get("session_info") or {}
        return {
            "status_id": session_info.get("status_id"),
            "session_type": session_info.get("session_type"),
            "circuit": session_info.get("circuit"),
            "event": session_info.get("event"),
            "source": data.get("source"),
        }


class MotoGPCurrentSessionSensor(MotoGPLiveSensorBase):
    """Current session abbreviation (RAC, Q1, FP1, SPR, etc.)."""

    _attr_icon = "mdi:cog-play"

    @property
    def native_value(self) -> str | None:
        data = self._get_live_data()
        if not data:
            return None
        session_info = data.get("session_info") or {}
        return session_info.get("session_type")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self._get_live_data()
        if not data:
            return None
        session_info = data.get("session_info") or {}
        return {
            "session_name": session_info.get("session_name"),
            "circuit": session_info.get("circuit"),
            "event": session_info.get("event"),
            "date": session_info.get("date"),
            "num_laps": session_info.get("num_laps"),
            "status": session_info.get("status"),
            "source": data.get("source"),
        }


class MotoGPRaceLapCountSensor(MotoGPLiveSensorBase):
    """Current race lap count (highest lap number across all riders)."""

    _attr_icon = "mdi:counter"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        data = self._get_live_data()
        if not data:
            return None
        return data.get("lap_current")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self._get_live_data()
        if not data:
            return None
        return {
            "lap_total": data.get("lap_total"),
            "source": data.get("source"),
        }


class MotoGPRiderListSensor(MotoGPLiveSensorBase):
    """List of all MotoGP riders in the current session with brief info."""

    _attr_icon = "mdi:account-group"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        data = self._get_live_data()
        if not data:
            return None
        riders = data.get("riders") or {}
        return len(riders)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self._get_live_data()
        if not data:
            return None
        riders = data.get("riders") or {}
        rider_list = []
        for rn, info in riders.items():
            if not isinstance(info, dict):
                continue
            ident = info.get("identity") or {}
            rider_list.append(
                {
                    "number": rn,
                    "name": ident.get("name"),
                    "tla": ident.get("tla"),
                    "team": ident.get("team"),
                    "bike": ident.get("bike"),
                    "nation": ident.get("nation"),
                }
            )
        return {"riders": rider_list, "source": data.get("source")}


class MotoGPRiderPositionsSensor(MotoGPLiveSensorBase):
    """Full position board — all riders with timing, identity and lap data."""

    _attr_icon = "mdi:format-list-numbered"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        data = self._get_live_data()
        if not data:
            return None
        return len(data.get("riders") or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self._get_live_data()
        if not data:
            return None
        riders = data.get("riders") or {}
        positions: dict[str, Any] = {}
        for rn, info in riders.items():
            if not isinstance(info, dict):
                continue
            timing = info.get("timing") or {}
            ident = info.get("identity") or {}
            laps = info.get("laps") or {}
            positions[rn] = {
                "position": timing.get("position"),
                "name": ident.get("name"),
                "tla": ident.get("tla"),
                "team": ident.get("team"),
                "bike": ident.get("bike"),
                "nation": ident.get("nation"),
                "gap_to_leader": timing.get("gap_to_leader"),
                "interval": timing.get("interval"),
                "last_lap": timing.get("last_lap"),
                "in_pit": timing.get("in_pit"),
                "lap_current": laps.get("lap_current"),
            }
        return {"positions": positions, "source": data.get("source")}


class MotoGPTopThreeSensor(MotoGPLiveSensorBase):
    """Top-3 classification — state is the leader's name."""

    _attr_icon = "mdi:podium-gold"

    @property
    def native_value(self) -> str | None:
        data = self._get_live_data()
        if not data:
            return None
        riders = data.get("riders") or {}
        leader_rn = data.get("leader_rn")
        if leader_rn and leader_rn in riders:
            ident = riders[leader_rn].get("identity") or {}
            return ident.get("name") or ident.get("tla")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self._get_live_data()
        if not data:
            return None
        riders = data.get("riders") or {}
        result: dict[str, Any] = {}
        for pos in (1, 2, 3):
            for rn, info in riders.items():
                if not isinstance(info, dict):
                    continue
                timing = info.get("timing") or {}
                if str(timing.get("position") or "").strip() == str(pos):
                    ident = info.get("identity") or {}
                    result[f"p{pos}"] = {
                        "name": ident.get("name"),
                        "tla": ident.get("tla"),
                        "number": rn,
                        "team": ident.get("team"),
                        "gap_to_leader": timing.get("gap_to_leader"),
                        "last_lap": timing.get("last_lap"),
                        "in_pit": timing.get("in_pit"),
                    }
                    break
        result["source"] = data.get("source")
        return result if len(result) > 1 else None


class MotoGPLeaderSensor(MotoGPLiveSensorBase):
    """Current session leader — name as state, details as attributes."""

    _attr_icon = "mdi:crown"

    @property
    def native_value(self) -> str | None:
        data = self._get_live_data()
        if not data:
            return None
        riders = data.get("riders") or {}
        leader_rn = data.get("leader_rn")
        if leader_rn and leader_rn in riders:
            ident = riders[leader_rn].get("identity") or {}
            return ident.get("name") or ident.get("tla")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self._get_live_data()
        if not data:
            return None
        riders = data.get("riders") or {}
        leader_rn = data.get("leader_rn")
        if not leader_rn or leader_rn not in riders:
            return {"source": data.get("source")}
        info = riders[leader_rn]
        ident = info.get("identity") or {}
        timing = info.get("timing") or {}
        laps = info.get("laps") or {}
        return {
            "number": leader_rn,
            "tla": ident.get("tla"),
            "team": ident.get("team"),
            "bike": ident.get("bike"),
            "nation": ident.get("nation"),
            "last_lap": timing.get("last_lap"),
            "in_pit": timing.get("in_pit"),
            "lap_current": laps.get("lap_current"),
            "source": data.get("source"),
        }


class MotoGPFastestLapSensor(MotoGPLiveSensorBase):
    """
    Approximate fastest lap in the current session.

    Computed as the minimum `last_lap_time` across all riders.
    Note: livetiming-lite does not provide a session-best lap field, so this
    sensor shows the fastest *last completed* lap, which is an approximation.
    """

    _attr_icon = "mdi:timer-outline"

    @property
    def native_value(self) -> str | None:
        data = self._get_live_data()
        if not data:
            return None
        riders = data.get("riders") or {}
        fastest, _ = compute_fastest_lap(riders)
        return fastest

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self._get_live_data()
        if not data:
            return None
        riders = data.get("riders") or {}
        fastest, rider_rn = compute_fastest_lap(riders)
        rider_name: str | None = None
        if rider_rn and rider_rn in riders:
            ident = riders[rider_rn].get("identity") or {}
            rider_name = ident.get("name") or ident.get("tla")
        return {
            "rider_name": rider_name,
            "rider_number": rider_rn,
            "approximate": True,
            "source": data.get("source"),
        }


class MotoGPSessionTimeRemainingSensor(MotoGPLiveSensorBase):
    """
    Session time remaining.

    Pulselive livetiming-lite may embed this in the head object under
    keys such as `time_remaining`, `remaining_time`, or `remaining`.
    Returns None when the field is absent in the current payload.
    """

    _attr_icon = "mdi:timer-sand"

    @property
    def native_value(self) -> str | None:
        data = self._get_live_data()
        if not data:
            return None
        raw_head = data.get("raw_head") or {}
        for key in ("time_remaining", "remaining_time", "remaining", "timeRemaining"):
            value = raw_head.get(key)
            if value is not None:
                return str(value)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self._get_live_data()
        if not data:
            return None
        session_info = data.get("session_info") or {}
        return {
            "session_type": session_info.get("session_type"),
            "status": session_info.get("status"),
            "source": data.get("source"),
        }


# ── Diagnostic sensors ────────────────────────────────────────────────────────


class MotoGPLiveTimingSourceSensor(MotoGPLiveSensorBase):
    """Which live timing source is currently providing data."""

    _attr_icon = "mdi:antenna"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        # Always available so the user can see which source is configured
        return True

    @property
    def native_value(self) -> str:
        reg = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        return reg.get(KEY_ACTIVE_LIVE_SOURCE, LIVE_SOURCE_PULSELIVE)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        reg = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        attrs: dict[str, Any] = {
            "configured_source": reg.get(KEY_ACTIVE_LIVE_SOURCE, LIVE_SOURCE_PULSELIVE),
        }
        pl_coord = reg.get(KEY_PULSELIVE_LIVE_COORDINATOR)
        if pl_coord is not None:
            attrs["pulselive_last_update"] = (
                pl_coord.last_update_success_time.isoformat()
                if pl_coord.last_update_success_time
                else None
            )
            attrs["pulselive_available"] = pl_coord.last_update_success
        off_coord = reg.get(KEY_OFFICIAL_LIVE_COORDINATOR)
        if off_coord is not None:
            attrs["official_last_update"] = (
                off_coord.last_update_success_time.isoformat()
                if off_coord.last_update_success_time
                else None
            )
            attrs["official_available"] = off_coord.last_update_success
            if isinstance(off_coord.data, dict):
                attrs["official_schema_known"] = off_coord.data.get("schema_known", True)
        return attrs


class MotoGPOfficialLiveDiagnosticSensor(MotoGPLiveSensorBase):
    """Health status of the official motogp.com live timing source."""

    _attr_icon = "mdi:stethoscope"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        # Always available so the diagnostic is always visible
        return True

    @property
    def native_value(self) -> str:
        reg = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        coord = reg.get(KEY_OFFICIAL_LIVE_COORDINATOR)
        if coord is None:
            return "Disabled"
        if coord.data is None:
            return "Unavailable"
        if isinstance(coord.data, dict):
            if coord.data.get("schema_known", True):
                return "OK"
            return "Unknown Schema"
        return "Unavailable"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        reg = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        coord = reg.get(KEY_OFFICIAL_LIVE_COORDINATOR)
        if coord is None:
            return None
        attrs: dict[str, Any] = {
            "last_update_success": coord.last_update_success,
        }
        if isinstance(coord.data, dict):
            schema_known = coord.data.get("schema_known", True)
            attrs["schema_known"] = schema_known
            if not schema_known:
                raw = coord.data.get("raw") or {}
                attrs["raw_keys"] = (
                    list(raw.keys())[:10] if isinstance(raw, dict) else []
                )
        return attrs


class MotoGPConstructorStandingsSensor(MotoGPBaseSensor):
    """MotoGP constructor/team championship standings."""

    _attr_icon = "mdi:trophy-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        if not isinstance(data, list):
            return None
        return len(data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if not isinstance(data, list):
            return None
        return {"standings": data}


class MotoGPWeatherSensor(MotoGPBaseSensor):
    """Track weather fetched from Open-Meteo based on the current/next circuit."""

    _attr_icon = "mdi:weather-partly-cloudy"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        condition = data.get("condition")
        if condition:
            return condition
        wmo = data.get("weather_code")
        return str(wmo) if wmo is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        return {
            "air_temp": data.get("air_temp"),
            "humidity": data.get("humidity"),
            "wind_speed": data.get("wind_speed"),
            "wind_direction": data.get("wind_direction"),
            "weather_code": data.get("weather_code"),
            "current_temperature": data.get("current_temperature"),
            "current_precipitation_probability": data.get("current_precipitation_probability"),
            "current_wind_speed": data.get("current_wind_speed"),
            "current_humidity": data.get("current_humidity"),
            "race_temperature": data.get("race_temperature"),
            "race_precipitation_probability": data.get("race_precipitation_probability"),
            "race_wind_speed": data.get("race_wind_speed"),
            "circuit": data.get("circuit"),
            "short_name": data.get("short_name"),
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
        }


class MotoGPPitStopsSensor(MotoGPLiveSensorBase):
    """Riders currently in pit — state is count, attributes list which riders."""

    _attr_icon = "mdi:garage"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        data = self._get_live_data()
        if not data:
            return None
        riders = data.get("riders") or {}
        return sum(
            1
            for info in riders.values()
            if isinstance(info, dict)
            and (info.get("timing") or {}).get("in_pit") is True
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self._get_live_data()
        if not data:
            return None
        riders = data.get("riders") or {}
        in_pit: list[dict[str, Any]] = []
        for rn, info in riders.items():
            if not isinstance(info, dict):
                continue
            if (info.get("timing") or {}).get("in_pit") is True:
                ident = info.get("identity") or {}
                in_pit.append(
                    {
                        "number": rn,
                        "name": ident.get("name"),
                        "tla": ident.get("tla"),
                        "team": ident.get("team"),
                        "position": (info.get("timing") or {}).get("position"),
                    }
                )
        return {"riders_in_pit": in_pit, "source": data.get("source")}


# ── Platform setup ────────────────────────────────────────────────────────────

# Maps sensor key → (class, coordinator_key | None)
# coordinator_key=None means the sensor manages its own coordinator access (live sensors)
_STATIC_SENSOR_MAP: list[tuple[str, type[MotoGPBaseSensor], str]] = [
    ("next_race", MotoGPNextRaceSensor, KEY_SEASON_COORDINATOR),
    ("current_season", MotoGPCurrentSeasonSensor, KEY_SEASON_COORDINATOR),
    ("rider_standings", MotoGPRiderStandingsSensor, KEY_STANDINGS_COORDINATOR),
    ("last_race_results", MotoGPLastRaceResultsSensor, KEY_LAST_RACE_COORDINATOR),
    ("constructor_standings", MotoGPConstructorStandingsSensor, KEY_CONSTRUCTOR_STANDINGS_COORDINATOR),
    ("track_weather", MotoGPWeatherSensor, KEY_WEATHER_COORDINATOR),
]

_LIVE_SENSOR_MAP: list[tuple[str, type[MotoGPLiveSensorBase]]] = [
    ("session_status", MotoGPSessionStatusSensor),
    ("current_session", MotoGPCurrentSessionSensor),
    ("race_lap_count", MotoGPRaceLapCountSensor),
    ("rider_list", MotoGPRiderListSensor),
    ("rider_positions", MotoGPRiderPositionsSensor),
    ("top_three", MotoGPTopThreeSensor),
    ("leader", MotoGPLeaderSensor),
    ("fastest_lap", MotoGPFastestLapSensor),
    ("session_time_remaining", MotoGPSessionTimeRemainingSensor),
    ("live_timing_source", MotoGPLiveTimingSourceSensor),
    ("official_live_diagnostic", MotoGPOfficialLiveDiagnosticSensor),
    ("pit_stops", MotoGPPitStopsSensor),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MotoGP sensor entities from a config entry."""
    reg: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
    enabled: set[str] = reg.get(KEY_ENABLED_SENSORS, set())

    entities: list[SensorEntity] = []

    # Static sensors
    for sensor_key, sensor_cls, coord_key in _STATIC_SENSOR_MAP:
        if sensor_key not in enabled:
            continue
        coordinator = reg.get(coord_key)
        if coordinator is None:
            continue
        entity = sensor_cls(coordinator, entry, sensor_key)
        set_suggested_object_id(entity, default_object_id(sensor_key))
        entities.append(entity)

    # Live sensors
    # Only add live sensors when at least one live coordinator exists
    has_live = (
        reg.get(KEY_PULSELIVE_LIVE_COORDINATOR) is not None
        or reg.get(KEY_OFFICIAL_LIVE_COORDINATOR) is not None
    )
    if has_live:
        for sensor_key, sensor_cls in _LIVE_SENSOR_MAP:
            if sensor_key not in enabled:
                continue
            entity = sensor_cls(entry, sensor_key)
            set_suggested_object_id(entity, default_object_id(sensor_key))
            entities.append(entity)

    async_add_entities(entities)
