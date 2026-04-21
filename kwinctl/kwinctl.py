#!/usr/bin/env python3

import asyncio
import json
import logging
import signal
from collections import defaultdict
from pathlib import Path
from subprocess import Popen
from tempfile import NamedTemporaryFile

import yaml
from dbus_next import BusType
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method
from PyQt6.QtGui import QKeySequence

SCRIPT_PATH = Path("/usr/share/kwinctl/script.js")
DEFAULT_RULES_PATH = Path("/usr/share/kwinctl/rules.yaml")
RULES_PATH = Path("~/.local/share/kwinctl/rules.yaml").expanduser()

logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("kwinctl")


class KWinCtl(ServiceInterface):
    NAME = "org.kevwargo.kwinctl"

    def __init__(self):
        super().__init__(self.NAME)
        self.bus: MessageBus | None = None
        self.main_script = None
        self.rules = None
        self.rule_keys = None
        self.remaps = []
        self._stop_event = asyncio.Event()
        self._shutting_down = False

    @method()
    def Execute(self, value: "s"):  # noqa:F821
        Popen(value, shell=True)

    async def run(self):
        self._load_rules()

        loop = asyncio.get_running_loop()

        self._register_signals(loop)
        try:
            await self._register_dbus_service()
            await self._remap_keys()
            await self._load_main_script()

            logger.debug("Service is running. Press Ctrl+C to exit.")
            await self._stop_event.wait()

            logger.debug("Run loop exiting...")
        finally:
            await self._shutdown()

    def _register_signals(self, loop: asyncio.AbstractEventLoop):
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self._shutdown(s)))

    async def _register_dbus_service(self):
        self.bus = await MessageBus(bus_type=BusType.SESSION).connect()
        self.bus.export("/", self)
        await self.bus.request_name(self.NAME)
        logger.info(f"{self.NAME} D-Bus service running...")

    async def _get_iface(self, service: str, path: str, interface: str):
        introspection = await self.bus.introspect(service, path)
        obj = self.bus.get_proxy_object(service, path, introspection)
        return obj.get_interface(interface)

    async def _load_main_script(self):
        with NamedTemporaryFile(mode="w+", prefix="kwinctl-", suffix=".js") as f:
            print(f"const kwinctlDBus = {self.NAME!r}", file=f)
            print(f"const kwinctlRules = {json.dumps(self.rules)};", file=f)
            f.write(SCRIPT_PATH.read_text())
            f.flush()

            await self._cleanup_kglobalaccel()
            self.main_script = await self._load_script(f.name)
            await self.main_script.call_run()

    async def _cleanup_kglobalaccel(self):
        logger.info("calling cleanUp on org.kde.kglobalaccel /component/kwin")
        comp = await self._get_iface("org.kde.kglobalaccel", "/component/kwin", "org.kde.kglobalaccel.Component")
        res = await comp.call_clean_up()
        if res:
            logger.info("leftover KWin shortcuts cleaned up")

    async def _load_script(self, path: str):
        scripting = await self._get_iface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting")

        script_id = await scripting.call_load_script(path)
        logger.debug(f"loaded script id: {script_id}")

        return await self._get_iface(
            "org.kde.KWin",
            f"/Scripting/Script{script_id}",
            "org.kde.kwin.Script",
        )

    def _load_rules(self):
        if not RULES_PATH.exists():
            logger.info(f"{RULES_PATH} doesn't exit, copying default {DEFAULT_RULES_PATH}")
            RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
            RULES_PATH.write_text(DEFAULT_RULES_PATH.read_text())

        self.rules = yaml.safe_load(RULES_PATH.read_text())

        rule_keys = defaultdict(list)
        for i, r in self.rules.items():
            qk = QKeySequence(r["key"])
            if not qk.toString():
                raise ValueError(f"{r['key']!r} is not a valid Qt key")

            rule_keys[qk[0].toCombined()].append(r | {"id": i})

        errors = []
        for k, rules in rule_keys.items():
            if len(rules) > 1:
                errors.append(f"Key {k} is used for multiple rules:\n{yaml.safe_dump(rules)}")

        if errors:
            raise ValueError("\n".join(errors))

        self.rule_keys = {k: r[0] for k, r in rule_keys.items()}

    async def _remap_keys(self):
        kglobalaccel = await self._get_iface("org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel")
        components = await kglobalaccel.call_all_components()
        for c in components:
            component = await self._get_iface("org.kde.kglobalaccel", c, "org.kde.kglobalaccel.Component")
            shortcuts = await component.call_all_shortcut_infos()
            for s in shortcuts:
                keys = s[6]
                if all(k not in self.rule_keys for k in keys):
                    continue

                self.remaps.append(
                    {
                        "action_id": s[0],
                        "action_name": s[1],
                        "component_id": s[2],
                        "component_name": s[3],
                        "keys": keys,
                        "new_keys": [k for k in keys if k not in self.rule_keys],
                        "conflicts": [self.rule_keys[k] for k in keys if k in self.rule_keys],
                    }
                )

        for remap in self.remaps:
            old_keys = [QKeySequence(k).toString() for k in remap["keys"]]
            new_keys = [QKeySequence(k).toString() for k in remap["new_keys"]]
            logger.info(
                f"rebinding {remap['action_name']!r} in {remap['component_name']!r}: {old_keys} -> {new_keys}"
                f" (conflicts with {remap['conflicts']})"
            )
            await kglobalaccel.call_set_foreign_shortcut_keys(
                [remap["component_id"], remap["action_id"], remap["component_name"], remap["action_name"]],
                [[[k, 0, 0, 0]] for k in remap["new_keys"]],
            )

    async def _restore_remaps(self):
        kglobalaccel = await self._get_iface("org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel")
        for remap in self.remaps:
            logger.info(f"restoring {remap}")
            await kglobalaccel.call_set_foreign_shortcut_keys(
                [remap["component_id"], remap["action_id"], remap["component_name"], remap["action_name"]],
                [[[k, 0, 0, 0]] for k in remap["keys"]],
            )

    async def _shutdown(self, sig=None):
        if self._shutting_down:
            return
        self._shutting_down = True

        logger.info(f"Shutdown initiated (signal={sig})")

        try:
            if self.main_script is not None:
                try:
                    logger.info("Stopping KWin script...")
                    await self.main_script.call_stop()
                except Exception as e:
                    logger.error(f"Error stopping script: {e}")
            if self.bus is not None:
                try:
                    await self._cleanup_kglobalaccel()
                except Exception as e:
                    logger.error(f"Error cleaning up kglobalaccel: {e}")

                try:
                    await self._restore_remaps()
                except Exception as e:
                    logger.error(f"Restoring remapped keys: {e}")

                try:
                    logger.info("Releasing D-Bus name...")
                    await self.bus.release_name(self.NAME)
                except Exception as e:
                    logger.error(f"Error releasing name: {e}")

                try:
                    logger.info("Disconnecting from D-Bus...")
                    self.bus.disconnect()
                except Exception as e:
                    logger.error(f"Error disconnecting bus: {e}")
        finally:
            self._stop_event.set()
            logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(KWinCtl().run())
    except KeyboardInterrupt:
        logger.error("Forced exit")
