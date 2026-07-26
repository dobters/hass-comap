"""Config flow for the ComAp integration."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address
from typing import Any, override

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from pycomap import (
    ComApAuthError,
    ComApConnectionError,
    ComApInvalidAccessCodeError,
    ComApInvalidPasswordError,
    Controller,
    EthernetTransport,
)
from pycomap.configuration import ValueCategory
from pycomap.datatypes import DataType
from pycomap.discovery import DiscoveryDevice, discover_host
from pycomap.protocol import ComApClient

from .const import (
    CONF_ACCESS_CODE,
    CONF_CONTROLLER_ADDRESS,
    CONF_SERIAL,
    CONF_SETPOINTS,
    CONF_VALUES,
    DEFAULT_CONTROLLER_ADDRESS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SETPOINT_GROUPS,
    DEFAULT_VALUE_GROUPS,
    DOMAIN,
    LOGGER,
)
from .discovery import discover as _discover_network
from .utils import GROUP_UNGROUPED, collect_groups, entity_key

_MANUAL_ENTRY = "__manual__"
_CONF_CONFIGURE_ENTITIES = "configure_entities"

_HOST_VALIDATOR = vol.All(str, vol.Coerce(IPv4Address))
_PORT_SELECTOR = vol.All(
    NumberSelector(NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)),
    vol.Coerce(int),
)
_HIDDEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_PASSWORD_VALIDATOR = vol.All(_HIDDEN_SELECTOR, lambda v: v or None, vol.Any(None, vol.Coerce(int)))

_SKIP_TYPES = frozenset({DataType.FTIME, DataType.FDATE, DataType.TIMER})
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


def _credentials_schema(
    host: str | None = None,
    port: int = DEFAULT_PORT,
    controller_address: int = DEFAULT_CONTROLLER_ADDRESS,
) -> vol.Schema:
    host_key = (
        vol.Required(CONF_HOST) if host is None else vol.Required(CONF_HOST, default=host)
    )
    return vol.Schema(
        {
            host_key: _HOST_VALIDATOR,
            vol.Required(CONF_PORT, default=port): _PORT_SELECTOR,
            vol.Required(CONF_CONTROLLER_ADDRESS, default=controller_address): vol.All(
                NumberSelector(NumberSelectorConfig(min=1, max=32, step=1, mode=NumberSelectorMode.BOX)),
                vol.Coerce(int),
            ),
            vol.Required(CONF_ACCESS_CODE, default="0"): _HIDDEN_SELECTOR,
            vol.Optional(CONF_PASSWORD): _PASSWORD_VALIDATOR,
        }
    )


MANUAL_SCHEMA = _credentials_schema()


@dataclass
class _CtrlSnapshot:
    values: list
    setpoints: list
    mac: str | None


def _entry_title(device: DiscoveryDevice) -> str:
    if device.units:
        return device.units[0].name
    return str(device.ip)


def _default_enabled_values(values: list) -> list[int]:
    return [
        v.number
        for v in values
        if v.category is not ValueCategory.ONE_TIME
        and v.data_type not in _SKIP_TYPES
        and entity_key(v.name) is not None
        and (v.group or GROUP_UNGROUPED) in DEFAULT_VALUE_GROUPS
    ]


def _default_enabled_setpoints(setpoints: list) -> list[int]:
    return [
        s.number
        for s in setpoints
        if (s.data_type in _NUMERIC_TYPES or s.data_type is DataType.STRING_LIST)
        and entity_key(s.name) is not None
        and (s.group or GROUP_UNGROUPED) in DEFAULT_SETPOINT_GROUPS
    ]


async def _validate_connection(
    host: IPv4Address,
    port: int,
    controller_address: int,
    access_code: str,
    password: int | None,
) -> tuple[dict[str, str], _CtrlSnapshot | None]:
    """Connect, authenticate, snapshot values/setpoints/MAC, and close."""
    ctrl = Controller(
        ComApClient(EthernetTransport(host, port), addr=controller_address),
        access_code=access_code,
        password=password,
    )
    try:
        await ctrl.connect()

        try:
            raw = await ctrl.read_value("MAC Address")
            mac: str | None = raw if isinstance(raw, str) and raw else None
        except Exception:
            mac = None

        if password is not None:
            await ctrl.elevate_access()

        return {}, _CtrlSnapshot(
            values=list(ctrl.values),
            setpoints=list(ctrl.setpoints),
            mac=mac,
        )

    except ComApInvalidAccessCodeError:
        return {"base": "invalid_auth"}, None
    except ComApConnectionError:
        return {"base": "cannot_connect"}, None
    except ComApInvalidPasswordError:
        return {CONF_PASSWORD: "invalid_password"}, None
    except ComApAuthError:
        return {"base": "invalid_auth"}, None
    except Exception:
        return {"base": "unknown"}, None

    finally:
        await ctrl.close()


class ComApConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ComAp."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, DiscoveryDevice] = {}
        self._confirm_device: DiscoveryDevice | None = None
        self._entry_data: dict[str, Any] = {}
        self._pending_options: dict[str, Any] = {}

    @override
    async def async_step_integration_discovery(
        self, discovery_info: DiscoveryDevice
    ) -> ConfigFlowResult:
        """Handle a device found by background discovery."""
        LOGGER.debug(
            "ConfigFlow.integration_discovery: ip=%s mac=%s",
            discovery_info.ip,
            discovery_info.mac,
        )
        await self.async_set_unique_id(dr.format_mac(discovery_info.mac))
        self._abort_if_unique_id_configured(updates={})
        self._confirm_device = discovery_info
        self.context["title_placeholders"] = {"name": _entry_title(discovery_info)}
        return await self.async_step_confirm()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show discovered devices or fall through to manual entry."""
        LOGGER.debug("ConfigFlow.user: user_input=%s", user_input)
        if user_input is not None:
            selected = user_input["device"]
            if selected == _MANUAL_ENTRY:
                return await self.async_step_user_manual()
            self._confirm_device = self._discovered[selected]
            return await self.async_step_confirm()

        devices = await _discover_network(self.hass)
        self._discovered = {str(d.ip): d for d in devices}
        LOGGER.debug("ConfigFlow.user: discovered=%s", list(self._discovered))

        if not self._discovered:
            return await self.async_step_no_devices()

        options: list[SelectOptionDict] = [
            SelectOptionDict(label=f"{_entry_title(d)} ({d.ip})", value=str(d.ip))
            for d in self._discovered.values()
        ]
        options.append(SelectOptionDict(label="Enter manually", value=_MANUAL_ENTRY))

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                    )
                }
            ),
        )

    async def async_step_no_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show menu when no devices are found on the network."""
        LOGGER.debug("ConfigFlow.no_devices: showing menu")
        return self.async_show_menu(
            step_id="no_devices",
            menu_options=["scan", "user_manual"],
        )

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-run discovery and return to the device list."""
        LOGGER.debug("ConfigFlow.scan: re-running discovery")
        return await self.async_step_user()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered device and validate credentials."""
        LOGGER.debug("ConfigFlow.confirm: user_input=%s", user_input)
        device_data = self._confirm_device
        assert device_data is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            errors, snapshot = await _validate_connection(
                user_input[CONF_HOST],
                user_input[CONF_PORT],
                user_input[CONF_CONTROLLER_ADDRESS],
                user_input[CONF_ACCESS_CODE],
                user_input.get(CONF_PASSWORD),
            )
            LOGGER.debug("ConfigFlow.confirm: validation errors=%s", errors)
            if not errors and snapshot is not None:
                await self.async_set_unique_id(dr.format_mac(device_data.mac))
                self._abort_if_unique_id_configured()
                self._entry_data = {
                    **user_input,
                    CONF_HOST: str(user_input[CONF_HOST]),
                    CONF_SERIAL: device_data.serial_number,
                }
                self._pending_options = {
                    CONF_VALUES: _default_enabled_values(snapshot.values),
                    CONF_SETPOINTS: _default_enabled_setpoints(snapshot.setpoints),
                }
                return self._config_create_entry()
            elif not errors:
                errors = {"base": "unknown"}

        schema = _credentials_schema(host=str(device_data.ip), port=device_data.comm_port)
        return self.async_show_form(
            step_id="confirm",
            data_schema=schema,
            errors=errors,
            last_step=True,
        )

    async def async_step_user_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual host entry with MAC resolution fallback."""
        LOGGER.debug("ConfigFlow.user_manual: user_input=%s", user_input)
        errors: dict[str, str] = {}

        if user_input is not None:
            device = await discover_host(user_input[CONF_HOST], timeout=1.0)
            if device is None:
                LOGGER.warning("ConfigFlow.user_manual: device not discovered at %s", user_input[CONF_HOST])
            errors, snapshot = await _validate_connection(
                user_input[CONF_HOST],
                user_input[CONF_PORT],
                user_input[CONF_CONTROLLER_ADDRESS],
                user_input[CONF_ACCESS_CODE],
                user_input.get(CONF_PASSWORD),
            )
            LOGGER.debug("ConfigFlow.user_manual: validation errors=%s", errors)
            if not errors and snapshot is not None:
                mac = device.mac if device is not None else snapshot.mac

                LOGGER.debug(
                    "ConfigFlow.user_manual: device=%s mac=%s", device, mac
                )
                if not mac:
                    errors = {"base": "cannot_identify"}
                else:
                    self._confirm_device = device
                    await self.async_set_unique_id(dr.format_mac(mac))
                    self._abort_if_unique_id_configured()
                    serial = device.serial_number if device is not None else None
                    self._entry_data = {
                        **user_input,
                        CONF_HOST: str(user_input[CONF_HOST]),
                        CONF_SERIAL: serial,
                    }
                    self._pending_options = {
                        CONF_VALUES: _default_enabled_values(snapshot.values),
                        CONF_SETPOINTS: _default_enabled_setpoints(snapshot.setpoints),
                    }
                    return self._config_create_entry()
            elif not errors:
                errors = {"base": "unknown"}

        return self.async_show_form(
            step_id="user_manual",
            data_schema=MANUAL_SCHEMA,
            errors=errors,
            last_step=True,
        )

    def _config_create_entry(self) -> ConfigFlowResult:
        title = (
            _entry_title(self._confirm_device)
            if self._confirm_device is not None
            else self._entry_data.get(CONF_HOST, "ComAp")
        )
        LOGGER.debug(
            "ConfigFlow: creating entry title=%s options=%s",
            title,
            self._pending_options,
        )
        return self.async_create_entry(
            title=title,
            data=self._entry_data,
            options=self._pending_options,
        )

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> ComApOptionsFlow:
        return ComApOptionsFlow(config_entry)


