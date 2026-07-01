"""Device registry helpers for Mitsubishi Air Conditioner integration."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN

MANUFACTURER = "Mitsubishi Electric"


def device_identifiers(
    *,
    host: str,
    device_serial: str | None,
) -> set[tuple[str, str]]:
    """Return the integration-owned device identifier."""
    return {(DOMAIN, device_serial or host)}


def device_connections(device_mac: str | None) -> set[tuple[str, str]]:
    """Return shared device connections."""
    if not device_mac:
        return set()
    return {(dr.CONNECTION_NETWORK_MAC, device_mac)}


def device_name(*, host: str, device_mac: str | None) -> str:
    """Return the default device name."""
    if device_mac:
        return f"Mitsubishi AC {device_mac[-8:]}"
    return f"Mitsubishi AC ({host})"


def build_device_info(
    *,
    host: str,
    device_mac: str | None,
    device_serial: str | None,
    device_model: str | None = None,
    sw_version: str | None = None,
    suggested_area: str | None = None,
) -> DeviceInfo:
    """Build Home Assistant device registry info."""
    device_info = DeviceInfo(
        identifiers=device_identifiers(host=host, device_serial=device_serial),
        manufacturer=MANUFACTURER,
        name=device_name(host=host, device_mac=device_mac),
        serial_number=device_serial,
        configuration_url=f"http://{host}",
    )

    if connections := device_connections(device_mac):
        device_info["connections"] = connections
    if device_model is not None:
        device_info["model"] = device_model
    if sw_version is not None:
        device_info["sw_version"] = sw_version
    if suggested_area is not None:
        device_info["suggested_area"] = suggested_area

    return device_info


def migrate_device_registry_entry(
    device_registry: dr.DeviceRegistry,
    *,
    config_entry_id: str,
    host: str,
    device_mac: str | None,
    device_serial: str | None,
    device_model: str | None,
    sw_version: str | None,
) -> None:
    """Migrate old MAC-identifier devices to shared MAC connections."""
    if not device_mac:
        return

    old_device = device_registry.async_get_device(identifiers={(DOMAIN, device_mac)})
    if old_device is None:
        return

    connections = device_connections(device_mac)
    identifiers = device_identifiers(host=host, device_serial=device_serial)
    shared_device = device_registry.async_get_device(connections=connections)
    configuration_url = f"http://{host}"
    name = device_name(host=host, device_mac=device_mac)

    if shared_device is not None and shared_device.id != old_device.id:
        device_registry.async_update_device(
            shared_device.id,
            add_config_entry_id=config_entry_id,
            merge_identifiers=identifiers,
            merge_connections=connections,
            configuration_url=configuration_url,
            hw_version=None,
            manufacturer=MANUFACTURER,
            model=device_model,
            name=name,
            serial_number=device_serial,
            sw_version=sw_version,
        )
        if config_entry_id in old_device.config_entries:
            device_registry.async_update_device(
                old_device.id,
                remove_config_entry_id=config_entry_id,
            )
        return

    device_registry.async_update_device(
        old_device.id,
        new_identifiers=identifiers,
        merge_connections=connections,
        configuration_url=configuration_url,
        hw_version=None,
        manufacturer=MANUFACTURER,
        model=device_model,
        name=name,
        serial_number=device_serial,
        sw_version=sw_version,
    )
