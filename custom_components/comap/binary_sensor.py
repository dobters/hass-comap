"""Binary sensor platform for the ComAp integration.

Each BINARY* value is split into individual per-bit binary sensors.
Bit names from the controller firmware are not used (value_bit_names() returns wrong
data on this controller — bit name index points to the wrong CommonNames offset).
Bits are named positionally: "<value name> <bit>" (1-based).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from pycomap.configuration import ValueDescription
from pycomap.datatypes import DataType

from .const import CONF_VALUES
from .coordinator import ComApCoordinator
from .entity import ComApEntity
from .utils import entity_key

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import ComApConfigEntry

_BINARY_WIDTH: dict[DataType, int] = {
    DataType.BINARY8: 8,
    DataType.BINARY16: 16,
    DataType.BINARY32: 32,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComApConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ComAp binary sensor entities."""
    coordinator: ComApCoordinator = entry.runtime_data
    entities: list[ComApBitSensor] = []

    enabled_values = entry.options.get(CONF_VALUES)
    for desc in coordinator.ctrl.values:
        width = _BINARY_WIDTH.get(desc.data_type)
        if width is None:
            continue
        if entity_key(desc.name) is None:
            continue
        if enabled_values is not None and desc.number not in enabled_values:
            continue
        for bit in range(width):
            entities.append(ComApBitSensor(coordinator, desc, bit))

    async_add_entities(entities)


class ComApBitSensor(ComApEntity, BinarySensorEntity):
    """Binary sensor representing a single bit of a BINARY* controller value."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ComApCoordinator,
        desc: ValueDescription,
        bit: int,
    ) -> None:
        super().__init__(coordinator, f"{desc.number}_bit_{bit}")
        self._desc = desc
        self._bit = bit
        self._attr_name = f"{desc.name} {bit + 1}"

    @callback
    def _update_attrs(self) -> None:
        value = self.coordinator.data.values.get(self._desc.number)
        if isinstance(value, int):
            self._attr_is_on = bool(value & (1 << self._bit))
