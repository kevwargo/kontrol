#!/usr/bin/env python3

import asyncio
import json
import signal
from pathlib import Path
from tempfile import NamedTemporaryFile

import yaml
from dbus_next import BusType
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method

SCRIPT_PATH = Path(__file__).parent / "kwinctl.js"
RULES = yaml.safe_load((Path(__file__).parent / "rules.yaml").read_text())


class KWinCtl(ServiceInterface):
    NAME = "org.kevwargo.kwinctl"

    def __init__(self):
        super().__init__(self.NAME)
        self.bus: MessageBus | None = None
        self.main_script = None
        self._stop_event = asyncio.Event()
        self._shutting_down = False

    @method()
    def Execute(self, value: "s"):  # noqa:F821
        print(f"execute({value})")

    async def run(self):
        loop = asyncio.get_running_loop()

        self._register_signals(loop)
        await self._register_dbus_service()
        await self._load_main_script()

        print("Service is running. Press Ctrl+C to exit.")
        await self._stop_event.wait()

        print("Run loop exiting...")

    def _register_signals(self, loop: asyncio.AbstractEventLoop):
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self._shutdown(s)))

    async def _register_dbus_service(self):
        self.bus = await MessageBus(bus_type=BusType.SESSION).connect()
        self.bus.export("/", self)
        await self.bus.request_name(self.NAME)
        print(f"{self.NAME} D-Bus service running...")

    async def _get_iface(self, service: str, path: str, interface: str):
        introspection = await self.bus.introspect(service, path)
        obj = self.bus.get_proxy_object(service, path, introspection)
        return obj.get_interface(interface)

    async def _load_main_script(self):
        with NamedTemporaryFile(mode="w+", prefix="kwinctl-", suffix=".js") as f:
            print(f"const kwinctlDBus = {self.NAME!r}", file=f)
            print(f"const kwinctlRules = {json.dumps(RULES)};", file=f)
            f.write(SCRIPT_PATH.read_text())
            f.flush()

            await self._cleanup_kglobalaccel()
            self.main_script = await self._load_script(f.name)
            await self.main_script.call_run()

    async def _cleanup_kglobalaccel(self):
        print("calling cleanUp on org.kde.kglobalaccel /component/kwin")
        comp = await self._get_iface("org.kde.kglobalaccel", "/component/kwin", "org.kde.kglobalaccel.Component")
        res = await comp.call_clean_up()
        if res:
            print("leftover KWin shortcuts cleaned up")

    async def _load_script(self, path: str):
        scripting = await self._get_iface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting")

        script_id = await scripting.call_load_script(path)
        print(f"loaded script id: {script_id}")

        return await self._get_iface(
            "org.kde.KWin",
            f"/Scripting/Script{script_id}",
            "org.kde.kwin.Script",
        )

    async def _shutdown(self, sig=None):
        if self._shutting_down:
            return
        self._shutting_down = True

        print(f"Shutdown initiated (signal={sig})")

        try:
            if self.main_script is not None:
                try:
                    print("Stopping KWin script...")
                    await self.main_script.call_stop()
                except Exception as e:
                    print(f"Error stopping script: {e}")
            if self.bus is not None:
                try:
                    await self._cleanup_kglobalaccel()
                except Exception as e:
                    print(f"Error cleaning up kglobalaccel: {e}")

                try:
                    print("Releasing D-Bus name...")
                    await self.bus.release_name(self.NAME)
                except Exception as e:
                    print(f"Error releasing name: {e}")

                try:
                    print("Disconnecting from D-Bus...")
                    self.bus.disconnect()
                except Exception as e:
                    print(f"Error disconnecting bus: {e}")
        finally:
            self._stop_event.set()
            print("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(KWinCtl().run())
    except KeyboardInterrupt:
        print("Forced exit")
