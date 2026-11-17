"""Config flow for the ComAp integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import IPv4Address
from typing import Any, cast, override

import probatio
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
from homeassistant.helpers.typing import DiscoveryInfoType
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

_HOST_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
_PORT_SELECTOR = probatio.All(
    NumberSelector(NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)),
    probatio.Coerce(int),
)
_HIDDEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

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
) -> probatio.Schema:
    host_key = (
        probatio.Required(CONF_HOST) if host is None else probatio.Required(CONF_HOST, default=host)
    )
    return probatio.Schema(
        {
            host_key: _HOST_SELECTOR,
            probatio.Required(CONF_PORT, default=port): _PORT_SELECTOR,
            probatio.Required(CONF_CONTROLLER_ADDRESS, default=controller_address): probatio.All(
                NumberSelector(
                    NumberSelectorConfig(min=1, max=32, step=1, mode=NumberSelectorMode.BOX)
                ),
                probatio.Coerce(int),
            ),
            probatio.Required(CONF_ACCESS_CODE, default="0"): _HIDDEN_SELECTOR,
            probatio.Optional(CONF_PASSWORD): _HIDDEN_SELECTOR,
        }
    )


MANUAL_SCHEMA = _credentials_schema()


@dataclass
class _CtrlSnapshot:
    values: list
    setpoints: list
    mac: str | None


_SECRET_KEYS = (CONF_ACCESS_CODE, CONF_PASSWORD)


def _redact(user_input: dict[str, Any] | None) -> dict[str, Any] | None:
    """Copy of ``user_input`` that is safe to log (credentials masked)."""
    if user_input is None:
        return None
    return {k: "**REDACTED**" if k in _SECRET_KEYS and v else v for k, v in user_input.items()}


def _parse_host(value: str) -> IPv4Address | None:
    try:
        return IPv4Address(value)
    except ValueError:
        return None


def _parse_password(value: str | None) -> int | None:
    """Return the coerced password, or ``None`` if blank.

    Raises:
        ValueError: if `value` is non-blank and not a valid integer.

    """
    return int(value) if value else None


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

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication after the controller rejected the access code."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new access code and verify it before saving."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            # The write password is deliberately not sent here: it is a separate credential with
            # brute-force protection on the controller, and only the access code is in question.
            errors, _ = await _validate_connection(
                IPv4Address(entry.data[CONF_HOST]),
                entry.data.get(CONF_PORT, DEFAULT_PORT),
                entry.data.get(CONF_CONTROLLER_ADDRESS, DEFAULT_CONTROLLER_ADDRESS),
                user_input[CONF_ACCESS_CODE],
                None,
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_ACCESS_CODE: user_input[CONF_ACCESS_CODE]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema({probatio.Required(CONF_ACCESS_CODE): _HIDDEN_SELECTOR}),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    @override
    async def async_step_integration_discovery(
        self, discovery_info: DiscoveryInfoType
    ) -> ConfigFlowResult:
        """Handle a device found by background discovery."""
        device = cast(DiscoveryDevice, discovery_info)
        LOGGER.debug(
            "ConfigFlow.integration_discovery: ip=%s mac=%s",
            device.ip,
            device.mac,
        )
        await self.async_set_unique_id(dr.format_mac(device.mac))
        self._abort_if_unique_id_configured(updates={})
        self._confirm_device = device
        self.context["title_placeholders"] = {"name": _entry_title(device)}
        return await self.async_step_confirm()

    @override
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
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
            data_schema=probatio.Schema(
                {
                    probatio.Required("device"): SelectSelector(
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

    async def async_step_scan(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Re-run discovery and return to the device list."""
        LOGGER.debug("ConfigFlow.scan: re-running discovery")
        return await self.async_step_user()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered device and validate credentials."""
        LOGGER.debug("ConfigFlow.confirm: user_input=%s", _redact(user_input))
        device_data = self._confirm_device
        assert device_data is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            host = _parse_host(user_input[CONF_HOST])
            if host is None:
                errors[CONF_HOST] = "invalid_host"

            password: int | None = None
            try:
                password = _parse_password(user_input.get(CONF_PASSWORD))
            except ValueError:
                errors[CONF_PASSWORD] = "invalid_password"

            if host is not None and not errors:
                errors, snapshot = await _validate_connection(
                    host,
                    user_input[CONF_PORT],
                    user_input[CONF_CONTROLLER_ADDRESS],
                    user_input[CONF_ACCESS_CODE],
                    password,
                )
                LOGGER.debug("ConfigFlow.confirm: validation errors=%s", errors)
                if not errors and snapshot is not None:
                    await self.async_set_unique_id(dr.format_mac(device_data.mac))
                    self._abort_if_unique_id_configured()
                    self._entry_data = {
                        **user_input,
                        CONF_HOST: str(host),
                        CONF_PASSWORD: password,
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
        LOGGER.debug("ConfigFlow.user_manual: user_input=%s", _redact(user_input))
        errors: dict[str, str] = {}

        if user_input is not None:
            host = _parse_host(user_input[CONF_HOST])
            if host is None:
                errors[CONF_HOST] = "invalid_host"

            password: int | None = None
            try:
                password = _parse_password(user_input.get(CONF_PASSWORD))
            except ValueError:
                errors[CONF_PASSWORD] = "invalid_password"

            if host is not None and not errors:
                device = await discover_host(host, timeout=1.0)
                if device is None:
                    LOGGER.warning("ConfigFlow.user_manual: device not discovered at %s", host)
                errors, snapshot = await _validate_connection(
                    host,
                    user_input[CONF_PORT],
                    user_input[CONF_CONTROLLER_ADDRESS],
                    user_input[CONF_ACCESS_CODE],
                    password,
                )
                LOGGER.debug("ConfigFlow.user_manual: validation errors=%s", errors)
                if not errors and snapshot is not None:
                    mac = device.mac if device is not None else snapshot.mac

                    LOGGER.debug("ConfigFlow.user_manual: device=%s mac=%s", device, mac)
                    if not mac:
                        errors = {"base": "cannot_identify"}
                    else:
                        self._confirm_device = device
                        await self.async_set_unique_id(dr.format_mac(mac))
                        self._abort_if_unique_id_configured()
                        serial = device.serial_number if device is not None else None
                        self._entry_data = {
                            **user_input,
                            CONF_HOST: str(host),
                            CONF_PASSWORD: password,
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
    """Options flow — poll interval, write password, and per-item entity selection."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.coordinator = config_entry.runtime_data
        self.options = config_entry.options
        self._entry = config_entry
        self._new_password: int | None = None
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
            v
            for v in ctrl.values
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
            s
            for s in ctrl.setpoints
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

    def _save(self) -> ConfigFlowResult:
        """Persist options, and the new write password (stored in entry data) if one was given.

        Both go through a single ``async_update_entry`` so the entry reloads once.
        """
        if self._new_password is not None:
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={**self._entry.data, CONF_PASSWORD: self._new_password},
                options=self._pending,
            )
        return self.async_create_entry(data=self._pending)

    async def _validate_new_password(self, raw: str) -> tuple[dict[str, str], int | None]:
        """Parse the password and check it against the controller (one attempt)."""
        try:
            password = _parse_password(raw)
        except ValueError:
            return {CONF_PASSWORD: "invalid_password"}, None
        data = self._entry.data
        errors, _ = await _validate_connection(
            IPv4Address(data[CONF_HOST]),
            data.get(CONF_PORT, DEFAULT_PORT),
            data.get(CONF_CONTROLLER_ADDRESS, DEFAULT_CONTROLLER_ADDRESS),
            data[CONF_ACCESS_CODE],
            password,
        )
        return errors, password

    def _init_form(self, errors: dict[str, str] | None = None) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(
                {
                    probatio.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): probatio.All(
                        NumberSelector(
                            NumberSelectorConfig(
                                min=5, max=300, step=1, mode=NumberSelectorMode.BOX
                            )
                        ),
                        probatio.Coerce(int),
                    ),
                    probatio.Optional(CONF_PASSWORD): _HIDDEN_SELECTOR,
                    probatio.Optional(_CONF_CONFIGURE_ENTITIES, default=False): BooleanSelector(),
                }
            ),
            errors=errors or {},
        )

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect poll interval, optional new write password, and entity configuration."""
        LOGGER.debug(
            "OptionsFlow.init: configure=%s", (user_input or {}).get(_CONF_CONFIGURE_ENTITIES)
        )
        if user_input is None:
            return self._init_form()

        # Blank password means "keep the current one". A new one is verified first so a typo
        # never gets saved (and never reaches the controller more than once per submit).
        raw_password = user_input.get(CONF_PASSWORD)
        self._new_password = None
        if raw_password:
            errors, password = await self._validate_new_password(raw_password)
            if errors:
                return self._init_form(errors)
            self._new_password = password

        configure = user_input.get(_CONF_CONFIGURE_ENTITIES, False)
        self._pending = {
            k: v
            for k, v in user_input.items()
            if k not in (_CONF_CONFIGURE_ENTITIES, CONF_PASSWORD)
        }
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
        return self._save()

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
            self._current_value_group == len(self._value_groups) - 1 and not self._setpoint_groups
        )
        return self.async_show_form(
            step_id="value_group",
            data_schema=probatio.Schema(
                {
                    probatio.Optional("enabled", default=default): SelectSelector(
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
            return self._save()

        group_name, group_setpoints = self._setpoint_groups[self._current_setpoint_group]
        options = [
            SelectOptionDict(label=f"{s.number} — {s.name}", value=str(s.number))
            for s in group_setpoints
        ]
        default = [str(s.number) for s in group_setpoints if s.number in self._enabled_setpoints]
        is_last = self._current_setpoint_group == len(self._setpoint_groups) - 1
        return self.async_show_form(
            step_id="setpoint_group",
            data_schema=probatio.Schema(
                {
                    probatio.Optional("enabled", default=default): SelectSelector(
                        SelectSelectorConfig(
                            options=options, multiple=True, mode=SelectSelectorMode.LIST
                        )
                    ),
                }
            ),
            description_placeholders={"group": group_name},
            last_step=is_last,
        )
