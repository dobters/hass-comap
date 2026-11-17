"""DataUpdateCoordinator for the ComAp integration."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pycomap import (
    ComApAuthError,
    ComApConnectionError,
    ComApError,
    ComApInvalidAccessCodeError,
    ComApInvalidPasswordError,
    ComApProtocolError,
    Controller,
)
from pycomap.alarms import AlarmRecord
from pycomap.protocol import ControllerCommand

from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

EVENT_ALARM_ACTIVE = f"{DOMAIN}_alarm_active"
EVENT_ALARM_CLEARED = f"{DOMAIN}_alarm_cleared"

# Safety net around a whole poll (connect + three reads). pycomap enforces its own per-read
# socket timeouts; this guarantees a stuck poll can never freeze the update loop.
UPDATE_TIMEOUT = 60
# Same idea for a single write (setpoint / command) triggered from the UI.
WRITE_TIMEOUT = 30

ISSUE_PASSWORD_REJECTED = "password_rejected"
_PASSWORD_REJECTED_MSG = (
    "The controller rejected the write password. Writes that need it are paused to avoid "
    "locking the controller; update the password in the integration options."
)

# Stable identity for an alarm across polls.
_AlarmKey = tuple[str, str, int, int]  # (reason, prefix, fault_code, source)


def _describe(err: BaseException) -> str:
    """Short, traceback-free description of a communication failure."""
    return str(err) or type(err).__name__


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
        # Set when the controller rejects the write password. The controller blocks after a few
        # wrong attempts (and eventually locks permanently), so once rejected we stop sending the
        # password until the entry is reloaded (e.g. after the password was changed in options).
        self._password_rejected = False
        ir.async_delete_issue(hass, DOMAIN, self._issue_id)
        # Keyed by alarm identity; value is the record at the time it became active.
        # None means "first poll not yet done — don't fire events yet".
        self._active_alarms: dict[_AlarmKey, AlarmRecord] | None = None

    @property
    def _issue_id(self) -> str:
        return f"{ISSUE_PASSWORD_REJECTED}_{self.config_entry.entry_id}"

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
            async with asyncio.timeout(UPDATE_TIMEOUT):
                if not self._connected:
                    await self.ctrl.connect()
                    self._connected = True

                values = await self.ctrl.read_values()
                setpoints = await self.ctrl.read_setpoints()
                alarms = await self.ctrl.read_alarms()
        except ComApInvalidAccessCodeError as err:
            # Retrying with a wrong access code every poll is pointless (and hammers the
            # controller); hand over to HA's reauth flow, which pauses polling until fixed.
            await self._close()
            raise ConfigEntryAuthFailed(_describe(err)) from err
        except (ComApError, OSError, TimeoutError) as err:
            # Whatever went wrong, the session may be half-dead or out of sync. Drop it so the
            # next poll opens a fresh connection instead of reusing a broken one.
            await self._close()
            raise UpdateFailed(_describe(err)) from err

        self._process_alarm_events(alarms)
        return ComApData(values=values, setpoints=setpoints, alarms=alarms)

    def _process_alarm_events(self, alarms: list[AlarmRecord]) -> None:
        current_active = {_alarm_key(a): a for a in alarms if a.is_active}

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

    @contextlib.asynccontextmanager
    async def _guard_write(self, *, needs_password: bool = False) -> AsyncGenerator[None]:
        """Bound a UI-triggered write and turn failures into HomeAssistantError.

        Connection-level failures also drop the session so the next poll reconnects. A rejected
        write password latches ``_password_rejected`` so we never send a known-bad password again.
        """
        if needs_password and self._password_rejected:
            raise HomeAssistantError(_PASSWORD_REJECTED_MSG)
        try:
            async with asyncio.timeout(WRITE_TIMEOUT):
                yield
        except ComApInvalidPasswordError as err:
            self._reject_password()
            raise HomeAssistantError(_PASSWORD_REJECTED_MSG) from err
        except ComApAuthError as err:
            raise HomeAssistantError(str(err)) from err
        except (ComApConnectionError, ComApProtocolError, OSError, TimeoutError) as err:
            await self._close()
            raise HomeAssistantError(_describe(err)) from err
        except ComApError as err:
            raise HomeAssistantError(str(err)) from err

    def _reject_password(self) -> None:
        self._password_rejected = True
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_PASSWORD_REJECTED,
            translation_placeholders={"name": self.config_entry.title},
        )

    def _setpoint_needs_password(self, number: int) -> bool:
        try:
            return self.ctrl.setpoint_info(number).needs_password
        except KeyError:
            return False

    async def async_set_setpoint(self, number: int, value: int | float | str) -> None:
        """Write a setpoint value, re-raising controller errors as HomeAssistantError."""
        async with self._guard_write(needs_password=self._setpoint_needs_password(number)):
            await self.ctrl.set_setpoint(number, value)

    async def async_execute_command(self, command: ControllerCommand) -> None:
        """Send a controller command, re-raising errors as HomeAssistantError.

        Commands never send the write password (pycomap's ``execute_command`` doesn't elevate),
        so they are not affected by a rejected password.
        """
        async with self._guard_write():
            await self.ctrl.execute_command(command)

    async def async_shutdown(self) -> None:
        """Shut down the coordinator and close the controller connection."""
        await super().async_shutdown()
        await self._close()

    async def _close(self) -> None:
        self._connected = False
        with contextlib.suppress(Exception):
            await self.ctrl.close()
