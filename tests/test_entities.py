"""Entity state tests: values must be present right after setup, not only after the next poll."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pycomap.configuration import (
    SetpointCategory,
    SetpointDescription,
    ValueCategory,
    ValueDescription,
)
from pycomap.datatypes import DataType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comap.const import CONF_ACCESS_CODE, CONF_CONTROLLER_ADDRESS, DOMAIN

_MAC = "aa:bb:cc:dd:ee:ff"

_VOLTAGE = ValueDescription(
    number=1,
    category=ValueCategory.FIRST,
    data_type=DataType.FLOAT,
    data_length=4,
    decimal_places=1,
    data_index=0,
    state_index=None,
    name="Generator Voltage L1-N",
    dimension="V",
    group="Generator",
    low_limit=0,
    high_limit=1000,
    var_low_limit=False,
    var_high_limit=False,
    bit_name_index=None,
)
_BREAKER = ValueDescription(
    number=2,
    category=ValueCategory.FIRST,
    data_type=DataType.BINARY8,
    data_length=1,
    decimal_places=0,
    data_index=4,
    state_index=None,
    name="Breaker State",
    dimension="",
    group="Generator",
    low_limit=0,
    high_limit=255,
    var_low_limit=False,
    var_high_limit=False,
    bit_name_index=None,
)
_RPM = SetpointDescription(
    number=10,
    category=SetpointCategory.P,
    data_type=DataType.INTEGER16,
    data_length=2,
    decimal_places=0,
    data_index=0,
    name="Nominal RPM",
    dimension="",
    group="Engine",
    access_level=0,
    low_limit=0,
    high_limit=3000,
    var_low_limit=False,
    var_high_limit=False,
)
_MODE = SetpointDescription(
    number=11,
    category=SetpointCategory.P,
    data_type=DataType.STRING_LIST,
    data_length=1,
    decimal_places=0,
    data_index=2,
    name="Summer Time Mode",
    dimension="",
    group="Engine",
    access_level=0,
    low_limit=0,
    high_limit=4,
    var_low_limit=False,
    var_high_limit=False,
)


def _alarm(reason: str) -> MagicMock:
    alarm = MagicMock()
    alarm.reason = reason
    alarm.prefix = "Wrn"
    alarm.is_active = True
    alarm.is_confirmed = False
    alarm.fault_code = 0
    alarm.source = 1
    return alarm


@pytest.fixture
def fake_controller() -> MagicMock:
    ctrl = MagicMock()
    ctrl.connect = AsyncMock()
    ctrl.close = AsyncMock()
    ctrl.read_values = AsyncMock(return_value={1: 230.5, 2: 0b101})
    ctrl.read_setpoints = AsyncMock(return_value={10: 1500, 11: "Summer"})
    ctrl.read_alarms = AsyncMock(return_value=[_alarm("Generator Voltage L1-N")])
    ctrl.values = [_VOLTAGE, _BREAKER]
    ctrl.setpoints = [_RPM, _MODE]
    ctrl.setpoint_options.return_value = [(0, "Winter"), (2, "Summer")]
    ctrl.one_time_value.return_value = None
    return ctrl


async def _setup(hass: HomeAssistant, ctrl: MagicMock) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_MAC,
        title="AMF25",
        data={
            CONF_HOST: "192.168.1.9",
            CONF_PORT: 23,
            CONF_CONTROLLER_ADDRESS: 1,
            CONF_ACCESS_CODE: "0",
            CONF_PASSWORD: None,
        },
    )
    entry.add_to_hass(hass)
    with (
        patch("custom_components.comap.Controller", return_value=ctrl),
        patch("custom_components.comap.discover", new=AsyncMock(return_value=[])),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _state(hass: HomeAssistant, platform: str, key: str):
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{_MAC}_{key}")
    assert entity_id is not None, f"no {platform} entity with unique key {key}"
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def test_entities_have_values_immediately_after_setup(
    hass: HomeAssistant, fake_controller: MagicMock
) -> None:
    """No second poll has happened: every entity must already show the first poll's data."""
    await _setup(hass, fake_controller)

    assert fake_controller.read_values.await_count == 1  # only the setup refresh

    assert _state(hass, "sensor", "1").state == "230.5"
    assert _state(hass, "binary_sensor", "2_bit_0").state == "on"
    assert _state(hass, "binary_sensor", "2_bit_1").state == "off"
    assert _state(hass, "binary_sensor", "2_bit_2").state == "on"
    assert _state(hass, "number", "sp_10").state == "1500.0"
    assert _state(hass, "select", "sp_11").state == "Summer"


async def test_alarm_sensor_state_and_attributes_after_setup(
    hass: HomeAssistant, fake_controller: MagicMock
) -> None:
    await _setup(hass, fake_controller)

    state = _state(hass, "sensor", "alarm_count")

    assert state.state == "1"
    assert state.attributes["alarms"] == [
        {
            "reason": "Generator Voltage L1-N",
            "prefix": "Wrn",
            "is_active": True,
            "is_confirmed": False,
        }
    ]


async def test_entities_follow_later_polls(hass: HomeAssistant, fake_controller: MagicMock) -> None:
    entry = await _setup(hass, fake_controller)

    fake_controller.read_values.return_value = {1: 231.0, 2: 0b010}
    fake_controller.read_setpoints.return_value = {10: 1800, 11: "Winter"}
    fake_controller.read_alarms.return_value = []
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert _state(hass, "sensor", "1").state == "231.0"
    assert _state(hass, "binary_sensor", "2_bit_0").state == "off"
    assert _state(hass, "binary_sensor", "2_bit_1").state == "on"
    assert _state(hass, "number", "sp_10").state == "1800.0"
    assert _state(hass, "select", "sp_11").state == "Winter"
    alarm = _state(hass, "sensor", "alarm_count")
    assert alarm.state == "0"
    assert alarm.attributes["alarms"] == []
