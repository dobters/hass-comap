"""Tests for ComApCoordinator."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from pycomap import (
    ComApAuthError,
    ComApControllerError,
    ComApInvalidAccessCodeError,
    ComApInvalidPasswordError,
    Command,
)

from custom_components.comap import coordinator as coordinator_module
from custom_components.comap.const import DOMAIN
from custom_components.comap.coordinator import ComApCoordinator, ComApData


async def test_update_populates_data(hass: HomeAssistant, mock_controller: MagicMock) -> None:
    mock_controller.read_values.return_value = {1: 230.0, 2: 50.0}
    mock_controller.read_setpoints.return_value = {100: 1500}
    mock_controller.read_alarms.return_value = []

    entry = MagicMock()
    entry.unique_id = "aabbccddeeff"

    coordinator = ComApCoordinator(hass, mock_controller, entry, scan_interval=10)
    data = await coordinator._async_update_data()

    assert isinstance(data, ComApData)
    assert data.values == {1: 230.0, 2: 50.0}
    assert data.setpoints == {100: 1500}
    assert data.alarms == []
    mock_controller.connect.assert_called_once()
    mock_controller.read_values.assert_called_once()
    mock_controller.read_setpoints.assert_called_once()
    mock_controller.read_alarms.assert_called_once()


async def test_reconnects_after_disconnect(hass: HomeAssistant, mock_controller: MagicMock) -> None:
    """Second update re-connects when _connected flag was cleared."""
    entry = MagicMock()
    entry.unique_id = "aabbccddeeff"

    coordinator = ComApCoordinator(hass, mock_controller, entry, scan_interval=10)
    coordinator._connected = False

    await coordinator._async_update_data()
    assert mock_controller.connect.call_count == 1

    coordinator._connected = False
    await coordinator._async_update_data()
    assert mock_controller.connect.call_count == 2


@pytest.mark.parametrize(
    "exc_class",
    ["ComApConnectionError", "ComApProtocolError"],
)
async def test_update_raises_update_failed_on_connection_error(
    hass: HomeAssistant,
    mock_controller: MagicMock,
    exc_class: str,
) -> None:
    import pycomap

    exc = getattr(pycomap, exc_class)("boom")
    mock_controller.connect.side_effect = exc

    entry = MagicMock()
    entry.unique_id = "aabbccddeeff"

    coordinator = ComApCoordinator(hass, mock_controller, entry, scan_interval=10)

    from homeassistant.helpers.update_coordinator import UpdateFailed

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


# -- resilience: hung sockets, socket errors, reconnect ------------------------------------------


def _coordinator(hass: HomeAssistant, ctrl: MagicMock) -> ComApCoordinator:
    entry = MagicMock()
    entry.unique_id = "aabbccddeeff"
    entry.entry_id = "entry1"
    entry.title = "AMF25"
    return ComApCoordinator(hass, ctrl, entry, scan_interval=10)


async def test_hung_read_times_out_and_reconnects_on_next_poll(
    hass: HomeAssistant,
    mock_controller: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read that never returns must not freeze polling; the next poll reconnects."""
    monkeypatch.setattr(coordinator_module, "UPDATE_TIMEOUT", 0.05)
    hang = asyncio.Event()

    async def never_returns() -> dict:
        await hang.wait()
        return {}

    mock_controller.read_values.side_effect = never_returns
    coordinator = _coordinator(hass, mock_controller)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    mock_controller.close.assert_awaited_once()
    assert coordinator._connected is False

    mock_controller.read_values.side_effect = None
    mock_controller.read_values.return_value = {1: 1.0}
    data = await coordinator._async_update_data()

    assert data.values == {1: 1.0}
    assert mock_controller.connect.await_count == 2


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionResetError("reset by peer"),
        BrokenPipeError("broken pipe"),
        TimeoutError(),
        ComApControllerError(5),
        ComApAuthError("bad access code"),
    ],
    ids=["reset", "broken-pipe", "timeout", "controller-error", "auth-error"],
)
async def test_failures_close_session_and_reconnect(
    hass: HomeAssistant, mock_controller: MagicMock, exc: Exception
) -> None:
    mock_controller.read_values.side_effect = exc
    coordinator = _coordinator(hass, mock_controller)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    mock_controller.close.assert_awaited_once()
    assert coordinator._connected is False

    mock_controller.read_values.side_effect = None
    await coordinator._async_update_data()
    assert mock_controller.connect.await_count == 2


async def test_update_failed_message_is_never_empty(
    hass: HomeAssistant, mock_controller: MagicMock
) -> None:
    mock_controller.read_values.side_effect = TimeoutError()
    coordinator = _coordinator(hass, mock_controller)

    with pytest.raises(UpdateFailed, match="TimeoutError"):
        await coordinator._async_update_data()


async def test_close_failure_does_not_mask_update_failed(
    hass: HomeAssistant, mock_controller: MagicMock
) -> None:
    mock_controller.read_values.side_effect = ConnectionResetError("reset")
    mock_controller.close.side_effect = OSError("already dead")
    coordinator = _coordinator(hass, mock_controller)

    with pytest.raises(UpdateFailed, match="reset"):
        await coordinator._async_update_data()


# -- writes ---------------------------------------------------------------------------------------


