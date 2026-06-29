#!/usr/bin/env python3

import asyncio
import json
import logging
import os
import signal
import sys
from argparse import ArgumentParser
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from subprocess import Popen
from subprocess import run as run_cmd
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

    def read_cfg_file(self, base_name: str, only_global=False) -> str:
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
            return ""


class KWinCtl(ServiceInterface):
    NAME = "org.kevwargo.kwinctl"

    def __init__(self):
        super().__init__(self.NAME)
        self.env = Environment()
        self.bus: Bus | None = None
        self.main_script = None
        self.remaps: dict[tuple, ShortcutInfo] = {}
        self.hotkeys = HotkeysConfig(self.env)
        self._stop_event = asyncio.Event()
        self._shutting_down = False

    @method()
    def RunShellCommand(self, cmd: "s"):  # noqa:F821
        p = Popen(cmd, shell=True, start_new_session=True)
        self.env.log.info(f"Started shell command [{p.pid}]({cmd})")

    @method()
    def RunCommand(self, varargs: "av"):  # noqa:F821
        cmd = []
        for idx, arg in enumerate(varargs):
            if not (arg.signature == "s" and isinstance(arg.value, str)):
                raise ValueError(f"Invalid arg {idx}: val={arg.value!r} sig={arg.signature}")

            cmd.append(arg.value)

        p = Popen(cmd, start_new_session=True)
        self.env.log.info(f"Started command [{p.pid}]({cmd})")

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
        reaped = []

        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
            reaped.append(f"{pid}(status: {status})")

        self.env.log.info(f"reaped {'; '.join(reaped)}")

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
                print(f"const {name} = {json.dumps(value, default=json_default)};", file=f)

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
        for s in await self.bus.kgl_all_shortcuts():
            keys = set(s.active_keys)

            override = self.hotkeys.overrides.get((s.component_id, s.action_id))
            if override:
                keys = set(override["keys"])
                self.env.log.info(f"Found override for {s}: {keys}")
            else:
                keys.difference_update(self.hotkeys.bindings)

            if set(s.active_keys) != keys:
                self.remaps[tuple(keys)] = s

        for new_keys, remap in self.remaps.items():
            self.env.log.info(f"Remapping {remap} to {new_keys}")
            await self.bus.kgl_set_keys(remap.to_dbus(), [[k.to_dbus()] for k in new_keys])

    async def _restore_remaps(self):
        for remap in self.remaps.values():
            self.env.log.info(f"Restoring {remap}")
            await self.bus.kgl_set_keys(
                remap.to_dbus(), [[k.to_dbus()] for k in remap.active_keys]
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


@dataclass
class ShortcutInfo:
    action_id: str
    action_name: str
    component_id: str
    component_name: str
    context_id: str
    context_name: str
    active_keys: list[KeySequence]
    default_keys: list[KeySequence]

    @classmethod
    def from_list(cls, fields: list[str]):
        return cls(
            action_id=fields[0],
            action_name=fields[1],
            component_id=fields[2],
            component_name=fields[3],
            context_id=fields[4],
            context_name=fields[5],
            active_keys=[KeySequence(k) for k in fields[6] if k],
            default_keys=[KeySequence(k) for k in fields[7] if k],
        )

    def __str__(self):
        return (
            f"{self.component_id}({self.component_name}):{self.action_id}({self.action_name}):"
            f" {self.active_keys}"
        )

    def __repr__(self):
        return repr(str(self))

    def to_dbus(self) -> list[str]:
        return [self.component_id, self.action_id, self.component_name, self.action_name]


class Bus(MessageBus):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._iface_cache = {}

    async def kgl_all_shortcuts(self) -> list[ShortcutInfo]:
        all_shortcuts = []
        kgl = await self._get_iface(
            "org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel"
        )
        names = await kgl.call_all_components()
        for name in names:
            c = await self._get_iface(
                "org.kde.kglobalaccel", name, "org.kde.kglobalaccel.Component"
            )
            all_shortcuts.extend(map(ShortcutInfo.from_list, await c.call_all_shortcut_infos()))

        return all_shortcuts

    async def kgl_set_keys(self, *args):
        kgl = await self._get_iface(
            "org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel"
        )
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
    COMMAND_KEYS = {"shell", "cmd", "prompt"}

    def __init__(self, env: Environment):
        self.env = env
        self.bindings: dict[KeySequence, dict] = defaultdict(list)

        self._load_rules()
        self._load_commands()
        self._load_overrides()

        if errors := [
            f"Key {k} is used multiple times: " + json.dumps(items, indent=2, default=json_default)
            for k, items in self.bindings.items()
            if len(items) > 1
        ]:
            raise ValueError("\n".join(errors))

        self.bindings = {k: i[0] for k, i in self.bindings.items()}

    def _load_rules(self):
        self.rules = [
            {"id": k} | r
            for k, r in (yaml.safe_load(self.env.read_cfg_file("rules.yaml")) or {}).items()
        ]
        for rule in self.rules:
            rule["key"] = validate_key(rule.get("key"), f"Rule {rule}")
            if not (rule.get("cls") or rule.get("caption")):
                raise ValueError(f"Rule matches nothing: {rule}")
            self.bindings[rule["key"]].append({"type": "rule"} | rule)

    def _load_commands(self):
        self.commands = [
            {"id": k} | c
            for k, c in (yaml.safe_load(self.env.read_cfg_file("commands.yaml")) or {}).items()
        ]
        for cmd in self.commands:
            cmd["key"] = validate_key(cmd.get("key"), f"Command {cmd}")
            if len(self.COMMAND_KEYS.intersection(cmd)) != 1:
                raise ValueError(
                    f"Command must have exactly one of {self.COMMAND_KEYS}, but has {cmd}"
                )
            self.bindings[cmd["key"]].append({"type": "command"} | cmd)

    def _load_overrides(self):
        self.overrides: dict[tuple[str, str], dict] = {}

        overrides = yaml.safe_load(self.env.read_cfg_file("overrides.yaml")) or {}

        for comp_id, component in overrides.items():
            for act_id, action in component["actions"].items():
                override = {"component_id": comp_id, "action_id": act_id} | action
                action["keys"] = [
                    validate_key(k, f"Override {override}") for k in action.get("keys", [])
                ]

                self.overrides[(comp_id, act_id)] = action
                for k in action["keys"]:
                    self.bindings[k].append({"type": "override"} | override)


class KeySequence(QKeySequence):
    def __init__(self, raw):
        super().__init__(raw)
        if not self.toString():
            raise ValueError(f"Invalid key {raw!r}")

    def __str__(self):
        return self.toString()

    def __repr__(self):
        return repr(self.toString())

    def to_dbus(self) -> list[int]:
        numeric = [k.toCombined() for k in self]
        if (rem := 4 - len(numeric)) > 0:
            numeric.extend([0] * rem)
        return numeric


def validate_key(raw: str | None, err_msg: str) -> KeySequence:
    if not raw:
        raise ValueError(f"{err_msg}: Key not defined")
    if not (qk := KeySequence(raw)).toString():
        raise ValueError(f"{err_msg}: Key {raw!r} is invalid")

    return qk


def json_default(x) -> str:
    return x.toString() if isinstance(x, KeySequence) else str(x)


async def list_all_shortcuts(bus: Bus):
    all_shortcuts = {}
    for s in await bus.kgl_all_shortcuts():
        if s.action_id.startswith("kwinctl_"):
            continue

        if s.component_id not in all_shortcuts:
            all_shortcuts[s.component_id] = {"name": s.component_name, "actions": {}}

        all_shortcuts[s.component_id]["actions"][s.action_id] = {
            "name": s.action_name,
            "keys": s.active_keys,
            "default_keys": s.default_keys,
        }

    return all_shortcuts


async def record_overrides(args):
    bus = await Bus(bus_type=BusType.SESSION).connect()

    orig = await list_all_shortcuts(bus)
    run_cmd(["systemsettings", "kcm_keys"], check=True)
    new = await list_all_shortcuts(bus)

    modified = {}

    for c_id, component in new.items():
        if c_id in orig:
            for a_id, action in component["actions"].items():
                if orig[c_id]["actions"][a_id]["keys"] != action["keys"]:
                    if c_id not in modified:
                        modified[c_id] = {"name": component["name"], "actions": {}}
                    modified[c_id]["actions"][a_id] = action
        else:
            modified[c_id] = component

    for name, data in {
        "orig": orig,
        "new": new,
        "modified": modified,
    }.items():
        Path(f"overrides-{name}.yaml").write_text(yaml.safe_dump(data, sort_keys=False))


async def main():
    parser = ArgumentParser()
    parser.add_argument("-O", "--record-overrides", action="store_true")
    args = parser.parse_args()

    if args.record_overrides:
        await record_overrides(args)
    else:
        await KWinCtl().run()


if __name__ == "__main__":
    asyncio.run(main())
