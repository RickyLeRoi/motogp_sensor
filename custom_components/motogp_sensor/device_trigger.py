"""Device triggers for MotoGP Sensor."""
from __future__ import annotations

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import state as state_trigger
from homeassistant.const import CONF_ENTITY_ID, CONF_FOR, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import DOMAIN

# Maps trigger_type → (unique_id_suffix, entity_domain, to_state or None)
# to_state=None means "any state change" (fires on every update)
_TRIGGER_MAP: dict[str, tuple[str, str, str | None]] = {
    # Race / calendar
    "race_week_started": ("race_week", "binary_sensor", "on"),
    "race_week_ended": ("race_week", "binary_sensor", "off"),
    # Session status changes
    "session_in_progress": ("session_status", "sensor", "In Progress"),
    "session_finished": ("session_status", "sensor", "Finished"),
    "session_red_flag": ("session_status", "sensor", "Red Flag"),
    "session_cancelled": ("session_status", "sensor", "Cancelled"),
    "session_delayed": ("session_status", "sensor", "Delayed"),
    # Live timing connectivity
    "live_timing_online": ("live_timing_online", "binary_sensor", "on"),
    "live_timing_offline": ("live_timing_online", "binary_sensor", "off"),
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_ENTITY_ID): cv.entity_id_or_uuid,
        vol.Required(CONF_TYPE): vol.In(_TRIGGER_MAP),
        vol.Optional(CONF_FOR): cv.positive_time_period_dict,
    }
)


def _find_entity(
    entity_registry: er.EntityRegistry,
    device_id: str,
    suffix: str,
    domain: str,
) -> er.RegistryEntry | None:
    """Return the entity whose unique_id ends with ``_{suffix}`` and belongs to *domain*."""
    for entry in er.async_entries_for_device(entity_registry, device_id):
        if entry.domain == domain and entry.unique_id.endswith(f"_{suffix}"):
            return entry
    return None


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict]:
    """Return a list of trigger descriptors for the given device."""
    registry = er.async_get(hass)
    triggers = []
    for trigger_type, (suffix, entity_domain, _) in _TRIGGER_MAP.items():
        entity = _find_entity(registry, device_id, suffix, entity_domain)
        if entity is None:
            continue
        triggers.append(
            {
                **DEVICE_TRIGGER_BASE_SCHEMA(
                    {"platform": "device", "domain": DOMAIN, "device_id": device_id}
                ),
                CONF_TYPE: trigger_type,
                CONF_ENTITY_ID: entity.entity_id,
            }
        )
    return triggers


async def async_validate_trigger_config(
    hass: HomeAssistant, config: ConfigType
) -> ConfigType:
    """Validate a trigger config."""
    return TRIGGER_SCHEMA(config)


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a trigger to a state change on the mapped entity."""
    trigger_type: str = config[CONF_TYPE]
    _suffix, entity_domain, to_state = _TRIGGER_MAP[trigger_type]

    state_config: dict = {
        "platform": "state",
        CONF_ENTITY_ID: config[CONF_ENTITY_ID],
    }
    if to_state is not None:
        state_config["to"] = to_state
    if CONF_FOR in config:
        state_config[CONF_FOR] = config[CONF_FOR]

    state_config = await state_trigger.async_validate_trigger_config(hass, state_config)
    return await state_trigger.async_attach_trigger(
        hass, state_config, action, trigger_info, platform_type="device"
    )
