"""Tests for ComApCoordinator."""

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

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
