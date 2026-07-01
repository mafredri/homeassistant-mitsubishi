"""Tests for the MobileEntity class."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry

from custom_components.mitsubishi.const import DOMAIN
from custom_components.mitsubishi.entity import MitsubishiEntity
from tests import TEST_SYSTEM_DATA


@pytest.mark.asyncio
async def test_mitsubishi_entity_initialization(hass):
    """Test the initialization of MitsubishiEntity."""
    # Setup mock data
    config_data = {"host": "192.168.1.100"}

    mock_config_entry = MagicMock(spec=ConfigEntry)
    mock_config_entry.data = config_data

    mock_coordinator = MagicMock()
    mock_coordinator.data = TEST_SYSTEM_DATA

    # Create the entity
    entity = MitsubishiEntity(mock_coordinator, mock_config_entry, "test_key")

    # Assert properties are set
    assert entity._key == "test_key"

    # Check device info attributes
    assert entity.device_info["identifiers"] == {(DOMAIN, "00:11:22:33:44:55")}
    assert entity.device_info["manufacturer"] == "Mitsubishi Electric"
    assert entity.device_info["name"] == "Mitsubishi AC 33:44:55"
    assert entity.device_info["hw_version"] == "00:11:22:33:44:55"
    assert entity.device_info["serial_number"] == "TEST123456"

    # Check unique ID
    assert entity.unique_id == "00:11:22:33:44:55_test_key"


@pytest.mark.asyncio
async def test_mitsubishi_entity_availability(hass):
    """Test the availability of MitsubishiEntity."""
    # Setup mock data
    config_data = {"host": "192.168.1.100"}

    mock_config_entry = MagicMock(spec=ConfigEntry)
    mock_config_entry.data = config_data

    mock_coordinator = MagicMock()
    mock_coordinator.data = TEST_SYSTEM_DATA
    mock_coordinator.last_update_success = True

    # Create the entity
    entity = MitsubishiEntity(mock_coordinator, mock_config_entry, "test_key")

    # Check availability
    assert entity.available is True

    # Simulate update failure
    mock_coordinator.last_update_success = False

    # Check availability after failure
    assert entity.available is False


@pytest.mark.asyncio
async def test_mitsubishi_entity_initialization_with_none_data(hass):
    """Test entity initialization when coordinator data is None."""
    # Setup mock data
    config_data = {"host": "192.168.1.100"}

    mock_config_entry = MagicMock(spec=ConfigEntry)
    mock_config_entry.data = config_data

    mock_coordinator = MagicMock()
    mock_coordinator.data = None  # Simulate initial state
    mock_coordinator.last_update_success = False

    # Should not raise an exception
    entity = MitsubishiEntity(mock_coordinator, mock_config_entry, "test_key")

    # Check that entity was created successfully
    assert entity._key == "test_key"

    # Should have device info based on host fallback
    assert entity.device_info["identifiers"] == {(DOMAIN, "192.168.1.100")}
    assert entity.device_info["manufacturer"] == "Mitsubishi Electric"
    assert entity.device_info["name"] == "Mitsubishi AC 68.1.100"  # Last 8 chars of IP
    assert entity.device_info["hw_version"] == "192.168.1.100"
    assert entity.device_info["serial_number"] is None

    # Check unique ID
    assert entity.unique_id == "192.168.1.100_test_key"

    # Check availability
    assert entity.available is False


@pytest.mark.asyncio
async def test_execute_command_publishes_result_while_lock_is_held(
    hass, mock_config_entry, tracking_async_lock
):
    """Test command result publication is serialized with command execution."""
    lock = tracking_async_lock
    mock_coordinator = MagicMock()
    mock_coordinator.data = TEST_SYSTEM_DATA
    mock_coordinator.command_lock = lock
    mock_coordinator.last_update_success = True
    mock_coordinator.async_command_failed = AsyncMock()

    def apply_command_result(state, optimistic_fields=None):
        assert lock.locked is True

    mock_coordinator.async_apply_command_result = MagicMock(side_effect=apply_command_result)

    entity = MitsubishiEntity(mock_coordinator, mock_config_entry, "test_key")
    entity.hass = hass

    with patch.object(hass, "async_add_executor_job", new=AsyncMock(return_value=TEST_SYSTEM_DATA)):
        result = await entity._execute_command_with_refresh(
            "test command",
            MagicMock(),
            optimistic_fields={"temperature": 21.0},
        )

    assert result is True
    mock_coordinator.async_apply_command_result.assert_called_once_with(
        TEST_SYSTEM_DATA, {"temperature": 21.0}
    )


@pytest.mark.asyncio
async def test_execute_command_refreshes_failure_after_releasing_lock(
    hass, mock_config_entry, tracking_async_lock
):
    """Test failed command refreshes after command serialization is released."""
    lock = tracking_async_lock
    mock_coordinator = MagicMock()
    mock_coordinator.data = TEST_SYSTEM_DATA
    mock_coordinator.command_lock = lock
    mock_coordinator.last_update_success = True

    async def async_command_failed(optimistic_fields=None):
        assert lock.locked is False

    mock_coordinator.async_command_failed = AsyncMock(side_effect=async_command_failed)
    mock_coordinator.async_apply_command_result = MagicMock()

    entity = MitsubishiEntity(mock_coordinator, mock_config_entry, "test_key")
    entity.hass = hass

    with patch.object(hass, "async_add_executor_job", new=AsyncMock(return_value=None)):
        result = await entity._execute_command_with_refresh(
            "test command",
            MagicMock(),
            optimistic_fields={"temperature": 21.0},
        )

    assert result is False
    mock_coordinator.async_command_failed.assert_awaited_once_with({"temperature": 21.0})
    mock_coordinator.async_apply_command_result.assert_not_called()
