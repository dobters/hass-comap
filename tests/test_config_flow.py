"""Tests for the ComAp config flow."""

from ipaddress import IPv4Address
from unittest.mock import AsyncMock, patch

from custom_components.comap.config_flow import _CtrlSnapshot

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pycomap.discovery import AccessType, DeviceType, DiscoveryDevice, DiscoveryUnit

from custom_components.comap.const import CONF_ACCESS_CODE, DEFAULT_PORT, DOMAIN

_MOCK_DEVICE = DiscoveryDevice(
    format_version=1,
    device_type=DeviceType.IB_LITE,
    serial_number=12345,
    firmware_major_minor=0x0A00,
    firmware_patch_build=0,
    ip=IPv4Address("192.168.1.9"),
    mac="aa:bb:cc:dd:ee:ff",
    comm_port=23,
    access_type=AccessType.IP_ADDRESS,
    airgate_identifier="",
    connected_units=1,
    is_units_list_complete=True,
    units=[DiscoveryUnit(type=0, name="AMF25", serial="abcd1234")],
)

_MANUAL_INPUT = {
    CONF_HOST: "192.168.1.9",
    CONF_PORT: DEFAULT_PORT,
    CONF_ACCESS_CODE: "0",
}


def _mock_snapshot(mac: str | None = "aa:bb:cc:dd:ee:ff") -> _CtrlSnapshot:
    return _CtrlSnapshot(values=[], setpoints=[], mac=mac)


async def _init_to_manual(hass: HomeAssistant, flow_id: str) -> dict:
    """Navigate the no_devices menu to the user_manual step."""
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"next_step_id": "user_manual"}
    )
    assert result["step_id"] == "user_manual"
    return result


async def _submit_group_pages(hass: HomeAssistant, flow_id: str) -> dict:
    """Submit both group selection pages with empty input (all defaults).

    Only called when the mock ctrl exposes at least one value/setpoint so
    both form steps are actually shown.
    """
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["step_id"] == "setpoints_groups"
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    return result


async def test_manual_entry_no_discovery(hass: HomeAssistant) -> None:
    """When broadcast finds nothing, flow shows the no_devices menu."""
    with patch("custom_components.comap.config_flow._discover_network", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "no_devices"


async def test_scan_again_reruns_discovery(hass: HomeAssistant) -> None:
    """Choosing 'scan' on the no_devices menu re-runs discovery."""
    with patch("custom_components.comap.config_flow._discover_network", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert result["step_id"] == "no_devices"

    with patch(
        "custom_components.comap.config_flow._discover_network",
        return_value=[_MOCK_DEVICE],
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "scan"}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_manual_entry_creates_entry(hass: HomeAssistant) -> None:
    with (
        patch("custom_components.comap.discover", return_value=[]),
        patch("custom_components.comap.config_flow._discover_network", return_value=[]),
        patch(
            "custom_components.comap.config_flow._validate_connection",
            return_value=({}, _mock_snapshot()),
        ),
        patch(
            "custom_components.comap.config_flow.discover_host",
            new=AsyncMock(return_value=_MOCK_DEVICE),
        ),
        patch("custom_components.comap.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        await _init_to_manual(hass, result["flow_id"])
        # Empty snapshot → both group schemas are empty → flow skips to create entry.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _MANUAL_INPUT
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "AMF25"
    assert result["data"][CONF_HOST] == "192.168.1.9"
    assert result["data"]["serial_number"] == 12345


async def test_manual_entry_cannot_connect(hass: HomeAssistant) -> None:
    with (
        patch("custom_components.comap.config_flow._discover_network", return_value=[]),
        patch(
            "custom_components.comap.config_flow.discover_host",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.comap.config_flow._validate_connection",
            return_value=({"base": "cannot_connect"}, None),
        ),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        await _init_to_manual(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _MANUAL_INPUT
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_manual_entry_cannot_identify(hass: HomeAssistant) -> None:
    """Probe succeeds TCP but both discovery and MAC-from-values return nothing."""
    with (
        patch("custom_components.comap.config_flow._discover_network", return_value=[]),
        patch(
            "custom_components.comap.config_flow._validate_connection",
            return_value=({}, _mock_snapshot(mac=None)),
        ),
        patch(
            "custom_components.comap.config_flow.discover_host",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        await _init_to_manual(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _MANUAL_INPUT
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_identify"}


async def test_discovery_flow_creates_entry(hass: HomeAssistant) -> None:
    with (
        patch("custom_components.comap.discover", return_value=[]),
        patch(
            "custom_components.comap.config_flow._discover_network",
            return_value=[_MOCK_DEVICE],
        ),
        patch(
            "custom_components.comap.config_flow._validate_connection",
            return_value=({}, _mock_snapshot()),
        ),
        patch("custom_components.comap.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"device": "192.168.1.9"}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "confirm"

        # Empty snapshot → both group schemas are empty → flow skips to create entry.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "192.168.1.9", CONF_PORT: 23, CONF_ACCESS_CODE: "0"},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "AMF25"