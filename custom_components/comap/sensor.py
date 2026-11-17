"""Sensor platform for the ComAp integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from pycomap.configuration import ValueCategory, ValueDescription
from pycomap.datatypes import DataType

from .const import CONF_VALUES, DIMENSION_TO_DEVICE_CLASS, DIMENSION_TO_HA_UNIT
from .coordinator import ComApCoordinator
from .entity import ComApEntity
from .utils import entity_key

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import ComApConfigEntry

_BINARY_TYPES = frozenset({DataType.BINARY8, DataType.BINARY16, DataType.BINARY32})
_SKIP_TYPES = frozenset({DataType.FTIME, DataType.FDATE, DataType.TIMER})

# Dimensions that indicate a monotonically increasing counter.
_COUNTER_DIMS = frozenset({"kWh", "kVArh", "h", "hrs"})
_COUNTER_NAMES = frozenset({"Num Starts", "Num E-Stops", "Shutdowns"})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComApConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ComAp sensor entities."""
    coordinator: ComApCoordinator = entry.runtime_data
    entities: list[ComApValueSensor | ComApAlarmSensor] = []

    enabled_values = entry.options.get(CONF_VALUES)
    for desc in coordinator.ctrl.values:
        if desc.category is ValueCategory.ONE_TIME:
            continue
        if desc.data_type in _BINARY_TYPES | _SKIP_TYPES:
            continue
        if entity_key(desc.name) is None:
            continue
        if enabled_values is not None and desc.number not in enabled_values:
            continue
        entities.append(ComApValueSensor(coordinator, desc))

    entities.append(ComApAlarmSensor(coordinator))
    async_add_entities(entities)


class ComApValueSensor(ComApEntity, SensorEntity):
    """A sensor entity backed by a controller value."""

    def __init__(self, coordinator: ComApCoordinator, desc: ValueDescription) -> None:
        super().__init__(coordinator, str(desc.number))
        self._desc = desc
        self._attr_translation_key = entity_key(desc.name)

        self._attr_native_unit_of_measurement = DIMENSION_TO_HA_UNIT.get(desc.dimension)
        self._attr_device_class = DIMENSION_TO_DEVICE_CLASS.get(desc.dimension)

        if self._attr_native_unit_of_measurement is None:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

        if desc.dimension in _COUNTER_DIMS or desc.name in _COUNTER_NAMES:
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        elif self._attr_native_unit_of_measurement is not None:
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @callback
    def _update_attrs(self) -> None:
        value = self.coordinator.data.values.get(self._desc.number)
        self._attr_native_value = None if isinstance(value, bytes) else value


class ComApAlarmSensor(ComApEntity, SensorEntity):
    """Sensor showing the count of active alarms with alarm details as attributes."""

    _attr_translation_key = "alarm_count"
    _attr_device_class = None
    _attr_state_class = None
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ComApCoordinator) -> None:
        super().__init__(coordinator, "alarm_count")

    @callback
    def _update_attrs(self) -> None:
        alarms = self.coordinator.data.alarms if self.coordinator.data is not None else []
        self._attr_native_value = len(alarms)
        self._attr_extra_state_attributes = {
            "alarms": [
                {
                    "reason": a.reason,
                    "prefix": a.prefix,
                    "is_active": a.is_active,
                    "is_confirmed": a.is_confirmed,
                }
                for a in alarms
            ]
        }
