"""Number platform for the ComAp integration (writable numeric setpoints)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from pycomap.configuration import SetpointDescription
from pycomap.datatypes import DataType

from .const import CONF_SETPOINTS, DIMENSION_TO_DEVICE_CLASS, DIMENSION_TO_HA_UNIT
from .coordinator import ComApCoordinator
from .entity import ComApEntity
from .utils import entity_key

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import ComApConfigEntry

_NUMERIC_TYPES = frozenset(
    {
        DataType.INTEGER8,
        DataType.INTEGER16,
        DataType.INTEGER32,
        DataType.UNSIGNED8,
        DataType.UNSIGNED16,
        DataType.UNSIGNED32,
        DataType.FLOAT,
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComApConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ComAp number entities."""
    coordinator: ComApCoordinator = entry.runtime_data
    entities: list[ComApNumberEntity] = []

    enabled_setpoints = entry.options.get(CONF_SETPOINTS)
    for desc in coordinator.ctrl.setpoints:
        if desc.data_type not in _NUMERIC_TYPES:
            continue
        if entity_key(desc.name) is None:
            continue
        if enabled_setpoints is not None and desc.number not in enabled_setpoints:
            continue
        entities.append(ComApNumberEntity(coordinator, desc))

    async_add_entities(entities)


class ComApNumberEntity(ComApEntity, NumberEntity):
    """A number entity backed by a controller setpoint."""

    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ComApCoordinator, desc: SetpointDescription) -> None:
        super().__init__(coordinator, f"sp_{desc.number}")
        self._desc = desc
        self._attr_translation_key = entity_key(desc.name)

        scale = 10**desc.decimal_places if desc.decimal_places else 1
        if not desc.var_low_limit:
            self._attr_native_min_value = desc.low_limit / scale
        if not desc.var_high_limit:
            self._attr_native_max_value = desc.high_limit / scale
        self._attr_native_step = 1 / scale if desc.decimal_places else 1

        self._attr_native_unit_of_measurement = DIMENSION_TO_HA_UNIT.get(desc.dimension)
        device_class = DIMENSION_TO_DEVICE_CLASS.get(desc.dimension)
        if device_class is not None:
            self._attr_device_class = NumberDeviceClass(device_class.value)

        if desc.needs_password:
            self._attr_entity_registry_enabled_default = False

    @callback
    def _update_attrs(self) -> None:
        value = self.coordinator.data.setpoints.get(self._desc.number)
        if isinstance(value, (int, float)):
            self._attr_native_value = float(value)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_setpoint(self._desc.number, value)
        await self.coordinator.async_request_refresh()
