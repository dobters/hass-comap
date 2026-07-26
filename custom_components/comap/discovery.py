from ipaddress import IPv4Interface, ip_interface
from typing import TYPE_CHECKING

from homeassistant.components import network
from pycomap.discovery import DiscoveryDevice
from pycomap.discovery import discover as _discover_devices

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


__all__ = ["discover"]


async def discover(hass: HomeAssistant) -> list[DiscoveryDevice]:
    adapters = await network.async_get_adapters(hass)
    interfaces: list[IPv4Interface] = []
    for adapter in adapters:
        if not adapter["enabled"]:
            continue
        for ip_info in adapter["ipv4"]:
            interface = ip_interface(f"{ip_info['address']}/{ip_info['network_prefix']}")
            if isinstance(interface, IPv4Interface):
                interfaces.append(interface)

    return await _discover_devices(*interfaces)
