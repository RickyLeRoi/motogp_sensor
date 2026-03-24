"""Binary sensor platform for MotoGP Sensor."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_RACE_WEEK_START_DAY,
    DEFAULT_RACE_WEEK_START_DAY,
    DOMAIN,
    KEY_OFFICIAL_LIVE_COORDINATOR,
    KEY_PULSELIVE_LIVE_COORDINATOR,
    KEY_SEASON_COORDINATOR,
)
from .entity import (
    MotoGPAuxEntity,
    MotoGPBaseEntity,
    default_object_id,
    set_suggested_object_id,
)
from .helpers import find_current_event

_LOGGER = logging.getLogger(__name__)

# Keep race-week sensor ON for this duration after the official event end date
_RACE_WEEK_GRACE = timedelta(hours=3)


class MotoGPRaceWeekBinarySensor(MotoGPBaseEntity, BinarySensorEntity):
    """Binary sensor that is ON during a MotoGP race weekend."""

    _attr_translation_key = "race_week"
    _attr_icon = "mdi:racing-helmet"

    def __init__(
        self,
        coordinator: Any,
        unique_id: str,
        entry_id: str,
        device_name: str,
        start_day: str,
    ) -> None:
        MotoGPBaseEntity.__init__(self, coordinator, unique_id, entry_id, device_name)
        self._start_day = start_day

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return False
        events = data.get("events") or []
        # Standard window check
        if find_current_event(events, self._start_day) is not None:
            return True
        # Grace period: stay ON for _RACE_WEEK_GRACE after the official event end
        now_utc = datetime.now(tz=timezone.utc)
        for ev in events:
            if not isinstance(ev, dict):
                continue
            end_str = str(ev.get("date_end") or ev.get("dateEnd") or "").strip()[:10]
            start_str = str(ev.get("date_start") or ev.get("dateStart") or "").strip()[:10]
            if not end_str or not start_str:
                continue
            try:
                from datetime import date as _date
                start_date = _date.fromisoformat(start_str)
                end_date = _date.fromisoformat(end_str)
                # Event must have started before today
                if start_date > now_utc.date():
                    continue
                # Treat event as ending at midnight UTC of the day after date_end
                end_midnight_utc = datetime(
                    end_date.year, end_date.month, end_date.day,
                    tzinfo=timezone.utc,
                ) + timedelta(days=1)
                if now_utc < end_midnight_utc + _RACE_WEEK_GRACE:
                    return True
            except (ValueError, OverflowError):
                continue
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        events = data.get("events") or []
        event = find_current_event(events, self._start_day)
        if not event:
            return None
        from .helpers import circuit_name, event_country

        return {
            "event_name": event.get("name"),
            "short_name": event.get("short_name") or event.get("shortName"),
            "circuit": circuit_name(event),
            "country": event_country(event),
            "date_start": event.get("date_start") or event.get("dateStart"),
            "date_end": event.get("date_end") or event.get("dateEnd"),
            "race_week_start_day": self._start_day,
        }


class MotoGPLiveTimingOnlineBinarySensor(MotoGPAuxEntity, BinarySensorEntity):
    """Binary sensor that is ON when the live timing source is reachable."""

    _attr_translation_key = "live_timing_online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool:
        reg = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        pl = reg.get(KEY_PULSELIVE_LIVE_COORDINATOR)
        off = reg.get(KEY_OFFICIAL_LIVE_COORDINATOR)
        pl_ok = pl is not None and pl.last_update_success and pl.data is not None
        off_ok = off is not None and off.last_update_success and off.data is not None
        return pl_ok or off_ok

    @property
    def available(self) -> bool:
        # Always available so the diagnostic is always visible.
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        reg = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        attrs: dict[str, Any] = {}
        pl = reg.get(KEY_PULSELIVE_LIVE_COORDINATOR)
        if pl is not None:
            attrs["pulselive_success"] = pl.last_update_success
            attrs["pulselive_has_data"] = pl.data is not None
        off = reg.get(KEY_OFFICIAL_LIVE_COORDINATOR)
        if off is not None:
            attrs["official_success"] = off.last_update_success
            attrs["official_has_data"] = off.data is not None
        return attrs


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MotoGP binary sensor entities."""
    reg: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
    disabled: set[str] = set(entry.data.get("disabled_sensors") or [])
    device_name = entry.data.get("sensor_name", "MotoGP")
    start_day = entry.data.get(CONF_RACE_WEEK_START_DAY, DEFAULT_RACE_WEEK_START_DAY)

    entities: list[BinarySensorEntity] = []

    if "race_week" not in disabled:
        season_coord = reg.get(KEY_SEASON_COORDINATOR)
        if season_coord is not None:
            sensor = MotoGPRaceWeekBinarySensor(
                season_coord,
                f"{entry.entry_id}_race_week_binary",
                entry.entry_id,
                device_name,
                start_day,
            )
            set_suggested_object_id(sensor, default_object_id("race_week"))
            entities.append(sensor)

    if "live_timing_online" not in disabled:
        sensor = MotoGPLiveTimingOnlineBinarySensor(
            f"{entry.entry_id}_live_timing_online",
            entry.entry_id,
            device_name,
        )
        set_suggested_object_id(sensor, default_object_id("live_timing_online"))
        entities.append(sensor)

    if entities:
        async_add_entities(entities)
