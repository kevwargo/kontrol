#!/usr/bin/env python3

import asyncio
import sys

from dbus_next import BusType, Message
from dbus_next.aio import MessageBus, ProxyInterface
from dbus_next.errors import InvalidMemberNameError

SERVICE = "org.kde.konsole"


async def main():
    func = {
        "set-profile": set_profile,
        "dbus": traverse_dbus,
        "kdbus": kdbus,
    }[sys.argv[1]]

    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    try:
        await func(bus, *sys.argv[2:])
    finally:
        bus.disconnect()


async def set_profile(bus: MessageBus, profile: str):
    for s in await get_objects(bus, "/Sessions"):
        await s.call_set_profile(profile)
    for w in await get_objects(bus, "/Windows"):
        await w.call_set_default_profile(profile)
        break


async def traverse_dbus(bus: MessageBus, path="/", level=0):
    indent = "  " * level
    try:
        intro = await bus.introspect(SERVICE, path)
    except InvalidMemberNameError as e:
        print(f"{e} in {path}")
        return

    path_printed = False

    for iface in intro.interfaces:
        if iface.name.startswith("org.freedesktop"):
            continue

        if not path_printed:
            print(f"{indent}{path}")
            path_printed = True

        print(f"{indent} {iface.name}")
        for m in iface.methods:
            print(f"{indent}  {m.name}({m.in_signature})")

    if not path_printed:
        print(f"{indent}{path} :empty")

    for n in intro.nodes or []:
        await traverse_dbus(bus, f"{path.rstrip('/')}/{n.name}", level + 1)


async def kdbus(bus: MessageBus):
    msg = Message(
        destination="org.kde.konsole",
        path="/org/kde/konsole",
        interface="org.kde.KDBusService",
        member="CommandLine",
        signature="assa{sv}",
        body=[["konsole", "--new-tab"], "/home/kev/.emacs.d", {}],
    )
    res = await bus.call(msg)
    print(res)


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
