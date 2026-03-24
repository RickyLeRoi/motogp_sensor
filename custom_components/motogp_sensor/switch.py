"""Switch platform for MotoGP Sensor — No Spoiler Mode toggle."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, KEY_NO_SPOILER_MANAGER
from .entity import MotoGPAuxEntity, default_object_id, set_suggested_object_id
from .no_spoiler import NoSpoilerModeManager

_NO_SPOILER_SWITCH_ENTRY_KEY = "no_spoiler_switch_entry_id"


class MotoGPNoSpoilerSwitch(MotoGPAuxEntity, SwitchEntity, RestoreEntity):
    """Switch entity to activate / deactivate No Spoiler Mode globally."""

    _attr_translation_key = "no_spoiler_mode"
    _attr_icon = "mdi:eye-off"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        manager: NoSpoilerModeManager,
        unique_id: str,
        entry_id: str,
        device_name: str,
    ) -> None:
        MotoGPAuxEntity.__init__(self, unique_id, entry_id, device_name)
        self._manager = manager

    @property
    def is_on(self) -> bool:
        return self._manager.is_active

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._manager.async_set_active(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._manager.async_set_active(False)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Subscribe to manager state changes so the entity updates immediately.
        self.async_on_remove(
            self._manager.add_listener(lambda _active: self.async_write_ha_state())
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MotoGP switch entities."""
    domain_root: dict = hass.data.setdefault(DOMAIN, {})
    manager: NoSpoilerModeManager | None = domain_root.get(KEY_NO_SPOILER_MANAGER)

    entities: list[SwitchEntity] = []

    # No Spoiler switch is global — register only for the first entry that loads.
    if manager is not None and not domain_root.get(_NO_SPOILER_SWITCH_ENTRY_KEY):
        domain_root[_NO_SPOILER_SWITCH_ENTRY_KEY] = entry.entry_id
        device_name = entry.data.get("sensor_name", "MotoGP")
        entity = MotoGPNoSpoilerSwitch(
            manager,
            "motogp_sensor_no_spoiler_mode",
            entry.entry_id,
            device_name,
        )
        set_suggested_object_id(entity, default_object_id("no_spoiler_mode"))
        entities.append(entity)

    if entities:
        async_add_entities(entities)
