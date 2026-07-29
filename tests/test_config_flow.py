"""Tests for the ComAp config flow."""

from ipaddress import IPv4Address
from unittest.mock import AsyncMock, patch

import voluptuous_serialize
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from pycomap.discovery import AccessType, DeviceType, DiscoveryDevice, DiscoveryUnit

from custom_components.comap.config_flow import _CtrlSnapshot
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


def _assert_schema_serializable(result: ConfigFlowResult) -> None:
    """Assert the form's schema can be converted to JSON for the frontend.

    `hass.config_entries.flow.async_configure` calls the flow handler directly and
    returns the raw `vol.Schema` object in `result["data_schema"]` — it never
    exercises `voluptuous_serialize.convert`, which is what the real HTTP config
    flow endpoint does before sending a form to the browser (see
    `homeassistant/helpers/data_entry_flow.py::_prepare_result_json`). A schema
    field built from a validator the serializer can't represent (e.g.
    `vol.Coerce(IPv4Address)`, a bare lambda) passes every flow-manager-level
    assertion but crashes that endpoint with `ValueError: Unable to convert
    schema: ...`. Call this on every FORM result to catch that class of bug.
    """
    assert result["type"] == FlowResultType.FORM
    voluptuous_serialize.convert(result["data_schema"], custom_serializer=cv.custom_serializer)


async def _init_to_manual(hass: HomeAssistant, flow_id: str) -> ConfigFlowResult:
    """Navigate the no_devices menu to the user_manual step."""
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"next_step_id": "user_manual"}
    )
    assert result["step_id"] == "user_manual"
    _assert_schema_serializable(result)
    return result


async def _submit_group_pages(hass: HomeAssistant, flow_id: str) -> ConfigFlowResult:
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
    _assert_schema_serializable(result)


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
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _MANUAL_INPUT)

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
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _MANUAL_INPUT)

    assert result["errors"] == {"base": "cannot_connect"}
    _assert_schema_serializable(result)


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
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _MANUAL_INPUT)

    assert result["errors"] == {"base": "cannot_identify"}
    _assert_schema_serializable(result)


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
        assert result["step_id"] == "confirm"
        _assert_schema_serializable(result)

        # Empty snapshot → both group schemas are empty → flow skips to create entry.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "192.168.1.9", CONF_PORT: 23, CONF_ACCESS_CODE: "0"},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "AMF25"


async def test_confirm_step_rejects_invalid_host(hass: HomeAssistant) -> None:
    """An unparsable host must short-circuit before _validate_connection and re-render safely."""
    with (
        patch(
            "custom_components.comap.config_flow._discover_network",
            return_value=[_MOCK_DEVICE],
        ),
        patch(
            "custom_components.comap.config_flow._validate_connection", new=AsyncMock()
        ) as mock_validate,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"device": "192.168.1.9"}
        )
        assert result["step_id"] == "confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "not-an-ip", CONF_PORT: 23, CONF_ACCESS_CODE: "0"},
        )

    assert result["errors"] == {CONF_HOST: "invalid_host"}
    mock_validate.assert_not_awaited()
    _assert_schema_serializable(result)


async def test_confirm_step_rejects_invalid_password(hass: HomeAssistant) -> None:
    """A non-numeric password must short-circuit before _validate_connection and re-render."""
    with (
        patch(
            "custom_components.comap.config_flow._discover_network",
            return_value=[_MOCK_DEVICE],
        ),
        patch(
            "custom_components.comap.config_flow._validate_connection", new=AsyncMock()
        ) as mock_validate,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"device": "192.168.1.9"}
        )
        assert result["step_id"] == "confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "192.168.1.9",
                CONF_PORT: 23,
                CONF_ACCESS_CODE: "0",
                CONF_PASSWORD: "not-a-number",
            },
        )

    assert result["errors"] == {CONF_PASSWORD: "invalid_password"}
    mock_validate.assert_not_awaited()
    _assert_schema_serializable(result)


async def test_manual_entry_rejects_invalid_host(hass: HomeAssistant) -> None:
    """An unparsable host must short-circuit before discover_host/_validate_connection."""
    with (
        patch("custom_components.comap.config_flow._discover_network", return_value=[]),
        patch(
            "custom_components.comap.config_flow._validate_connection", new=AsyncMock()
        ) as mock_validate,
        patch(
            "custom_components.comap.config_flow.discover_host", new=AsyncMock(return_value=None)
        ) as mock_discover_host,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        await _init_to_manual(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**_MANUAL_INPUT, CONF_HOST: "not-an-ip"}
        )

    assert result["errors"] == {CONF_HOST: "invalid_host"}
    mock_discover_host.assert_not_awaited()
    mock_validate.assert_not_awaited()
    _assert_schema_serializable(result)


async def test_manual_entry_rejects_invalid_password(hass: HomeAssistant) -> None:
    """A non-numeric password must short-circuit before discover_host/_validate_connection."""
    with (
        patch("custom_components.comap.config_flow._discover_network", return_value=[]),
        patch(
            "custom_components.comap.config_flow._validate_connection", new=AsyncMock()
        ) as mock_validate,
        patch(
            "custom_components.comap.config_flow.discover_host", new=AsyncMock(return_value=None)
        ) as mock_discover_host,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        await _init_to_manual(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**_MANUAL_INPUT, CONF_PASSWORD: "not-a-number"}
        )

    assert result["errors"] == {CONF_PASSWORD: "invalid_password"}
    mock_discover_host.assert_not_awaited()
    mock_validate.assert_not_awaited()
    _assert_schema_serializable(result)
