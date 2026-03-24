"""Base entity classes and localized naming for MotoGP Sensor."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ENTITY_NAME_LANGUAGE,
    CONF_ENTITY_NAME_MODE,
    DEFAULT_ENTITY_NAME_LANGUAGE,
    DOMAIN,
    ENTITY_NAME_MODE_LEGACY,
    ENTITY_NAME_MODE_LOCALIZED,
)

_TRANSLATIONS_DIR = Path(__file__).parent / "translations"

# Module-level caches — survive for the life of the HA process.
_ENTRY_NAME_SETTINGS: dict[str, tuple[str, str]] = {}
_TRANSLATION_NAME_CACHE: dict[str, dict[str, str]] = {}


# ── Language / cache helpers ──────────────────────────────────────────────────


def _normalize_language(language: str | None) -> str:
    """Normalize a stored language code for translation lookup."""
    if not language:
        return DEFAULT_ENTITY_NAME_LANGUAGE
    normalized = str(language).strip().replace("_", "-").lower()
    return normalized or DEFAULT_ENTITY_NAME_LANGUAGE


def _translation_language_candidates(language: str | None) -> tuple[str, ...]:
    """Return translation file candidates in priority order."""
    normalized = _normalize_language(language)
    candidates: list[str] = []
    for candidate in (
        normalized,
        normalized.split("-", 1)[0],
        DEFAULT_ENTITY_NAME_LANGUAGE,
    ):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return tuple(candidates)


def _read_translation_names(language: str) -> dict[str, str]:
    """Read entity display names from a bundled translation file."""
    try:
        path = _TRANSLATIONS_DIR / f"{_normalize_language(language)}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        result: dict[str, str] = {}
        for entities in data.get("entity", {}).values():
            for key, attrs in entities.items():
                if isinstance(attrs, dict) and (n := attrs.get("name")):
                    result[key] = n
        return result
    except Exception:
        return {}


def _prime_translation_names(languages: tuple[str, ...]) -> None:
    """Load translation files into the cache (blocking — run in executor)."""
    for language in languages:
        normalized = _normalize_language(language)
        if normalized in _TRANSLATION_NAME_CACHE:
            continue
        _TRANSLATION_NAME_CACHE[normalized] = _read_translation_names(normalized)


async def async_prepare_translation_names(
    hass: HomeAssistant, entry_id: str | None = None
) -> None:
    """Preload translation files that may be needed during entity setup."""
    mode, language = _entry_name_settings(entry_id)
    languages = list(_translation_language_candidates(DEFAULT_ENTITY_NAME_LANGUAGE))
    if mode == ENTITY_NAME_MODE_LOCALIZED:
        for candidate in _translation_language_candidates(language):
            if candidate not in languages:
                languages.append(candidate)
    await hass.async_add_executor_job(_prime_translation_names, tuple(languages))


# ── Entry name settings ───────────────────────────────────────────────────────


def register_entry_name_settings(entry_id: str, data: dict) -> None:
    """Register naming mode and language for a config entry."""
    mode = data.get(CONF_ENTITY_NAME_MODE, ENTITY_NAME_MODE_LEGACY)
    if mode not in (ENTITY_NAME_MODE_LEGACY, ENTITY_NAME_MODE_LOCALIZED):
        mode = ENTITY_NAME_MODE_LEGACY
    language = _normalize_language(data.get(CONF_ENTITY_NAME_LANGUAGE))
    _ENTRY_NAME_SETTINGS[entry_id] = (mode, language)
    # If called outside the event loop (e.g. in tests), prime synchronously.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        languages = list(_translation_language_candidates(DEFAULT_ENTITY_NAME_LANGUAGE))
        if mode == ENTITY_NAME_MODE_LOCALIZED:
            for candidate in _translation_language_candidates(language):
                if candidate not in languages:
                    languages.append(candidate)
        _prime_translation_names(tuple(languages))


def unregister_entry_name_settings(entry_id: str) -> None:
    """Remove naming settings for a config entry."""
    _ENTRY_NAME_SETTINGS.pop(entry_id, None)


def clear_entry_name_settings() -> None:
    """Clear all cached settings — used in tests."""
    _ENTRY_NAME_SETTINGS.clear()
    _TRANSLATION_NAME_CACHE.clear()


def _entry_name_settings(entry_id: str | None) -> tuple[str, str]:
    """Return the naming mode and language for an entry."""
    if not entry_id:
        return ENTITY_NAME_MODE_LEGACY, DEFAULT_ENTITY_NAME_LANGUAGE
    return _ENTRY_NAME_SETTINGS.get(
        entry_id,
        (ENTITY_NAME_MODE_LEGACY, DEFAULT_ENTITY_NAME_LANGUAGE),
    )


# ── Name resolution ───────────────────────────────────────────────────────────


def _translated_entity_name(translation_key: str, language: str) -> str | None:
    """Return the first matching translated entity name for a language."""
    for candidate in _translation_language_candidates(language):
        if name := _TRANSLATION_NAME_CACHE.get(candidate, {}).get(translation_key):
            return name
    return None


def _entity_name_from_key(
    translation_key: str | None, *, entry_id: str | None = None
) -> str | None:
    """Return a display name without any device prefix."""
    if not translation_key:
        return None
    mode, language = _entry_name_settings(entry_id)
    if mode == ENTITY_NAME_MODE_LOCALIZED:
        if name := _translated_entity_name(translation_key, language):
            return name
    elif name := _translated_entity_name(translation_key, DEFAULT_ENTITY_NAME_LANGUAGE):
        return name
    # Fallback: capitalize translation key words
    parts = translation_key.replace("_", " ").split()
    if not parts:
        return None
    return " ".join([parts[0].capitalize()] + parts[1:])


# ── Object ID helpers ─────────────────────────────────────────────────────────


def default_object_id(key: str | None) -> str | None:
    """Build a stable suggested object_id for an entity key.

    Produces ``motogp_{key}`` to avoid collisions with other integrations.
    """
    if not key:
        return None
    normalized = str(key).strip().replace("-", "_").lower()
    return f"motogp_{normalized}" if normalized else None


def set_suggested_object_id(entity: Entity, object_id: str | None) -> None:
    """Attach a stable suggested object ID to an entity."""
    if object_id:
        entity._attr_suggested_object_id = object_id  # noqa: SLF001


# ── Base entity classes ───────────────────────────────────────────────────────


def _build_device_info(entry_id: str, device_name: str) -> dict:
    """Return a shared DeviceInfo dict for all MotoGP Sensor entities."""
    from homeassistant.helpers.entity import DeviceInfo  # local import to avoid cycle

    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name=device_name,
        manufacturer="MotoGP",
        model="Live Timing",
        entry_type=DeviceEntryType.SERVICE,
    )


class MotoGPBaseEntity(CoordinatorEntity):
    """Base entity for coordinators-backed MotoGP sensors.

    Sub-classes should set ``_attr_translation_key`` and call super().__init__
    with coordinator, entry_id, and device_name taken from entry.data.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: object,
        unique_id: str,
        entry_id: str,
        device_name: str,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = unique_id
        self._attr_device_info = _build_device_info(entry_id, device_name)

    @property
    def name(self) -> str | None:
        # Honour translation_key-based naming set by subclasses.
        if self._attr_translation_key:
            return _entity_name_from_key(
                self._attr_translation_key, entry_id=self._entry_id
            )
        return super().name


class MotoGPAuxEntity(Entity):
    """Base entity for MotoGP entities NOT bound to a DataUpdateCoordinator.

    Used for: switches, selects, diagnostic entities that read directly from
    hass.data rather than via a coordinator.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        unique_id: str,
        entry_id: str,
        device_name: str,
    ) -> None:
        self._entry_id = entry_id
        self._attr_unique_id = unique_id
        self._attr_device_info = _build_device_info(entry_id, device_name)

    @property
    def name(self) -> str | None:
        if self._attr_translation_key:
            return _entity_name_from_key(
                self._attr_translation_key, entry_id=self._entry_id
            )
        return super().name
