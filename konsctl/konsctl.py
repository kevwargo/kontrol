#!/usr/bin/env python3

import asyncio
import sys

from dbus_next import BusType
from dbus_next.aio import MessageBus, ProxyInterface

SERVICE = "org.kde.konsole"


async def main():
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    try:
        func = {
            "set-profile": set_profile,
        }[sys.argv[1]]
        await func(bus, *sys.argv[2:])
    finally:
        bus.disconnect()


async def set_profile(bus: MessageBus, profile: str):
    for s in await get_objects(bus, "/Sessions"):
        await s.call_set_profile(profile)
    for w in await get_objects(bus, "/Windows"):
        await w.call_set_default_profile(profile)
        break


async def get_objects(bus: MessageBus, path: str) -> list[ProxyInterface]:
    objects = []

    base = await bus.introspect(SERVICE, path)
    for n in base.nodes:
        args = (SERVICE, f"{path}/{n.name}")
        intro = await bus.introspect(*args)
        for iface in intro.interfaces:
            if iface.name.startswith(SERVICE):
                objects.append(bus.get_proxy_object(*args, intro).get_interface(iface.name))
                break

    return objects


if __name__ == "__main__":
    asyncio.run(main())
