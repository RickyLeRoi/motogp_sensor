"""MotoGP Season Calendar entity for Home Assistant."""
from __future__ import annotations

import datetime
import logging
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, KEY_SEASON_COORDINATOR
from .entity import MotoGPBaseEntity, default_object_id, set_suggested_object_id
from .helpers import circuit_name, event_country

_LOGGER = logging.getLogger(__name__)


def _parse_event_date(date_str: str | None) -> datetime.date | None:
    """Parse an ISO date string to a date object, ignoring time portion."""
    if not date_str:
        return None
    try:
        return datetime.date.fromisoformat(str(date_str)[:10])
    except (ValueError, TypeError):
        return None


class MotoGPSeasonCalendar(MotoGPBaseEntity, CalendarEntity):
    """CalendarEntity exposing all Grand Prix events of the current MotoGP season."""

    _attr_translation_key = "season_calendar"
    _attr_icon = "mdi:calendar-star"

    # ── CalendarEntity required property ─────────────────────────────────────

    @property
    def event(self) -> CalendarEvent | None:
        """Return the currently ongoing GP, or None."""
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        events: list[dict] = data.get("events") or []
        today = datetime.date.today()
        for ev in events:
            if not isinstance(ev, dict):
                continue
            start = _parse_event_date(ev.get("date_start") or ev.get("dateStart"))
            end = _parse_event_date(ev.get("date_end") or ev.get("dateEnd"))
            if start is None or end is None:
                continue
            if start <= today <= end:
                return self._event_to_calendar_event(ev)
        return None

    # ── CalendarEntity async method ───────────────────────────────────────────

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime.datetime,
        end_date: datetime.datetime,
    ) -> list[CalendarEvent]:
        """Return all GP events that overlap the requested date window."""
        data = self.coordinator.data
        if not isinstance(data, dict):
            return []
        events: list[dict] = data.get("events") or []
        result: list[CalendarEvent] = []
        window_start = start_date.date() if isinstance(start_date, datetime.datetime) else start_date
        window_end = end_date.date() if isinstance(end_date, datetime.datetime) else end_date
        for ev in events:
            if not isinstance(ev, dict):
                continue
            ev_start = _parse_event_date(ev.get("date_start") or ev.get("dateStart"))
            ev_end = _parse_event_date(ev.get("date_end") or ev.get("dateEnd"))
            if ev_start is None or ev_end is None:
                continue
            # CalendarEvent end is exclusive, so add 1 day.
            exclusive_end = ev_end + datetime.timedelta(days=1)
            # Overlap check: event starts before window end AND event ends after window start.
            if ev_start < window_end and exclusive_end > window_start:
                result.append(self._event_to_calendar_event(ev))
        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _event_to_calendar_event(self, ev: dict) -> CalendarEvent:
        start = _parse_event_date(ev.get("date_start") or ev.get("dateStart"))
        end = _parse_event_date(ev.get("date_end") or ev.get("dateEnd"))
        # CalendarEvent end is exclusive.
        exclusive_end = (end + datetime.timedelta(days=1)) if end else start

        name = (
            ev.get("name")
            or ev.get("officialName")
            or ev.get("short_name")
            or ev.get("shortName")
            or "MotoGP Event"
        )
        circuit = circuit_name(ev)
        country = event_country(ev)
        location_parts = [p for p in (circuit, country) if p]
        location = ", ".join(location_parts) if location_parts else None

        data = self.coordinator.data or {}
        season_year = data.get("season_year")
        status = str(ev.get("status") or "").strip()
        description_parts = []
        if season_year:
            description_parts.append(f"Season {season_year}")
        if status:
            description_parts.append(f"Status: {status}")
        description = " · ".join(description_parts) or None

        return CalendarEvent(
            start=start,
            end=exclusive_end,
            summary=name,
            location=location,
            description=description,
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the MotoGP Season Calendar entity."""
    disabled: set[str] = set(entry.data.get("disabled_sensors") or [])
    if "calendar" in disabled:
        return

    reg: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
    season_coord = reg.get(KEY_SEASON_COORDINATOR)
    if season_coord is None:
        return

    device_name = entry.data.get("sensor_name", "MotoGP")
    entity = MotoGPSeasonCalendar(
        season_coord,
        f"{entry.entry_id}_season_calendar",
        entry.entry_id,
        device_name,
    )
    set_suggested_object_id(entity, default_object_id("season_calendar"))
    async_add_entities([entity])
