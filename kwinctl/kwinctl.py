#!/usr/bin/env python3

import asyncio
import json
import logging
import os
import signal
import sys
from collections import defaultdict
from pathlib import Path
from subprocess import Popen
from tempfile import NamedTemporaryFile

import yaml
from dbus_next import BusType, Message
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method
from PyQt6.QtGui import QKeySequence

SCRIPT_UNIQUE_NAME = "kwinctl"


class Environment:
    USER_DIR = Path("~/.local/share/kwinctl").expanduser()
    GLOBAL_DIR = Path("/usr/share/kwinctl")

    def __init__(self):
        self.interactive = all(f.isatty() for f in (sys.stdin, sys.stdout, sys.stderr))

        logfmt = (
            "[%(levelname)s] %(asctime)s %(message)s"
            if self.interactive
            else "[%(levelname)s] %(message)s"
        )

        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(logfmt))
        self.log = logging.getLogger("kwinctl")
        self.log.setLevel(logging.DEBUG)
        self.log.addHandler(handler)

    def read_cfg_file(self, base_name: str, only_global=False) -> str | None:
        try:
            if self.interactive:
                return (Path(__file__).parent / base_name).read_text()

            global_path = self.GLOBAL_DIR / base_name
            if only_global:
                return global_path.read_text()

            if not (user_path := self.USER_DIR / base_name).exists():
                self.log.info(f"Initializing {user_path} from default {global_path}...")
                user_path.parent.mkdir(parents=True, exist_ok=True)
                user_path.write_bytes(global_path.read_bytes())

            return user_path.read_text()
        except FileNotFoundError:
            return None


class KWinCtl(ServiceInterface):
    NAME = "org.kevwargo.kwinctl"

    def __init__(self, env: Environment):
        super().__init__(self.NAME)
        self.env = env
        self.bus: Bus | None = None
        self.main_script = None
        self.remaps = []
        self.hotkeys = HotkeysConfig(env)
        self._stop_event = asyncio.Event()
        self._shutting_down = False

    @method()
    def Execute(self, cmd: "s"):  # noqa:F821
        p = Popen(cmd, shell=True, start_new_session=True)
        self.env.log.info(f"Started command [{p.pid}]{cmd!r}")

    async def run(self):
        self._register_signals()

        try:
            await self._register_dbus_service()
            await self._remap_keys()
            await self._load_main_script()

            self.env.log.debug("Service is running. Press Ctrl+C to exit.")
            await self._stop_event.wait()

            self.env.log.debug("Run loop exiting...")
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
                self.env.log.info(f"reaped last child: {e}")
                break

            self.env.log.info(f"reaped {pid}, status: {status}")

            if pid == 0:
                self.env.log.info("reaped last child")
                break

    async def _register_dbus_service(self):
        self.bus = await Bus(bus_type=BusType.SESSION).connect()
        self.bus.export("/", self)
        await self.bus.request_name(self.NAME)
        self.env.log.info(f"{self.NAME} D-Bus service running...")

    async def _load_main_script(self):
        with NamedTemporaryFile(mode="w+", prefix="kwinctl-", suffix=".js") as f:
            for name, value in {
                "DBUS_NAME": self.NAME,
                "RULES": self.hotkeys.rules,
                "COMMANDS": self.hotkeys.commands,
            }.items():
                print(f"const {name} = {json.dumps(value)};", file=f)

            f.write(self.env.read_cfg_file("kwinctl.js", True))
            f.flush()

            await self._cleanup_kglobalaccel()
            self.main_script = await self._load_script_file(f.name)
            await self.main_script.call_run()

    async def _cleanup_kglobalaccel(self):
        self.env.log.info("calling cleanUp on org.kde.kglobalaccel /component/kwin")
        if await self.bus.kgl_cleanup_kwin():
            self.env.log.info("leftover KWin shortcuts cleaned up")

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

        self.env.log.debug(f"loaded script id: {script_id}")

        return await self.bus.kwin_load_script(script_id)

    async def _remap_keys(self):
        async for name, component in self.bus.kgl_components():
            shortcuts = await component.call_all_shortcut_infos()

            for s in shortcuts:
                keys = s[6]
                if all(k not in self.hotkeys.bindings for k in keys):
                    continue

                self.remaps.append(
                    {
                        "action_id": s[0],
                        "action_name": s[1],
                        "component_id": s[2],
                        "component_name": s[3],
                        "preserved_keys": keys,
                        "temp_new_keys": [k for k in keys if k not in self.hotkeys.bindings],
                        "conflicts": [
                            self.hotkeys.bindings[k] for k in keys if k in self.hotkeys.bindings
                        ],
                    }
                )

        for remap in self.remaps:
            preserved_keys = [QKeySequence(k).toString() for k in remap["preserved_keys"]]
            temp_new_keys = [QKeySequence(k).toString() for k in remap["temp_new_keys"]]
            self.env.log.info(
                f"rebinding {remap['action_name']!r} in {remap['component_name']!r}: "
                f"{preserved_keys} -> {temp_new_keys} (conflicts with {remap['conflicts']})"
            )
            await self.bus.kgl_set_keys(
                [
                    remap["component_id"],
                    remap["action_id"],
                    remap["component_name"],
                    remap["action_name"],
                ],
                [[[k, 0, 0, 0]] for k in remap["temp_new_keys"]],
            )

    async def _restore_remaps(self):
        for remap in self.remaps:
            self.env.log.info(f"restoring {remap}")
            await self.bus.kgl_set_keys(
                [
                    remap["component_id"],
                    remap["action_id"],
                    remap["component_name"],
                    remap["action_name"],
                ],
                [[[k, 0, 0, 0]] for k in remap["preserved_keys"]],
            )

    async def _shutdown(self, sig=None):
        if self._shutting_down:
            return
        self._shutting_down = True

        self.env.log.info(f"Shutdown initiated (signal={sig})")

        try:
            if self.main_script is not None:
                try:
                    self.env.log.info("Stopping KWin script...")
                    await self.main_script.call_stop()
                except Exception as e:
                    self.env.log.error(f"Error stopping script: {e}")
            if self.bus is not None:
                try:
                    await self._cleanup_kglobalaccel()
                except Exception as e:
                    self.env.log.error(f"Error cleaning up kglobalaccel: {e}")

                try:
                    await self._restore_remaps()
                except Exception as e:
                    self.env.log.error(f"Restoring remapped keys: {e}")

                try:
                    self.env.log.info("Releasing D-Bus name...")
                    await self.bus.release_name(self.NAME)
                except Exception as e:
                    self.env.log.error(f"Error releasing name: {e}")

                try:
                    self.env.log.info("Disconnecting from D-Bus...")
                    self.bus.disconnect()
                except Exception as e:
                    self.env.log.error(f"Error disconnecting bus: {e}")
        finally:
            self._stop_event.set()
            self.env.log.info("Shutdown complete.")


