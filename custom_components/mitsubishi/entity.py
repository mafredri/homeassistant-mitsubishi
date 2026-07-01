"""Base entity class for Mitsubishi Air Conditioner integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import MitsubishiDataUpdateCoordinator
from .device_info import build_device_info

_LOGGER = logging.getLogger(__name__)


class MitsubishiEntity(CoordinatorEntity[MitsubishiDataUpdateCoordinator]):
    """Base class for Mitsubishi entities."""

    _attr_has_entity_name = True
    config_entry: ConfigEntry

    def __init__(
        self,
        coordinator: MitsubishiDataUpdateCoordinator,
        config_entry: ConfigEntry,
        key: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._key = key

        if coordinator.data:
            device_mac = coordinator.data.mac
            device_serial = coordinator.data.serial
        else:
            device_mac = None
            device_serial = None

        # Set device info
        self._attr_device_info = build_device_info(
            host=config_entry.data["host"],
            device_mac=device_mac,
            device_serial=device_serial,
        )

        # Set unique ID
        self._attr_unique_id = f"{device_mac or device_serial or config_entry.data['host']}_{key}"

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success and self.coordinator.data is not None

    async def _execute_command_with_refresh(
        self,
        command_name: str,
        command_func,
        *args: Any,
        command_factory=None,
        optimistic_fields: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> bool:
        """Execute a device command and publish expected state on success."""
        try:
            _LOGGER.debug(
                "[%s] Executing command: %s",
                self.coordinator.config_entry.title,
                command_name,
            )
            command_failed = False
            async with self.coordinator.command_lock:
                if command_factory is not None:
                    command_func, args, kwargs = command_factory()
                state = await self.hass.async_add_executor_job(
                    lambda: command_func(*args, **kwargs)
                )

                if not state:
                    _LOGGER.warning(
                        "[%s] Failed to execute %s",
                        self.coordinator.config_entry.title,
                        command_name,
                    )
                    command_failed = True
                else:
                    self.coordinator.async_apply_command_result(state, optimistic_fields)

            if command_failed:
                await self.coordinator.async_command_failed(optimistic_fields)
                return False
            _LOGGER.debug(
                "[%s] Command completed: %s",
                self.coordinator.config_entry.title,
                command_name,
            )
            return True
        except Exception:
            _LOGGER.exception(
                "[%s] Error executing %s",
                self.coordinator.config_entry.title,
                command_name,
            )
            await self.coordinator.async_command_failed(optimistic_fields)
            return False