async def test_set_setpoint_connection_error_closes_session(
    hass: HomeAssistant, mock_controller: MagicMock
) -> None:
    mock_controller.set_setpoint = AsyncMock(side_effect=ConnectionResetError("reset"))
    coordinator = _coordinator(hass, mock_controller)
    coordinator._connected = True

    with pytest.raises(HomeAssistantError, match="reset"):
        await coordinator.async_set_setpoint(1, 5)
    mock_controller.close.assert_awaited_once()
    assert coordinator._connected is False


async def test_set_setpoint_auth_error_keeps_session(
    hass: HomeAssistant, mock_controller: MagicMock
) -> None:
    mock_controller.set_setpoint = AsyncMock(side_effect=ComApAuthError("password required"))
    coordinator = _coordinator(hass, mock_controller)
    coordinator._connected = True

    with pytest.raises(HomeAssistantError, match="password required"):
        await coordinator.async_set_setpoint(1, 5)
    mock_controller.close.assert_not_awaited()
    assert coordinator._connected is True


async def test_execute_command_timeout_closes_session(
    hass: HomeAssistant,
    mock_controller: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(coordinator_module, "WRITE_TIMEOUT", 0.05)
    hang = asyncio.Event()

    async def never_returns(_: object) -> int:
        await hang.wait()
        return 0

    mock_controller.execute_command = AsyncMock(side_effect=never_returns)
    coordinator = _coordinator(hass, mock_controller)
    coordinator._connected = True

    with pytest.raises(HomeAssistantError):
        await coordinator.async_execute_command(Command.FAULT_RESET)
    mock_controller.close.assert_awaited_once()
    assert coordinator._connected is False


# -- access code: hand over to reauth instead of retrying -----------------------------------------


async def test_invalid_access_code_raises_config_entry_auth_failed(
    hass: HomeAssistant, mock_controller: MagicMock
) -> None:
    mock_controller.connect.side_effect = ComApInvalidAccessCodeError("access code rejected")
    coordinator = _coordinator(hass, mock_controller)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
    mock_controller.close.assert_awaited_once()
    assert coordinator._connected is False


# -- write password: latch after the first rejection ----------------------------------------------


def _password_setpoints(ctrl: MagicMock, *, needs_password: bool) -> None:
    ctrl.setpoint_info.return_value.needs_password = needs_password


def _issue(hass: HomeAssistant) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, "password_rejected_entry1")


async def test_rejected_password_latches_and_is_never_sent_again(
    hass: HomeAssistant, mock_controller: MagicMock
) -> None:
    _password_setpoints(mock_controller, needs_password=True)
    mock_controller.set_setpoint = AsyncMock(side_effect=ComApInvalidPasswordError("wrong"))
    coordinator = _coordinator(hass, mock_controller)

    with pytest.raises(HomeAssistantError, match="write password"):
        await coordinator.async_set_setpoint(1, 5)
    assert mock_controller.set_setpoint.await_count == 1
    assert _issue(hass) is not None

    # Further attempts (button, automation, ...) must not reach the controller.
    for _ in range(3):
        with pytest.raises(HomeAssistantError, match="write password"):
            await coordinator.async_set_setpoint(1, 5)
    assert mock_controller.set_setpoint.await_count == 1


async def test_rejected_password_does_not_block_writes_that_need_no_password(
    hass: HomeAssistant, mock_controller: MagicMock
) -> None:
    coordinator = _coordinator(hass, mock_controller)
    coordinator._password_rejected = True
    _password_setpoints(mock_controller, needs_password=False)
    mock_controller.set_setpoint = AsyncMock()

    await coordinator.async_set_setpoint(2, 7)

    mock_controller.set_setpoint.assert_awaited_once_with(2, 7)


async def test_rejected_password_does_not_block_commands(
    hass: HomeAssistant, mock_controller: MagicMock
) -> None:
    coordinator = _coordinator(hass, mock_controller)
    coordinator._password_rejected = True
    mock_controller.execute_command = AsyncMock(return_value=0)

    await coordinator.async_execute_command(Command.FAULT_RESET)

    mock_controller.execute_command.assert_awaited_once()


async def test_missing_password_is_not_a_rejection(
    hass: HomeAssistant, mock_controller: MagicMock
) -> None:
    """'No password configured' never reaches the controller, so it must not latch."""
    _password_setpoints(mock_controller, needs_password=True)
    mock_controller.set_setpoint = AsyncMock(side_effect=ComApAuthError("no password supplied"))
    coordinator = _coordinator(hass, mock_controller)

    with pytest.raises(HomeAssistantError, match="no password supplied"):
        await coordinator.async_set_setpoint(1, 5)
    assert coordinator._password_rejected is False
    assert _issue(hass) is None


async def test_new_coordinator_clears_password_issue(
    hass: HomeAssistant, mock_controller: MagicMock
) -> None:
    """A reload (e.g. after the password was changed in options) starts with a clean slate."""
    _password_setpoints(mock_controller, needs_password=True)
    mock_controller.set_setpoint = AsyncMock(side_effect=ComApInvalidPasswordError("wrong"))
    coordinator = _coordinator(hass, mock_controller)
    with pytest.raises(HomeAssistantError):
        await coordinator.async_set_setpoint(1, 5)
    assert _issue(hass) is not None

    reloaded = _coordinator(hass, mock_controller)

    assert reloaded._password_rejected is False
    assert _issue(hass) is None
