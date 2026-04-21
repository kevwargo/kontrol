#!/usr/bin/env python3

import asyncio
import json
import sys
from tempfile import NamedTemporaryFile
from uuid import uuid4

from dbus_next import BusType
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method

NAME = "org.kevwargo.kwinctl.inspect"

SCRIPT = """let value;
try {
  value = %s;
  if (typeof value !== "string") {
    value = JSON.stringify(value);
    if (typeof value === "undefined") value = "";
  }
} catch (e) {
  value = `${e}`;
}
"""


class Eval(ServiceInterface):
    def __init__(self):
        super().__init__(NAME)
        self.bus: MessageBus | None = None
        self.id_ = str(uuid4())
        self._stop_event = asyncio.Event()

    async def main(self):
        self.bus = await MessageBus(bus_type=BusType.SESSION).connect()
        self.bus.export("/", self)
        await self.bus.request_name(NAME)

        try:
            await self._run()
        finally:
            await self.bus.release_name(NAME)

    async def _run(self):
        with NamedTemporaryFile(mode="w+", prefix="kwin-eval-", suffix=".js") as f:
            expr = sys.argv[1]
            if ";" in expr:
                expr = "(() => { %s%s return r; })()" % (expr, "" if expr.endswith(";") else ";")

            body = (SCRIPT % expr) + f'callDBus("{NAME}", "/", "{NAME}", "Return", "{self.id_}", value);'

            f.write(body)
            f.flush()

            scripting = await self._get_iface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting")
            script_id = await scripting.call_load_script(f.name)
            script = await self._get_iface("org.kde.KWin", f"/Scripting/Script{script_id}", "org.kde.kwin.Script")
            await script.call_run()
            await self._stop_event.wait()
            await script.call_stop()

    async def _get_iface(self, service: str, path: str, interface: str):
        introspection = await self.bus.introspect(service, path)
        obj = self.bus.get_proxy_object(service, path, introspection)
        return obj.get_interface(interface)

    @method()
    def Return(self, id_: "s", value: "s"):  # noqa:F821
        if id_ != self.id_:
            return

        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass

        if not isinstance(value, str):
            value = json.dumps(value, indent=2)

        print(value)
        self._stop_event.set()


if __name__ == "__main__":
    asyncio.run(Eval().main())
