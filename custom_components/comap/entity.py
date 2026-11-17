"""Base entity for the ComAp integration."""

from __future__ import annotations

from typing import cast

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERIAL, DOMAIN
from .coordinator import ComApCoordinator


class ComApEntity(CoordinatorEntity[ComApCoordinator]):
    """Base class for all ComAp entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ComApCoordinator, unique_key: str) -> None:
        super().__init__(coordinator)
        unique_id = cast(str, coordinator.config_entry.unique_id)
        self._attr_unique_id = f"{unique_id}_{unique_key}"
        serial = coordinator.config_entry.data.get(CONF_SERIAL)
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, unique_id)},
            name=coordinator.config_entry.title,
            manufacturer="ComAp",
            model=coordinator.model,
            model_id=coordinator.model_id,
            serial_number=str(serial) if serial is not None else None,
            connections={(dr.CONNECTION_NETWORK_MAC, unique_id)},
            sw_version=coordinator.sw_version,
        )

    async def async_added_to_hass(self) -> None:
        """Populate state from the data already polled, so it isn't "unknown" until the next poll.

        ``CoordinatorEntity`` only registers a listener here and does not call
        ``_handle_coordinator_update`` for the data that is already available.
        """
        await super().async_added_to_hass()
        self._update_attrs()

    @callback
    def _update_attrs(self) -> None:
        """Copy current coordinator data into the entity's ``_attr_*`` fields.

        Overridden by subclasses. Called once when the entity is added and on every update.
        """

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_attrs()
        super()._handle_coordinator_update()
