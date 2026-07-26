"""Button platform for the ComAp integration (controller commands)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from pycomap import Command
from pycomap.protocol import ControllerCommand

from .coordinator import ComApCoordinator
from .entity import ComApEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import ComApConfigEntry

# (command, translation_key, unique_key)
_COMMANDS: list[tuple[ControllerCommand, str]] = [
    (Command.FAULT_RESET, "fault_reset"),
    (Command.HORN_RESET, "horn_reset"),
    (Command.ENGINE_START, "engine_start"),
    (Command.ENGINE_STOP, "engine_stop"),
    (Command.GCB_ON, "gcb_on"),
    (Command.GCB_OFF, "gcb_off"),
    (Command.MCB_ON, "mcb_on"),
    (Command.MCB_OFF, "mcb_off"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComApConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ComAp button entities."""
    coordinator: ComApCoordinator = entry.runtime_data
    async_add_entities([ComApCommandButton(coordinator, cmd, key) for cmd, key in _COMMANDS])


class ComApCommandButton(ComApEntity, ButtonEntity):
    """Button that sends a named command to the controller."""

    def __init__(
        self,
        coordinator: ComApCoordinator,
        command: ControllerCommand,
        translation_key: str,
    ) -> None:
        super().__init__(coordinator, translation_key)
        self._command = command
        self._attr_translation_key = translation_key

    async def async_press(self) -> None:
        await self.coordinator.ctrl.execute_command(self._command)
        await self.coordinator.async_request_refresh()
