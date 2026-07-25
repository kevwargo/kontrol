import asyncio
import json
import shutil
import subprocess
import sys

from dbus_next import BusType
from dbus_next.aio import MessageBus

from kontrol.utils.kbd import KeySequence, ShortcutInfo


def main():
    asyncio.run(_run(sys.argv[1]))


async def _run(key: str):
    if len(key) > 1 and key.isdecimal():
        qk = KeySequence(int(key))
    else:
        qk = KeySequence(key)

    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    intro = await bus.introspect("org.kde.kglobalaccel", "/kglobalaccel")
    iface = bus.get_proxy_object("org.kde.kglobalaccel", "/kglobalaccel", intro).get_interface(
        "org.kde.KGlobalAccel"
    )
    raw_res = await iface.call_global_shortcuts_by_key([qk.to_dbus()], [0])

    res = [
        {
            "action": f"{s.action_id}({s.action_name!r})",
            "component": f"{s.component_id}({s.component_name!r})",
            "active_keys": s.active_keys,
            "default_keys": s.default_keys,
        }
        for s in map(ShortcutInfo.from_list, raw_res)
    ]

    if sys.stdout.isatty() and (jq := shutil.which("jq")):
        subprocess.run([jq], input=json.dumps(res, default=str), text=True, check=True)
    else:
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
