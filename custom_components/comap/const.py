"""Constants for the ComAp integration."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfLength,
    UnitOfPower,
    UnitOfPressure,
    UnitOfReactiveEnergy,
    UnitOfReactivePower,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)

DOMAIN = "comap"
LOGGER = logging.getLogger(__package__)

CONF_ACCESS_CODE = "access_code"
CONF_SERIAL = "serial_number"
CONF_CONTROLLER_ADDRESS = "controller_address"
CONF_VALUES = "values"
CONF_SETPOINTS = "setpoints"

DEFAULT_VALUE_GROUPS = frozenset(
    {
        "Generator",
        "Mains",
        "Load",
        "Statistics",
        "Controller I/O",
        "IL Info",
        "Engine",
    }
)
DEFAULT_SETPOINT_GROUPS = frozenset(
    {
        "Statistics",
        "Basic Settings",
        "AMF Settings",
    }
)

DEFAULT_SCAN_INTERVAL = 10
DEFAULT_PORT = 23
DEFAULT_CONTROLLER_ADDRESS = 1
DISCOVERY_INTERVAL_SECONDS = 300

# Controller dimension string → HA unit constant.
# Dimension strings come directly from the controller's ConfigurationTable.
DIMENSION_TO_HA_UNIT: dict[str, str] = {
    "V": UnitOfElectricPotential.VOLT,
    "A": UnitOfElectricCurrent.AMPERE,
    "kW": UnitOfPower.KILO_WATT,
    "kvar": UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
    "kVAr": UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
    "kVA": UnitOfApparentPower.KILO_VOLT_AMPERE,
    "Hz": UnitOfFrequency.HERTZ,
    "RPM": REVOLUTIONS_PER_MINUTE,
    "%": PERCENTAGE,
    "kWh": UnitOfEnergy.KILO_WATT_HOUR,
    "kVArh": UnitOfReactiveEnergy.KILO_VOLT_AMPERE_REACTIVE_HOUR,
    "h": UnitOfTime.HOURS,
    "hrs": UnitOfTime.HOURS,
    "s": UnitOfTime.SECONDS,
    "min": UnitOfTime.MINUTES,
    "ms": UnitOfTime.MILLISECONDS,
    "°C": UnitOfTemperature.CELSIUS,
    "°F": UnitOfTemperature.FAHRENHEIT,
    "Bar": UnitOfPressure.BAR,
    "bar": UnitOfPressure.BAR,
    "L": UnitOfVolume.LITERS,
    "m": UnitOfLength.METERS,
    "km": UnitOfLength.KILOMETERS,
}

DIMENSION_TO_DEVICE_CLASS: dict[str, SensorDeviceClass] = {
    "V": SensorDeviceClass.VOLTAGE,
    "A": SensorDeviceClass.CURRENT,
    "kW": SensorDeviceClass.POWER,
    "kvar": SensorDeviceClass.REACTIVE_POWER,
    "kVAr": SensorDeviceClass.REACTIVE_POWER,
    "kVA": SensorDeviceClass.APPARENT_POWER,
    "Hz": SensorDeviceClass.FREQUENCY,
    "kWh": SensorDeviceClass.ENERGY,
    "kVArh": SensorDeviceClass.REACTIVE_ENERGY,
    "h": SensorDeviceClass.DURATION,
    "hrs": SensorDeviceClass.DURATION,
    "s": SensorDeviceClass.DURATION,
    "min": SensorDeviceClass.DURATION,
    "ms": SensorDeviceClass.DURATION,
    "°C": SensorDeviceClass.TEMPERATURE,
    "°F": SensorDeviceClass.TEMPERATURE,
    "Bar": SensorDeviceClass.PRESSURE,
    "bar": SensorDeviceClass.PRESSURE,
    "L": SensorDeviceClass.VOLUME_STORAGE,
}
