"""Pytest configuration for hass-comap integration tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.comap.coordinator import ComApData


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations for all tests."""


@pytest.fixture
def mock_comap_data() -> ComApData:
    """Return minimal ComApData for unit tests."""
    return ComApData(values={}, setpoints={}, alarms=[])


@pytest.fixture
def mock_controller() -> MagicMock:
    """Return a mocked pycomap Controller."""
    ctrl = MagicMock()
    ctrl.connect = AsyncMock()
    ctrl.close = AsyncMock()
    ctrl.read_values = AsyncMock(return_value={})
    ctrl.read_setpoints = AsyncMock(return_value={})
    ctrl.read_alarms = AsyncMock(return_value=[])
    ctrl.values = []
    ctrl.setpoints = []
    return ctrl
