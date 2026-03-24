from __future__ import annotations

from homeassistant import config_entries
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .const import (
    CONF_ENTITY_NAME_LANGUAGE,
    CONF_LIVE_SOURCE,
    CONF_RACE_WEEK_START_DAY,
    CONF_SENSOR_NAME,
    DEFAULT_ENTITY_NAME_LANGUAGE,
    DEFAULT_RACE_WEEK_START_DAY,
    DOMAIN,
    LIVE_SOURCE_AUTO,
    LIVE_SOURCE_OFFICIAL,
    LIVE_SOURCE_PULSELIVE,
    RACE_WEEK_START_MONDAY,
    RACE_WEEK_START_SATURDAY,
    RACE_WEEK_START_SUNDAY,
    SUPPORTED_SENSOR_KEYS,
)

# ── Display names for each sensor key ────────────────────────────────────────
SENSOR_OPTIONS: dict[str, str] = {
    # Static / REST sensors
    "next_race": "Next race",
    "current_season": "Current season",
    "rider_standings": "Rider standings",
    "last_race_results": "Last race results",
    "race_week": "Race week",
    # Live timing sensors
    "session_status": "Session status (live)",
    "current_session": "Current session (live)",
    "race_lap_count": "Race lap count (live)",
    "rider_list": "Rider list (live)",
    "rider_positions": "Rider positions (live)",
    "top_three": "Top three (live)",
    "leader": "Leader (live)",
    "fastest_lap": "Fastest lap (live)",
    "session_time_remaining": "Session time remaining (live)",
    # Diagnostics
    "live_timing_source": "Live timing source",
    "official_live_diagnostic": "Official live timing diagnostic",
    # Binary sensors
    "race_week": "Race week (binary sensor)",
    "live_timing_online": "Live timing online (binary sensor)",
    # Calendar entity
    "calendar": "Season calendar",
    # New sensors
    "constructor_standings": "Constructor standings",
    "track_weather": "Track weather",
    "pit_stops": "Pit stops (riders in pit)",
}

RACE_WEEK_START_OPTIONS: dict[str, str] = {
    RACE_WEEK_START_MONDAY: "Monday (full week)",
    RACE_WEEK_START_SATURDAY: "Saturday (race weekend only)",
    RACE_WEEK_START_SUNDAY: "Sunday (previous week)",
}

LIVE_SOURCE_OPTIONS: dict[str, str] = {
    LIVE_SOURCE_PULSELIVE: "Pulselive (recommended)",
    LIVE_SOURCE_OFFICIAL: "Official motogp.com (experimental)",
    LIVE_SOURCE_AUTO: "Auto (Pulselive primary, Official fallback)",
}


def _all_sensor_keys() -> list[str]:
    return list(SENSOR_OPTIONS.keys())


class MotoGPFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MotoGP Sensor."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        current = user_input or {}

        if user_input is not None:
            all_keys = set(SENSOR_OPTIONS.keys())
            checked: set[str] = set(user_input.pop("enabled_sensors", all_keys))
            user_input["disabled_sensors"] = sorted(all_keys - checked)

            if not errors:
                return self.async_create_entry(
                    title=user_input.get(CONF_SENSOR_NAME, "MotoGP"),
                    data={
                        **user_input,
                        CONF_ENTITY_NAME_LANGUAGE: self.hass.config.language
                        or DEFAULT_ENTITY_NAME_LANGUAGE,
                    },
                )

        all_sensor_keys = _all_sensor_keys()

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_SENSOR_NAME,
                    default=current.get(CONF_SENSOR_NAME, "MotoGP"),
                ): cv.string,
                vol.Required(
                    "enabled_sensors",
                    default=current.get("enabled_sensors", all_sensor_keys),
                ): cv.multi_select(SENSOR_OPTIONS),
                vol.Required(
                    CONF_LIVE_SOURCE,
                    default=current.get(CONF_LIVE_SOURCE, LIVE_SOURCE_PULSELIVE),
                ): vol.In(LIVE_SOURCE_OPTIONS),
                vol.Required(
                    CONF_RACE_WEEK_START_DAY,
                    default=current.get(CONF_RACE_WEEK_START_DAY, DEFAULT_RACE_WEEK_START_DAY),
                ): vol.In(RACE_WEEK_START_OPTIONS),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        current = dict(entry.data)

        if user_input is not None:
            all_keys = set(SENSOR_OPTIONS.keys())
            checked: set[str] = set(user_input.pop("enabled_sensors", all_keys))
            user_input["disabled_sensors"] = sorted(all_keys - checked)

            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=user_input,
                )

        all_sensor_keys_set = set(SENSOR_OPTIONS.keys())

        # Build default enabled list from stored disabled_sensors (new format)
        # or enabled_sensors (legacy format), auto-enabling new keys.
        raw_disabled = current.get("disabled_sensors")
        raw_enabled = current.get("enabled_sensors")

        if raw_disabled is not None:
            disabled_set = set(raw_disabled) & all_sensor_keys_set
            default_enabled = [k for k in SENSOR_OPTIONS if k not in disabled_set]
        elif raw_enabled is not None:
            # Legacy: migrate and auto-enable new keys.
            seen: set[str] = set()
            normalized: list[str] = []
            for key in raw_enabled:
                if key in all_sensor_keys_set and key not in seen:
                    normalized.append(key)
                    seen.add(key)
            for key in SENSOR_OPTIONS:
                if key not in seen:
                    normalized.append(key)
            default_enabled = normalized
        else:
            default_enabled = list(SENSOR_OPTIONS.keys())

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_SENSOR_NAME,
                    default=current.get(CONF_SENSOR_NAME, "MotoGP"),
                ): cv.string,
                vol.Required(
                    "enabled_sensors",
                    default=default_enabled,
                ): cv.multi_select(SENSOR_OPTIONS),
                vol.Required(
                    CONF_LIVE_SOURCE,
                    default=current.get(CONF_LIVE_SOURCE, LIVE_SOURCE_PULSELIVE),
                ): vol.In(LIVE_SOURCE_OPTIONS),
                vol.Required(
                    CONF_RACE_WEEK_START_DAY,
                    default=current.get(CONF_RACE_WEEK_START_DAY, DEFAULT_RACE_WEEK_START_DAY),
                ): vol.In(RACE_WEEK_START_OPTIONS),
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=data_schema,
            errors=errors,
        )

    def _get_reconfigure_entry(self) -> config_entries.ConfigEntry:
        """Return the config entry being reconfigured."""
        entries = self.hass.config_entries.async_entries(DOMAIN)
        return entries[0]