class ComApOptionsFlow(OptionsFlow):
    """Options flow — password, poll interval, and per-item entity selection."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.coordinator = config_entry.runtime_data
        self.options = config_entry.options
        self._pending: dict[str, Any] = {}
        self._value_groups: list[tuple[str, list]] = []
        self._setpoint_groups: list[tuple[str, list]] = []
        self._current_value_group: int = 0
        self._current_setpoint_group: int = 0
        self._enabled_values: set[int] = set()
        self._enabled_setpoints: set[int] = set()
        LOGGER.debug(
            "OptionsFlow.__init__: entry=%s coordinator=%s values=%d setpoints=%d",
            config_entry.entry_id,
            type(self.coordinator).__name__,
            len(self.coordinator.ctrl.values),
            len(self.coordinator.ctrl.setpoints),
        )

    def _setup_groups(self) -> None:
        ctrl = self.coordinator.ctrl

        entity_values = [
            v for v in ctrl.values
            if v.category is not ValueCategory.ONE_TIME
            and v.data_type not in _SKIP_TYPES
            and entity_key(v.name) is not None
        ]
        group_names = collect_groups(entity_values)
        self._value_groups = [
            (g, [v for v in entity_values if (v.group or GROUP_UNGROUPED) == g])
            for g in group_names
        ]

        entity_setpoints = [
            s for s in ctrl.setpoints
            if (s.data_type in _NUMERIC_TYPES or s.data_type is DataType.STRING_LIST)
            and entity_key(s.name) is not None
        ]
        sp_group_names = collect_groups(entity_setpoints)
        self._setpoint_groups = [
            (g, [s for s in entity_setpoints if (s.group or GROUP_UNGROUPED) == g])
            for g in sp_group_names
        ]

    def _initial_enabled_values(self) -> set[int]:
        saved = self.options.get(CONF_VALUES)
        if saved is not None:
            return set(saved)
        ctrl = self.coordinator.ctrl
        return {
            v.number
            for v in ctrl.values
            if v.category is not ValueCategory.ONE_TIME
            and v.data_type not in _SKIP_TYPES
            and entity_key(v.name) is not None
            and (v.group or GROUP_UNGROUPED) in DEFAULT_VALUE_GROUPS
        }

    def _initial_enabled_setpoints(self) -> set[int]:
        saved = self.options.get(CONF_SETPOINTS)
        if saved is not None:
            return set(saved)
        ctrl = self.coordinator.ctrl
        return {
            s.number
            for s in ctrl.setpoints
            if (s.data_type in _NUMERIC_TYPES or s.data_type is DataType.STRING_LIST)
            and entity_key(s.name) is not None
            and (s.group or GROUP_UNGROUPED) in DEFAULT_SETPOINT_GROUPS
        }

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect password, poll interval, and optional entity configuration."""
        LOGGER.debug("OptionsFlow.init: user_input=%s", user_input)
        if user_input is not None:
            configure = user_input.get(_CONF_CONFIGURE_ENTITIES, False)
            self._pending = {k: v for k, v in user_input.items() if k != _CONF_CONFIGURE_ENTITIES}
            if configure:
                self._setup_groups()
                self._enabled_values = self._initial_enabled_values()
                self._enabled_setpoints = self._initial_enabled_setpoints()
                self._current_value_group = 0
                self._current_setpoint_group = 0
                return await self.async_step_value_group()
            # Preserve existing entity configuration unchanged.
            for key in (CONF_VALUES, CONF_SETPOINTS):
                if key in self.options:
                    self._pending[key] = self.options[key]
            return self.async_create_entry(data=self._pending)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(
                        NumberSelector(
                            NumberSelectorConfig(
                                min=5, max=300, step=1, mode=NumberSelectorMode.BOX
                            )
                        ),
                        vol.Coerce(int),
                    ),
                    vol.Optional(_CONF_CONFIGURE_ENTITIES, default=False): BooleanSelector(),
                }
            ),
        )

    async def async_step_value_group(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enable/disable individual values within the current group."""
        if user_input is not None:
            _, group_values = self._value_groups[self._current_value_group]
            selected = {int(n) for n in user_input.get("enabled", [])}
            group_numbers = {v.number for v in group_values}
            self._enabled_values = (self._enabled_values - group_numbers) | selected
            self._current_value_group += 1

        # Advance past any empty groups.
        while (
            self._current_value_group < len(self._value_groups)
            and not self._value_groups[self._current_value_group][1]
        ):
            self._current_value_group += 1

        if self._current_value_group >= len(self._value_groups):
            return await self.async_step_setpoint_group()

        group_name, group_values = self._value_groups[self._current_value_group]
        options = [
            SelectOptionDict(label=f"{v.number} — {v.name}", value=str(v.number))
            for v in group_values
        ]
        default = [str(v.number) for v in group_values if v.number in self._enabled_values]
        is_last = (
            self._current_value_group == len(self._value_groups) - 1
            and not self._setpoint_groups
        )
        return self.async_show_form(
            step_id="value_group",
            data_schema=vol.Schema(
                {
                    vol.Optional("enabled", default=default): SelectSelector(
                        SelectSelectorConfig(
                            options=options, multiple=True, mode=SelectSelectorMode.LIST
                        )
                    ),
                }
            ),
            description_placeholders={"group": group_name},
            last_step=is_last,
        )

    async def async_step_setpoint_group(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enable/disable individual setpoints within the current group."""
        if user_input is not None:
            _, group_setpoints = self._setpoint_groups[self._current_setpoint_group]
            selected = {int(n) for n in user_input.get("enabled", [])}
            group_numbers = {s.number for s in group_setpoints}
            self._enabled_setpoints = (self._enabled_setpoints - group_numbers) | selected
            self._current_setpoint_group += 1

        # Advance past any empty groups.
        while (
            self._current_setpoint_group < len(self._setpoint_groups)
            and not self._setpoint_groups[self._current_setpoint_group][1]
        ):
            self._current_setpoint_group += 1

        if self._current_setpoint_group >= len(self._setpoint_groups):
            self._pending[CONF_VALUES] = list(self._enabled_values)
            self._pending[CONF_SETPOINTS] = list(self._enabled_setpoints)
            LOGGER.debug("OptionsFlow saving: %s", self._pending)
            return self.async_create_entry(data=self._pending)

        group_name, group_setpoints = self._setpoint_groups[self._current_setpoint_group]
        options = [
            SelectOptionDict(label=f"{s.number} — {s.name}", value=str(s.number))
            for s in group_setpoints
        ]
        default = [str(s.number) for s in group_setpoints if s.number in self._enabled_setpoints]
        is_last = self._current_setpoint_group == len(self._setpoint_groups) - 1
        return self.async_show_form(
            step_id="setpoint_group",
            data_schema=vol.Schema(
                {
                    vol.Optional("enabled", default=default): SelectSelector(
                        SelectSelectorConfig(
                            options=options, multiple=True, mode=SelectSelectorMode.LIST
                        )
                    ),
                }
            ),
            description_placeholders={"group": group_name},
            last_step=is_last,
        )