class Bus(MessageBus):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._iface_cache = {}

    async def kgl_components(self):
        kgl = await self._get_iface("org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel")
        names = await kgl.call_all_components()
        for name in names:
            c = await self._get_iface(
                "org.kde.kglobalaccel", name, "org.kde.kglobalaccel.Component"
            )
            yield name, c

    async def kgl_set_keys(self, *args):
        kgl = await self._get_iface("org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel")
        return await kgl.call_set_foreign_shortcut_keys(*args)

    async def kgl_cleanup_kwin(self):
        comp = await self._get_iface(
            "org.kde.kglobalaccel", "/component/kwin", "org.kde.kglobalaccel.Component"
        )
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


class HotkeysConfig:
    COMMAND_KEYS = {"shell", "cmd", "dbus"}

    def __init__(self, env: Environment):
        self.env = env

        self._load_rules()
        self._load_commands()

        bindings = defaultdict(list)
        for item in [{"type": "rule"} | r for r in self.rules] + [
            {"type": "command"} | c for c in self.commands
        ]:
            qk = QKeySequence(k := item["key"])
            if not qk.toString():
                raise ValueError(f"{k!r} is not a valid Qt key in {item}")

            bindings[qk[0].toCombined()].append(item)

        if errors := [
            f"Key {k} is used multiple times:\n{yaml.safe_dump(items)}"
            for k, items in bindings.items()
            if len(items) > 1
        ]:
            raise ValueError("\n".join(errors))

        self.bindings = {k: i[0] for k, i in bindings.items()}

    def _load_rules(self):
        self.rules = [
            {"id": k} | r
            for k, r in (yaml.safe_load(self.env.read_cfg_file("rules.yaml")) or {}).items()
        ]
        for rule in self.rules:
            if not (rule.get("cls") or rule.get("caption")):
                raise ValueError(f"Rule matches nothing: {rule}")
            if not rule.get("key"):
                raise ValueError(f"Rule does not have a key: {rule}")

    def _load_commands(self):
        self.commands = [
            {"id": k} | c
            for k, c in (yaml.safe_load(self.env.read_cfg_file("commands.yaml")) or {}).items()
        ]
        for cmd in self.commands:
            if not cmd.get("key"):
                raise ValueError(f"Command {cmd} does not have a key")

            if len(self.COMMAND_KEYS.intersection(cmd)) != 1:
                raise ValueError(
                    f"Command must have exactly one of {self.COMMAND_KEYS}, but has {cmd}"
                )


if __name__ == "__main__":
    env = Environment()
    try:
        asyncio.run(KWinCtl(env).run())
    except KeyboardInterrupt:
        env.log.error("Forced exit")
