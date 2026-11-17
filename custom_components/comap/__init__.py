"""ComAp controller Home Assistant integration."""

from __future__ import annotations

from datetime import timedelta
from ipaddress import IPv4Address
from typing import TYPE_CHECKING, Any

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SCAN_INTERVAL, Platform
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import discovery_flow
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from pycomap import (
    ComApAuthError,
    ComApConnectionError,
    Controller,
    EthernetTransport,
)
from pycomap.protocol import ComApClient

from .const import (
    CONF_ACCESS_CODE,
    CONF_CONTROLLER_ADDRESS,
    DEFAULT_CONTROLLER_ADDRESS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DISCOVERY_INTERVAL_SECONDS,
    DOMAIN,
)
from .coordinator import ISSUE_PASSWORD_REJECTED, ComApCoordinator
from .discovery import discover

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Start background discovery at startup and on a repeating interval."""

    async def _discover(*_: Any) -> None:
        devices = await discover(hass)

        configured_macs = {
            entry.unique_id
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.unique_id
        }

        for device in devices:
            if dr.format_mac(device.mac) in configured_macs:
                continue
            discovery_flow.async_create_flow(
                hass,
                DOMAIN,
                context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
                data=device,
            )

    hass.async_create_background_task(_discover(), "comap_discovery", eager_start=True)
    async_track_time_interval(
        hass,
        _discover,
        timedelta(seconds=DISCOVERY_INTERVAL_SECONDS),
        cancel_on_shutdown=True,
    )
    return True


PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
]

type ComApConfigEntry = ConfigEntry[ComApCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: ComApConfigEntry) -> bool:
    """Set up ComAp from a config entry."""
    ctrl = Controller(
        ComApClient(
            EthernetTransport(
                IPv4Address(entry.data[CONF_HOST]),
                entry.data.get(CONF_PORT, DEFAULT_PORT),
            ),
            addr=entry.data.get(CONF_CONTROLLER_ADDRESS, DEFAULT_CONTROLLER_ADDRESS),
        ),
        access_code=entry.data[CONF_ACCESS_CODE],
        password=entry.data.get(CONF_PASSWORD),
    )

    scan_interval: int = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = ComApCoordinator(hass, ctrl, entry, scan_interval)

    try:
        await coordinator.async_config_entry_first_refresh()
    except (ComApConnectionError, ComApAuthError) as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _reload_entry(hass: HomeAssistant, entry: ComApConfigEntry) -> None:
        await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_reload_entry))
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ComApConfigEntry) -> None:
    """Clean up the repair issue when the entry is deleted."""
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_PASSWORD_REJECTED}_{entry.entry_id}")


async def async_unload_entry(hass: HomeAssistant, entry: ComApConfigEntry) -> bool:
    """Unload a config entry."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_shutdown()
    return ok
