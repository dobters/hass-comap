"""DataUpdateCoordinator for the ComAp integration."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pycomap import ComApAuthError, ComApConnectionError, ComApError, ComApProtocolError, Controller
from pycomap.alarms import AlarmRecord

from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

EVENT_ALARM_ACTIVE = f"{DOMAIN}_alarm_active"
EVENT_ALARM_CLEARED = f"{DOMAIN}_alarm_cleared"

# Stable identity for an alarm across polls.
_AlarmKey = tuple[str, str, int, int]  # (reason, prefix, fault_code, source)


def _alarm_key(alarm: AlarmRecord) -> _AlarmKey:
    return (alarm.reason, alarm.prefix, alarm.fault_code, alarm.source)


@dataclass
class ComApData:
    """Snapshot of all polled controller data."""

    values: dict[int, int | float | str | bytes]
    setpoints: dict[int, int | float | str | bytes]
    alarms: list[AlarmRecord]


class ComApCoordinator(DataUpdateCoordinator[ComApData]):
    """Coordinator that polls a ComAp controller via pycomap."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        ctrl: Controller,
        config_entry: ConfigEntry,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.ctrl = ctrl
        self.config_entry = config_entry
        self._connected = False
        # Keyed by alarm identity; value is the record at the time it became active.
        # None means "first poll not yet done — don't fire events yet".
        self._active_alarms: dict[_AlarmKey, AlarmRecord] | None = None

    def _one_time_str(self, name: str) -> str | None:
        try:
            val = self.ctrl.one_time_value(name)
            return val if isinstance(val, str) and val else None
        except Exception:
            return None

    @property
    def model(self) -> str | None:
        return self._one_time_str("Application")

    @property
    def model_id(self) -> str | None:
        return self._one_time_str("ID String")

    @property
    def sw_version(self) -> str | None:
        return self._one_time_str("FW Version")

    async def _async_update_data(self) -> ComApData:
        try:
            if not self._connected:
                await self.ctrl.connect()
                self._connected = True

            values = await self.ctrl.read_values()
            setpoints = await self.ctrl.read_setpoints()
            alarms = await self.ctrl.read_alarms()
        except (ComApConnectionError, ComApProtocolError) as err:
            await self._close()
            raise UpdateFailed(str(err)) from err
        except ComApError as err:
            raise UpdateFailed(str(err)) from err

        self._process_alarm_events(alarms)
        return ComApData(values=values, setpoints=setpoints, alarms=alarms)

    def _process_alarm_events(self, alarms: list[AlarmRecord]) -> None:
        current_active = {
            _alarm_key(a): a for a in alarms if a.is_active
        }

        if self._active_alarms is None:
            # First successful poll — snapshot state without firing events so that
            # alarms already present at startup don't spam the event bus on every reload.
            self._active_alarms = current_active
            return

        entry_id = self.config_entry.entry_id

        for key, alarm in current_active.items():
            if key not in self._active_alarms:
                self.hass.bus.async_fire(
                    EVENT_ALARM_ACTIVE,
                    {
                        "entry_id": entry_id,
                        "reason": alarm.reason,
                        "prefix": alarm.prefix,
                        "is_confirmed": alarm.is_confirmed,
                        "fault_code": alarm.fault_code,
                        "source": alarm.source,
                    },
                )

        for key, alarm in self._active_alarms.items():
            if key not in current_active:
                self.hass.bus.async_fire(
                    EVENT_ALARM_CLEARED,
                    {
                        "entry_id": entry_id,
                        "reason": alarm.reason,
                        "prefix": alarm.prefix,
                        "fault_code": alarm.fault_code,
                        "source": alarm.source,
                    },
                )

        self._active_alarms = current_active

    async def async_set_setpoint(self, number: int, value: int | float | str) -> None:
        """Write a setpoint value, re-raising auth errors as HomeAssistantError."""
        try:
            await self.ctrl.set_setpoint(number, value)
        except ComApAuthError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_shutdown(self) -> None:
        """Shut down the coordinator and close the controller connection."""
        await super().async_shutdown()
        await self._close()

    async def _close(self) -> None:
        self._connected = False
        with contextlib.suppress(Exception):
            await self.ctrl.close()