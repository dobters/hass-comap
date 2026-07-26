"""Base entity for the ComAp integration."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERIAL, DOMAIN
from .coordinator import ComApCoordinator


class ComApEntity(CoordinatorEntity[ComApCoordinator]):
    """Base class for all ComAp entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ComApCoordinator, unique_key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{unique_key}"
        serial = coordinator.config_entry.data.get(CONF_SERIAL)
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.unique_id)},
            name=coordinator.config_entry.title,
            manufacturer="ComAp",
            model=coordinator.model,
            model_id=coordinator.model_id,
            serial_number=str(serial) if serial is not None else None,
            connections={(dr.CONNECTION_NETWORK_MAC, coordinator.config_entry.unique_id)},
            sw_version=coordinator.sw_version,
        )
