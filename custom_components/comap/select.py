"""Select platform for the ComAp integration (STRING_LIST setpoints)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from pycomap.configuration import SetpointDescription
from pycomap.datatypes import DataType

from .const import CONF_SETPOINTS
from .coordinator import ComApCoordinator
from .entity import ComApEntity
from .utils import entity_key

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import ComApConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComApConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ComAp select entities."""
    coordinator: ComApCoordinator = entry.runtime_data
    entities: list[ComApSelectEntity] = []

    enabled_setpoints = entry.options.get(CONF_SETPOINTS)
    for desc in coordinator.ctrl.setpoints:
        if desc.data_type is not DataType.STRING_LIST:
            continue
        if entity_key(desc.name) is None:
            continue
        if enabled_setpoints is not None and desc.number not in enabled_setpoints:
            continue
        entities.append(ComApSelectEntity(coordinator, desc))

    async_add_entities(entities)


class ComApSelectEntity(ComApEntity, SelectEntity):
    """A select entity backed by a STRING_LIST controller setpoint."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ComApCoordinator, desc: SetpointDescription) -> None:
        super().__init__(coordinator, f"sp_{desc.number}")
        self._desc = desc
        self._attr_translation_key = entity_key(desc.name)

        self._attr_options = [label for _, label in coordinator.ctrl.setpoint_options(desc.number)]

        if desc.needs_password:
            self._attr_entity_registry_enabled_default = False

    @callback
    def _handle_coordinator_update(self) -> None:
        value = self.coordinator.data.setpoints.get(self._desc.number)
        if isinstance(value, str):
            self._attr_current_option = value
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_setpoint(self._desc.number, option)
        await self.coordinator.async_request_refresh()
