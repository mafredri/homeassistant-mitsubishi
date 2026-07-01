"""Data update coordinator for the Mitsubishi Air Conditioner integration."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pymitsubishi import MitsubishiController, ParsedDeviceState

from .const import (
    CONF_EXPERIMENTAL_FEATURES,
    CONF_EXTERNAL_TEMP_ENTITY,
    CONF_REMOTE_TEMP_MODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Extra time beyond one poll interval for the delayed refresh and scheduler jitter.
COMMAND_CONFIRMATION_BUFFER = 10
REQUEST_REFRESH_COOLDOWN = 1


class MitsubishiDataUpdateCoordinator(DataUpdateCoordinator[ParsedDeviceState]):
    """Class to manage fetching data from the Mitsubishi Air Conditioner."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        controller: MitsubishiController,
        config_entry: ConfigEntry,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=timedelta(seconds=scan_interval),
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=REQUEST_REFRESH_COOLDOWN,
                immediate=True,
                function=None,
            ),
        )
        self.controller = controller
        self.unit_info = None  # Will be populated on first update or by config flow
        # Load persisted remote temperature mode (AC doesn't report it, so we track it)
        self._remote_temp_mode: bool = config_entry.options.get(CONF_REMOTE_TEMP_MODE, False)
        self._startup_mode_applied = False

        # Log the loaded state for debugging persistence issues
        _LOGGER.info(
            "Coordinator for `%s` initialized: remote_temp_mode=%s (from options: %s)",
            self.config_entry.title,
            self._remote_temp_mode,
            dict(config_entry.options),
        )
        self._command_lock = asyncio.Lock()
        self._pending_general: dict[str, Any] = {}
        self._pending_expires_at: float | None = None
        # Keep optimistic state through the delayed refresh plus one configured
        # polling interval before treating a stale value as a rejection.
        self._command_confirmation_timeout = scan_interval + COMMAND_CONFIRMATION_BUFFER
        self._delayed_refresh_task: asyncio.Task[None] | None = None

    async def set_remote_temp_mode(self, enabled: bool) -> None:
        """Set whether remote temperature mode is enabled and persist to storage.

        When disabling remote mode, this also tells the AC to use its internal sensor.
        """
        self._remote_temp_mode = enabled
        _LOGGER.info("[%s] Remote temperature mode set to: %s", self.config_entry.title, enabled)

        # When disabling remote mode, tell the AC to use internal sensor
        if not enabled:
            await self.async_set_current_temperature(None)

        # Persist to config entry options
        new_options = dict(self.config_entry.options)
        new_options[CONF_REMOTE_TEMP_MODE] = enabled
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options=new_options,
        )

    @property
    def remote_temp_mode(self) -> bool:
        """Return whether remote temperature mode is enabled."""
        return self._remote_temp_mode

    @property
    def experimental_features_enabled(self) -> bool:
        """Return whether experimental features are enabled."""
        return self.config_entry.options.get(CONF_EXPERIMENTAL_FEATURES, False)

    async def get_unit_info(self) -> dict:
        """Fetch unit information once for device info."""
        unit_info = await self.hass.async_add_executor_job(self.controller.get_unit_info)
        return unit_info

    async def _async_update_data(self) -> ParsedDeviceState:
        """Update data via library."""
        _LOGGER.debug("[%s] Coordinator fetching device status", self.config_entry.title)

        # Only process remote temperature if experimental features are enabled
        if self.experimental_features_enabled:
            # On first update after startup, restore the persisted mode to the AC
            if not self._startup_mode_applied:
                self._startup_mode_applied = True
                if self._remote_temp_mode:
                    _LOGGER.info(
                        "[%s] Restoring remote temperature mode from persisted state",
                        self.config_entry.title,
                    )
                    await self._send_remote_temperature()
                else:
                    _LOGGER.debug(
                        "[%s] Starting with internal temperature mode", self.config_entry.title
                    )
            elif self._remote_temp_mode:
                # Regular update - send remote temperature if enabled
                await self._send_remote_temperature()
        else:
            self._startup_mode_applied = True

        # Fetch status from device
        state = await self.hass.async_add_executor_job(
            self.controller.fetch_status,
        )
        state = self._apply_pending_general(state)
        self.controller.state = state
        return state

    @property
    def command_lock(self) -> asyncio.Lock:
        """Return the command serialization lock."""
        return self._command_lock

    def async_apply_command_result(
        self,
        state: ParsedDeviceState | None,
        optimistic_fields: dict[str, Any] | None = None,
    ) -> None:
        """Publish command result with optional optimistic general-state fields."""
        if state is None:
            state = self.data

        if optimistic_fields:
            state = self._state_with_general_fields(state, optimistic_fields)
            self.controller.state = state
            self._set_pending_general(optimistic_fields)

        self.async_set_updated_data(state)
        self._schedule_delayed_refresh()

    async def async_command_failed(self, optimistic_fields: dict[str, Any] | None = None) -> None:
        """Clear pending command state and fetch current device state."""
        self._clear_pending_general(optimistic_fields)
        await self.async_request_refresh()

    def _state_with_general_fields(
        self, state: ParsedDeviceState, fields: dict[str, Any]
    ) -> ParsedDeviceState:
        """Return a copy of state with general-state fields overridden."""
        new_state = dataclasses.replace(state)
        if state.general is None:
            return new_state

        general = dataclasses.replace(state.general)
        for key, value in fields.items():
            setattr(general, key, value)
        new_state.general = general
        return new_state

    def _set_pending_general(self, fields: dict[str, Any]) -> None:
        """Track general-state fields that still need device confirmation."""
        self._pending_general.update(fields)
        self._pending_expires_at = self.hass.loop.time() + self._command_confirmation_timeout

    def _clear_pending_general(self, fields: dict[str, Any] | None = None) -> None:
        """Clear pending confirmation fields."""
        if fields is None:
            self._pending_general.clear()
        else:
            for key in fields:
                self._pending_general.pop(key, None)

        if not self._pending_general:
            self._pending_expires_at = None

    def _apply_pending_general(self, state: ParsedDeviceState) -> ParsedDeviceState:
        """Keep expected command values visible until the device confirms them."""
        if not self._pending_general or state.general is None:
            return state

        confirmed = [
            key
            for key, expected_value in self._pending_general.items()
            if getattr(state.general, key) == expected_value
        ]
        self._clear_pending_general(dict.fromkeys(confirmed))

        if not self._pending_general:
            return state

        if (
            self._pending_expires_at is not None
            and self.hass.loop.time() >= self._pending_expires_at
        ):
            _LOGGER.warning(
                "[%s] Device did not confirm command state before timeout: %s",
                self.config_entry.title,
                sorted(self._pending_general),
            )
            self._clear_pending_general()
            return state

        return self._state_with_general_fields(state, self._pending_general)

    def _schedule_delayed_refresh(self) -> None:
        """Refresh after the device has had time to publish command effects."""
        if self._delayed_refresh_task and not self._delayed_refresh_task.done():
            self._delayed_refresh_task.cancel()

        self._delayed_refresh_task = self.config_entry.async_create_background_task(
            self.hass,
            self._async_delayed_refresh(),
            name=f"{DOMAIN} delayed command refresh",
        )

    async def _async_delayed_refresh(self) -> None:
        """Refresh after command processing delay without blocking the service call."""
        try:
            await asyncio.sleep(self.controller.wait_time_after_command)
            await self.async_request_refresh()
        except asyncio.CancelledError:
            return

    async def _send_remote_temperature(self) -> None:
        """Send remote temperature to AC if configured and available.

        If the external temperature entity is unavailable, the AC is temporarily
        switched to use its internal sensor, but remote mode remains enabled so
        we automatically resume sending remote temperatures when the entity
        becomes available again.
        """
        external_entity_id = self.config_entry.options.get(CONF_EXTERNAL_TEMP_ENTITY)

        if not external_entity_id:
            _LOGGER.warning(
                "[%s] Remote temperature mode enabled but no external entity configured, "
                "falling back to internal sensor",
                self.config_entry.title,
            )
            await self.set_remote_temp_mode(False)
            return

        state = self.hass.states.get(external_entity_id)

        if state is None:
            _LOGGER.warning(
                "[%s External temperature entity %s not found, "
                "temporarily using internal sensor (will retry)",
                self.config_entry.title,
                external_entity_id,
            )
            # Tell AC to use internal sensor temporarily, but don't disable remote mode
            # so we automatically resume when the entity becomes available
            await self.async_set_current_temperature(None)
            return

        if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            _LOGGER.warning(
                "[%s] External temperature entity %s is %s, "
                "temporarily using internal sensor (will retry)",
                self.config_entry.title,
                external_entity_id,
                state.state,
            )
            # Tell AC to use internal sensor temporarily, but don't disable remote mode
            # so we automatically resume when the entity becomes available
            await self.async_set_current_temperature(None)
            return

        try:
            temperature = float(state.state)
            _LOGGER.debug(
                "[%s] Sending remote temperature %.1f from %s to AC",
                self.config_entry.title,
                temperature,
                external_entity_id,
            )
            await self.async_set_current_temperature(temperature)
        except (ValueError, TypeError) as e:
            _LOGGER.error(
                "[%s] Invalid temperature value '%s' from %s: %s, "
                "temporarily using internal sensor (will retry)",
                self.config_entry.title,
                state.state,
                external_entity_id,
                e,
            )
            # Tell AC to use internal sensor temporarily, but don't disable remote mode
            # Invalid values might be transient (e.g., during sensor reconfiguration)
            await self.async_set_current_temperature(None)

    async def async_set_current_temperature(self, temperature: float | None) -> None:
        """Serialize remote temperature commands with entity commands."""
        async with self._command_lock:
            await self.hass.async_add_executor_job(
                self.controller.set_current_temperature, temperature
            )
