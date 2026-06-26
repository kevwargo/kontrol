#!/usr/bin/env python3

import asyncio
import json
import logging
import os
import signal
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from subprocess import Popen
from tempfile import NamedTemporaryFile

import yaml
from dbus_next import BusType, Message
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method
from PyQt6.QtGui import QKeySequence

SCRIPT_PATH = Path("/usr/share/kwinctl/script.js")
DEFAULT_RULES_PATH = Path("/usr/share/kwinctl/rules.yaml")
RULES_PATH = Path("~/.local/share/kwinctl/rules.yaml").expanduser()
SCRIPT_UNIQUE_NAME = "kwinctl"

logfmt = "[%(levelname)s] %(message)s"

if sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty():
    logfmt = "[%(levelname)s] %(asctime)s %(message)s"
    SCRIPT_PATH = Path(__file__).parent / "kwinctl.js"

logging.basicConfig(level=logging.DEBUG, format=logfmt)
logger = logging.getLogger("kwinctl")

SHORTCUTS_LOG = Path("~/.local/share/kwinctl/shortcuts.log").expanduser()


class KWinCtl(ServiceInterface):
    NAME = "org.kevwargo.kwinctl"

    def __init__(self):
        super().__init__(self.NAME)
        self.bus: Bus | None = None
        self.main_script = None
        self.rules = None
        self.rule_keys = None
        self.remaps = []
        self._stop_event = asyncio.Event()
        self._shutting_down = False

    @method()
    def Execute(self, value: "s"):  # noqa:F821
        p = Popen(value, shell=True, start_new_session=True)
        logger.info(f"Started command [{p.pid}]{value!r}")

    async def run(self):
        self._load_rules()
        self._register_signals()

        try:
            await self._register_dbus_service()
            await self._remap_keys()
            await self._load_main_script()

            logger.debug("Service is running. Press Ctrl+C to exit.")
            await self._stop_event.wait()

            logger.debug("Run loop exiting...")
        finally:
            await self._shutdown()

    def _register_signals(self):
        loop = asyncio.get_running_loop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self._shutdown(s)))

        loop.add_signal_handler(signal.SIGCHLD, self._reap_children)

    def _reap_children(self, s=None):
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError as e:
                logger.info(f"reaped last child: {e}")
                break

            logger.info(f"reaped [{pid}]({status})")

            if pid == 0:
                logger.info("reaped last child")
                break

    async def _register_dbus_service(self):
        self.bus = await Bus(bus_type=BusType.SESSION).connect()
        self.bus.export("/", self)
        await self.bus.request_name(self.NAME)
        logger.info(f"{self.NAME} D-Bus service running...")

    async def _load_main_script(self):
        with NamedTemporaryFile(mode="w+", prefix="kwinctl-", suffix=".js") as f:
            print(f"const DBUS_NAME = {self.NAME!r};", file=f)
            print(f"const RULES = {json.dumps(self.rules)};", file=f)
            f.write(SCRIPT_PATH.read_text())
            f.flush()

            await self._cleanup_kglobalaccel()
            self.main_script = await self._load_script_file(f.name)
            await self.main_script.call_run()

    async def _cleanup_kglobalaccel(self):
        logger.info("calling cleanUp on org.kde.kglobalaccel /component/kwin")
        if await self.bus.kgl_cleanup_kwin():
            logger.info("leftover KWin shortcuts cleaned up")

    async def _load_script_file(self, path: str):
        script_id = (
            await self.bus.send_msg(
                destination="org.kde.KWin",
                path="/Scripting",
                interface="org.kde.kwin.Scripting",
                member="loadScript",
                signature="ss",
                body=[path, SCRIPT_UNIQUE_NAME],
            )
        )[0]
        if script_id < 0:
            raise RuntimeError(f"KWin script {SCRIPT_UNIQUE_NAME!r} is already running")

        logger.debug(f"loaded script id: {script_id}")

        return await self.bus.kwin_load_script(script_id)

    def _load_rules(self):
        if not RULES_PATH.exists():
            logger.info(f"{RULES_PATH} doesn't exit, copying default {DEFAULT_RULES_PATH}")
            RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
            RULES_PATH.write_text(DEFAULT_RULES_PATH.read_text())

        self.rules = [r | {"id": k} for k, r in yaml.safe_load(RULES_PATH.read_text()).items()]

        rule_keys = defaultdict(list)
        for r in self.rules:
            qk = QKeySequence(r["key"])
            if not qk.toString():
                raise ValueError(f"{r['key']!r} is not a valid Qt key")

            rule_keys[qk[0].toCombined()].append(r)

        if errors := [
            f"Key {k} is used for multiple rules:\n{yaml.safe_dump(rules)}"
            for k, rules in rule_keys.items()
            if len(rules) > 1
        ]:
            raise ValueError("\n".join(errors))

        self.rule_keys = {k: r[0] for k, r in rule_keys.items()}

    async def _remap_keys(self):
        async for name, component in self.bus.kgl_components():
            shortcuts = await component.call_all_shortcut_infos()

            with SHORTCUTS_LOG.open("a") as f:
                print(
                    datetime.now().isoformat(timespec="milliseconds"),
                    name,
                    json.dumps(shortcuts, indent=2),
                    file=f,
                )

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
                f"rebinding {remap['action_name']!r} in {remap['component_name']!r}: "
                f"{old_keys} -> {new_keys} (conflicts with {remap['conflicts']})"
            )
            await self.bus.kgl_set_keys(
                [
                    remap["component_id"],
                    remap["action_id"],
                    remap["component_name"],
                    remap["action_name"],
                ],
                [[[k, 0, 0, 0]] for k in remap["new_keys"]],
            )

    async def _restore_remaps(self):
        for remap in self.remaps:
            logger.info(f"restoring {remap}")
            await self.bus.kgl_set_keys(
                [
                    remap["component_id"],
                    remap["action_id"],
                    remap["component_name"],
                    remap["action_name"],
                ],
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


class Bus(MessageBus):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._iface_cache = {}

    async def kgl_components(self):
        kgl = await self._get_iface("org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel")
        names = await kgl.call_all_components()
        for name in names:
            c = await self._get_iface("org.kde.kglobalaccel", name, "org.kde.kglobalaccel.Component")
            yield name, c

    async def kgl_set_keys(self, *args):
        kgl = await self._get_iface("org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel")
        return await kgl.call_set_foreign_shortcut_keys(*args)

    async def kgl_cleanup_kwin(self):
        comp = await self._get_iface("org.kde.kglobalaccel", "/component/kwin", "org.kde.kglobalaccel.Component")
        return await comp.call_clean_up()

    async def kwin_load_script(self, script_id: int):
        return await self._get_iface(
            "org.kde.KWin",
            f"/Scripting/Script{script_id}",
            "org.kde.kwin.Script",
        )

    async def send_msg(self, **kwargs):
        return (await self.call(Message(**kwargs))).body

    async def _get_iface(self, service: str, path: str, interface: str):
        if cached := self._iface_cache.get((service, path, interface)):
            return cached

        introspection = await self.introspect(service, path)
        obj = self.get_proxy_object(service, path, introspection)
        iface = obj.get_interface(interface)
        self._iface_cache[(service, path, interface)] = iface

        return iface


if __name__ == "__main__":
    try:
        asyncio.run(KWinCtl().run())
    except KeyboardInterrupt:
        logger.error("Forced exit")
