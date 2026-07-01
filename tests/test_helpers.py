"""Tests for the Mitsubishi Helper functions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mitsubishi import async_setup_entry, async_unload_entry
from custom_components.mitsubishi.const import DOMAIN
from custom_components.mitsubishi.device_info import migrate_device_registry_entry


def mock_empty_device_registry():
    """Return a device registry mock with no existing devices."""
    mock_registry = MagicMock()
    mock_registry.async_get_device.return_value = None
    mock_registry.async_get_or_create = MagicMock(return_value=MagicMock())
    return mock_registry


@pytest.mark.asyncio
async def test_async_setup_entry(hass, mock_config_entry, mock_coordinator):
    """Test setting up Mitsubishi integration via config entry."""
    with (
        patch("custom_components.mitsubishi.MitsubishiController") as mock_controller_class,
        patch(
            "custom_components.mitsubishi.MitsubishiDataUpdateCoordinator",
            return_value=mock_coordinator,
        ),
        patch("custom_components.mitsubishi.dr.async_get") as mock_device_registry,
        patch.object(hass.config_entries, "async_forward_entry_setups", return_value=None),
    ):
        mock_controller = MagicMock()
        mock_controller.fetch_status = MagicMock(return_value=True)
        mock_controller_class.return_value = mock_controller

        mock_coordinator.get_unit_info = AsyncMock(return_value={})

        # Mock device registry
        mock_registry = mock_empty_device_registry()
        mock_device_registry.return_value = mock_registry

        assert await async_setup_entry(hass, mock_config_entry) is True

        # Check that device info fetching occurs
        mock_coordinator.get_unit_info.assert_called_once()

        # Check that the refresh is attempted
        mock_coordinator.async_config_entry_first_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_unload_entry(hass, mock_config_entry, mock_coordinator):
    """Test unloading of config entry."""
    # Set up the data as if the integration was already loaded
    hass.data = {"mitsubishi": {mock_config_entry.entry_id: mock_coordinator}}
    # Remote temp mode disabled - should not try to switch to internal
    mock_coordinator.remote_temp_mode = False

    # Mock the async_unload_platforms method to return True
    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=True
    ) as mock_unload:
        result = await async_unload_entry(hass, mock_config_entry)

        assert result is True
        mock_unload.assert_awaited_once()
        mock_coordinator.controller.api.close.assert_called_once()
        # set_current_temperature should NOT be called when remote mode is disabled
        mock_coordinator.controller.set_current_temperature.assert_not_called()
        assert mock_config_entry.entry_id not in hass.data["mitsubishi"]


@pytest.mark.asyncio
async def test_async_unload_entry_with_remote_temp_mode(hass, mock_config_entry, mock_coordinator):
    """Test unloading switches AC to internal sensor when remote temp mode is enabled."""
    # Set up the data as if the integration was already loaded
    hass.data = {"mitsubishi": {mock_config_entry.entry_id: mock_coordinator}}
    # Remote temp mode enabled - should switch to internal on unload
    mock_coordinator.remote_temp_mode = True

    # Mock the async_unload_platforms method to return True
    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=True
    ) as mock_unload:
        result = await async_unload_entry(hass, mock_config_entry)

        assert result is True
        mock_unload.assert_awaited_once()
        # Should call set_current_temperature(None) to switch to internal
        mock_coordinator.controller.set_current_temperature.assert_called_once_with(None)
        mock_coordinator.controller.api.close.assert_called_once()
        assert mock_config_entry.entry_id not in hass.data["mitsubishi"]


@pytest.mark.asyncio
async def test_async_unload_entry_remote_temp_switch_fails(
    hass, mock_config_entry, mock_coordinator
):
    """Test unloading continues even if switching to internal sensor fails."""
    # Set up the data as if the integration was already loaded
    hass.data = {"mitsubishi": {mock_config_entry.entry_id: mock_coordinator}}
    # Remote temp mode enabled
    mock_coordinator.remote_temp_mode = True
    # But switching to internal fails
    mock_coordinator.controller.set_current_temperature.side_effect = Exception("Connection failed")

    # Mock the async_unload_platforms method to return True
    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=True
    ) as mock_unload:
        result = await async_unload_entry(hass, mock_config_entry)

        # Should still return True and close the API
        assert result is True
        mock_unload.assert_awaited_once()
        mock_coordinator.controller.set_current_temperature.assert_called_once_with(None)
        mock_coordinator.controller.api.close.assert_called_once()
        assert mock_config_entry.entry_id not in hass.data["mitsubishi"]


@pytest.mark.asyncio
async def test_async_setup_entry_with_comprehensive_unit_info(
    hass, mock_config_entry, mock_coordinator
):
    """Test setup with comprehensive unit info to cover sw_versions and hw_info branches."""

    with (
        patch("custom_components.mitsubishi.MitsubishiController") as mock_controller_class,
        patch(
            "custom_components.mitsubishi.MitsubishiDataUpdateCoordinator",
            return_value=mock_coordinator,
        ),
        patch("custom_components.mitsubishi.dr.async_get") as mock_device_registry,
        patch.object(hass.config_entries, "async_forward_entry_setups", return_value=None),
    ):
        mock_controller = MagicMock()
        mock_controller.fetch_status = MagicMock(return_value=True)
        mock_controller_class.return_value = mock_controller

        # Mock device registry
        mock_registry = mock_empty_device_registry()
        mock_device_registry.return_value = mock_registry

        result = await async_setup_entry(hass, mock_config_entry)

        assert result is True

        # Verify device registry was called with comprehensive info
        mock_registry.async_get_or_create.assert_called_once()
        call_kwargs = mock_registry.async_get_or_create.call_args[1]

        # Check that sw_version contains all the version components
        sw_version = call_kwargs["sw_version"]
        assert "App: 33.00" in sw_version
        assert "Rel: 00.06" in sw_version
        assert "CP: 01.08" in sw_version
        assert call_kwargs["identifiers"] == {(DOMAIN, "1234567890")}
        assert call_kwargs["connections"] == {(dr.CONNECTION_NETWORK_MAC, "00:11:22:33:44:55")}
        assert "hw_version" not in call_kwargs


@pytest.mark.asyncio
async def test_async_setup_entry_without_unit_info(hass, mock_config_entry, mock_coordinator):
    """Test setup with failing unit info"""

    with (
        patch("custom_components.mitsubishi.MitsubishiController") as mock_controller_class,
        patch(
            "custom_components.mitsubishi.MitsubishiDataUpdateCoordinator",
            return_value=mock_coordinator,
        ),
        patch("custom_components.mitsubishi.dr.async_get") as mock_device_registry,
        patch.object(hass.config_entries, "async_forward_entry_setups", return_value=None),
    ):
        mock_controller = MagicMock()
        mock_controller.fetch_status = MagicMock(return_value=True)
        mock_controller_class.return_value = mock_controller

        # Mock device registry
        mock_registry = mock_empty_device_registry()
        mock_device_registry.return_value = mock_registry

        mock_coordinator.get_unit_info = MagicMock(
            side_effect=requests.exceptions.HTTPError("404 Client Error: Not Found for url")
        )

        result = await async_setup_entry(hass, mock_config_entry)

        assert result is True

        # Verify device registry was called with comprehensive info
        mock_registry.async_get_or_create.assert_called_once()


@pytest.mark.asyncio
async def test_async_setup_entry_rekeys_old_mac_identifier(
    hass, mock_config_entry, mock_coordinator
):
    """Test setup rekeys old MAC identifier devices to serial plus MAC connection."""
    mock_config_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    old_device = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "00:11:22:33:44:55")},
        hw_version="00:11:22:33:44:55",
    )

    with (
        patch("custom_components.mitsubishi.MitsubishiController") as mock_controller_class,
        patch(
            "custom_components.mitsubishi.MitsubishiDataUpdateCoordinator",
            return_value=mock_coordinator,
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", return_value=None),
    ):
        mock_controller = MagicMock()
        mock_controller.fetch_status = MagicMock(return_value=True)
        mock_controller_class.return_value = mock_controller

        assert await async_setup_entry(hass, mock_config_entry) is True

    migrated_device = device_registry.async_get(old_device.id)
    assert migrated_device is not None
    assert migrated_device.identifiers == {(DOMAIN, "1234567890")}
    assert migrated_device.connections == {(dr.CONNECTION_NETWORK_MAC, "00:11:22:33:44:55")}
    assert migrated_device.hw_version is None
    assert migrated_device.serial_number == "1234567890"
    assert mock_config_entry.entry_id in migrated_device.config_entries


def test_migrate_device_registry_entry_moves_config_to_existing_mac_device(hass, mock_config_entry):
    """Test migration joins an existing network device with the same MAC."""
    mock_config_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    old_device = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "00:11:22:33:44:55")},
        hw_version="00:11:22:33:44:55",
    )
    shared_config_entry = MockConfigEntry(
        domain="unifi",
        title="UniFi Network",
        entry_id="unifi_entry_id",
    )
    shared_config_entry.add_to_hass(hass)
    shared_device = device_registry.async_get_or_create(
        config_entry_id=shared_config_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "00:11:22:33:44:55")},
        manufacturer="Ubiquiti",
    )

    migrate_device_registry_entry(
        device_registry,
        config_entry_id=mock_config_entry.entry_id,
        host="192.168.1.100",
        device_mac="00:11:22:33:44:55",
        device_serial="1234567890",
        device_model="MAC-577IF-E",
        sw_version="App: 33.00",
    )

    migrated_shared_device = device_registry.async_get(shared_device.id)
    migrated_old_device = device_registry.async_get(old_device.id)
    assert migrated_shared_device is not None
    assert mock_config_entry.entry_id in migrated_shared_device.config_entries
    assert migrated_old_device is None
    assert (DOMAIN, "1234567890") in migrated_shared_device.identifiers
    assert (dr.CONNECTION_NETWORK_MAC, "00:11:22:33:44:55") in migrated_shared_device.connections
    assert migrated_shared_device.hw_version is None
    assert migrated_shared_device.serial_number == "1234567890"


@pytest.mark.asyncio
async def test_async_setup_entry_connection_failure(hass, mock_config_entry):
    """Test setup when connection to device fails."""
    from homeassistant.exceptions import ConfigEntryNotReady

    with (
        patch("custom_components.mitsubishi.MitsubishiController") as mock_controller_class,
    ):
        mock_controller_class.side_effect = requests.exceptions.RequestException("foobar")

        # Should raise ConfigEntryNotReady when connection fails
        with pytest.raises(ConfigEntryNotReady, match="foobar"):
            await async_setup_entry(hass, mock_config_entry)
