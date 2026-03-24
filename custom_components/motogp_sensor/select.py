"""Select platform for MotoGP Sensor — runtime live source selection."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_LIVE_SOURCE,
    DOMAIN,
    KEY_ACTIVE_LIVE_SOURCE,
    LIVE_SOURCE_AUTO,
    LIVE_SOURCE_OFFICIAL,
    LIVE_SOURCE_PULSELIVE,
)
from .entity import MotoGPAuxEntity, default_object_id, set_suggested_object_id

_LOGGER = logging.getLogger(__name__)

# Ordered list of valid options (also used as SelectEntity.options).
_LIVE_SOURCE_OPTIONS = [LIVE_SOURCE_PULSELIVE, LIVE_SOURCE_OFFICIAL, LIVE_SOURCE_AUTO]


class MotoGPLiveSourceSelect(MotoGPAuxEntity, SelectEntity):
    """Select entity to switch between Pulselive / Official / Auto live sources.

    The selection is applied immediately to hass.data and also persisted back to
    the config entry so it survives restarts without going through reconfigure.
    """

    _attr_translation_key = "live_source"
    _attr_icon = "mdi:antenna"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = _LIVE_SOURCE_OPTIONS

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Sync select state with hass.data on startup.
        self.async_write_ha_state()

    @property
    def current_option(self) -> str:
        reg = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        return reg.get(KEY_ACTIVE_LIVE_SOURCE, LIVE_SOURCE_PULSELIVE)

    async def async_select_option(self, option: str) -> None:
        if option not in _LIVE_SOURCE_OPTIONS:
            _LOGGER.warning("Invalid live source option: %s", option)
            return
        # Update in-memory state immediately so live sensors pick it up.
        reg = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if isinstance(reg, dict):
            reg[KEY_ACTIVE_LIVE_SOURCE] = option
        # Persist to config entry so it survives restarts.
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is not None:
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_LIVE_SOURCE: option}
            )
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MotoGP select entities."""
    device_name = entry.data.get("sensor_name", "MotoGP")
    entity = MotoGPLiveSourceSelect(
        f"{entry.entry_id}_live_source",
        entry.entry_id,
        device_name,
    )
    set_suggested_object_id(entity, default_object_id("live_source"))
    async_add_entities([entity])
