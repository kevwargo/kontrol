import asyncio
import json
import sys

from dbus_next import BusType
from dbus_next.aio import MessageBus
from PyQt6.QtGui import QKeySequence


def main(key: str):
    asyncio.run(_main(sys.argv[1]))


async def _main(key: str):
    qk = QKeySequence(key)
    if not qk.toString():
        raise ValueError(f"invalid key {key}")

    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    intro = await bus.introspect("org.kde.kglobalaccel", "/kglobalaccel")
    iface = bus.get_proxy_object("org.kde.kglobalaccel", "/kglobalaccel", intro).get_interface(
        "org.kde.KGlobalAccel"
    )
    res = await iface.call_global_shortcuts_by_key([[qk[0].toCombined(), 0, 0, 0]], [0])
    print(json.dumps(res, indent=2, ensure_ascii=False))
