import asyncio
import json
import logging
import os
import signal
from argparse import ArgumentParser
from collections import defaultdict
from pathlib import Path
from subprocess import PIPE, Popen
from tempfile import NamedTemporaryFile

import yaml
from dbus_next import BusType, Message
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method

from kontrol.utils.kbd import KeySequence, ShortcutInfo

SCRIPT_UNIQUE_NAME = "kwinctl"


def main():
    env = Environment()
    asyncio.run(OverridesManager(env).sync() if env.args.sync_overrides else KWinCtl(env).run())


class Environment:
    def __init__(self):
        self.sysdir = Path("/usr/share/kwinctl")
        self.userdir = Path("~/.local/share/kwinctl").expanduser()
        self.localdir = Path(__file__).parent

        self.parse_args()

        logfmt = (
            "[%(levelname)s] %(message)s"
            if self.args.service
            else "[%(levelname)s] %(asctime)s %(message)s"
        )

        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(logfmt))
        self.log = logging.getLogger("kwinctl")
        self.log.setLevel(logging.DEBUG)
        self.log.addHandler(handler)

    def parse_args(self):
        parser = ArgumentParser()
        parser.add_argument("--service", action="store_true", help="Run as a systemd service")
        parser.add_argument(
            "-O",
            "--sync-overrides",
            action="store_true",
            help="Sync the current state of global shortcuts into overrides.yaml",
        )
        parser.add_argument("-X", "--reset-overrides", action="store_true")
        parser.add_argument(
            "-c",
            "--components",
            action="append",
            help="Instead of non-default, sync shortcuts from these components",
        )
        self.args = parser.parse_args()

    def read_raw(self, filename: str) -> str:
        return ((self.sysdir if self.args.service else self.localdir) / filename).read_text()

    def read_cfg(self, filename: str) -> dict:
        if not self.args.service:
            return self._read_yaml(self.localdir / filename)

        try:
            cfg = self._read_yaml(self.sysdir / filename)
        except FileNotFoundError:
            cfg = {}

        try:
            cfg.update(self._read_yaml(self.userdir / filename))
        except FileNotFoundError:
            pass

        # trimming empty values to allow user config disable settings
        # defined in system config

        merged_cfg = {}
        for k, v in cfg.items():
            if v is None:
                self.log.debug(f"Removing disabled {k} from {filename}")
            else:
                self.log.debug(f"Setting {filename}:{k} = {v}")
                merged_cfg[k] = v

        return merged_cfg

    def write_cfg(self, filename: str, cfg: dict):
        if self.args.service:
            path = self.userdir / filename
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path = self.localdir / filename

        path.write_text(yaml.safe_dump(cfg, sort_keys=False))

    @staticmethod
    def _read_yaml(path: Path) -> dict:
        return yaml.safe_load(path.read_text())


class KWinCtl(ServiceInterface):
    NAME = "org.kevwargo.kwinctl"

    def __init__(self, env: Environment):
        super().__init__(self.NAME)
        self.env = env
        self.bus: Bus | None = None
        self.main_script = None
        self.remaps: list[ShortcutInfo] = []
        self.hotkeys = HotkeysConfig(self.env)
        self._stop_event = asyncio.Event()
        self._shutting_down = False

    @method()
    def RunShellCommand(self, cmd: "s"):  # noqa:F821
        p = Popen(cmd, shell=True, start_new_session=True)
        self.env.log.info(f"Started shell command [{p.pid}]({cmd})")

    @method()
    def RunCommand(self, cmd_id: "s"):  # noqa:F821
        cmd = self.hotkeys.commands.get(cmd_id)
        if not cmd:
            self.env.log.error(f"Command {cmd_id} not found")
            return

        if cmd_args := cmd.get("cmd"):
            p = Popen(cmd_args, start_new_session=True)
            self.env.log.info(f"Started command [{p.pid}]({cmd_args})")
        elif shell := cmd.get("shell"):
            self.RunShellCommand(shell)
        elif prompt := cmd.get("prompt"):
            asyncio.create_task(self._show_prompt(prompt))
        elif snippet := cmd.get("snippet"):
            asyncio.create_task(self._exec_snippet(cmd_id, snippet))

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

    async def _show_prompt(self, prompt: str):
        await self.bus.krunner_query(prompt)
        self.env.log.info(f"Showed prompt {prompt!r}")

    async def _exec_snippet(self, cmd_id: str, snippet: dict):
        cmd = snippet.get("cmd")

        if not (text := snippet.get("text")):
            p = await asyncio.create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)
            out, err = await p.communicate()
            text = out.decode()
            if p.returncode != 0:
                self.env.log.error(
                    f"Command {cmd!r} failed with code {p.returncode}. out:{out} err:{err}"
                )
                return

        await self.bus.klipper_set(text)

        if notify := snippet.get("notify"):
            body_fields = {
                "Name": cmd_id,
                "Command": cmd if notify.get("details") else None,
                "Details": text if notify.get("details") else None,
            }

            await self.bus.notify(
                "Snippet activated",
                "\n".join(
                    f"{k}: <u><b>{v}</b></u>" for k, v in body_fields.items() if v is not None
                ),
                notify.get("timeout", 3000),
            )

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

        if reaped:
            self.env.log.info(f"reaped {'; '.join(reaped)}")

    async def _register_dbus_service(self):
        self.bus = await Bus().connect()
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

            f.write(self.env.read_raw("kwinctl.js"))
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
                s.remapped_keys = list(keys)
                self.remaps.append(s)

        for remap in self.remaps:
            self.env.log.info(f"Remapping {remap}")
            await self.bus.kgl_set_keys(
                remap.to_dbus(), [[k.to_dbus()] for k in remap.remapped_keys]
            )

    async def _restore_remaps(self):
        for remap in self.remaps:
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


class Bus(MessageBus):
    def __init__(self, *args, **kwargs):
        if not kwargs:
            kwargs.update(bus_type=BusType.SESSION)

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

    async def krunner_query(self, prompt: str):
        iface = await self._get_iface("org.kde.krunner", "/App", "org.kde.krunner.App")
        await iface.call_display()
        await iface.call_query(prompt)

    async def klipper_set(self, text: str):
        iface = await self._get_iface("org.kde.klipper", "/klipper", "org.kde.klipper.klipper")
        await iface.call_set_clipboard_contents(text)

    async def notify(self, summary: str, body: str, timeout: int):
        iface = await self._get_iface(
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
        )
        await iface.call_notify("kwinctl", 0, "", summary, body, [], {}, timeout)

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
    COMMAND_KEYS = {"shell", "cmd", "prompt", "snippet"}

    def __init__(self, env: Environment):
        self.env = env
        self.bindings: dict[KeySequence, dict] = defaultdict(list)

        self._load_rules()
        self._load_commands()
        self._load_overrides()

        if errors := [
            f"Key {k} is used multiple times: "
            + json.dumps(items, indent=2, default=json_default, ensure_ascii=False)
            for k, items in self.bindings.items()
            if len(items) > 1
        ]:
            raise ValueError("\n".join(errors))

        self.bindings = {k: i[0] for k, i in self.bindings.items()}

    def _load_rules(self):
        self.rules = [{"id": k} | r for k, r in self.env.read_cfg("rules.yaml").items()]
        for rule in self.rules:
            rule["key"] = validate_key(rule.get("key"), f"Rule {rule}")
            if not (rule.get("cls") or rule.get("caption")):
                raise ValueError(f"Rule matches nothing: {rule}")
            self.bindings[rule["key"]].append({"type": "rule"} | rule)

    def _load_commands(self):
        self.commands = self.env.read_cfg("commands.yaml")

        for cmd in [{"id": k} | c for k, c in self.commands.items()]:
            cmd["key"] = validate_key(cmd.get("key"), f"Command {cmd}")
            if len(self.COMMAND_KEYS.intersection(cmd)) != 1:
                raise ValueError(
                    f"Command must have exactly one of {self.COMMAND_KEYS}, but has {cmd}"
                )
            self.bindings[cmd["key"]].append({"type": "command"} | cmd)

    def _load_overrides(self):
        self.overrides: dict[tuple[str, str], dict] = {}

        cfg = self.env.read_cfg("overrides.yaml")
        for comp_id, actions in cfg.items():
            for act_id, action in actions.items():
                override = {"component_id": comp_id, "action_id": act_id} | action
                action["keys"] = [
                    validate_key(k, f"Override {override}") for k in action.get("keys", [])
                ]

                self.overrides[(comp_id, act_id)] = action
                for k in action["keys"]:
                    self.bindings[k].append({"type": "override"} | override)


def validate_key(raw: str | None, err_msg: str) -> KeySequence:
    if not raw:
        raise ValueError(f"{err_msg}: Key not defined")
    if not (qk := KeySequence(raw)).toString():
        raise ValueError(f"{err_msg}: Key {raw!r} is invalid")

    return qk


def json_default(x) -> str:
    return x.toString() if isinstance(x, KeySequence) else str(x)


class OverridesManager:
    def __init__(self, env: Environment):
        self.bus: Bus | None = None
        self.env = env

    async def sync(self):
        self.bus = await Bus().connect()

        if self.args.reset_overrides:
            overrides = {}
        else:
            overrides = HotkeysConfig(self.env).overrides

        active = await self._active_shortcuts()
        for (comp_id, act_id), action in active.items():
            if self.args.components:
                if comp_id not in self.args.components:
                    continue
            elif action["keys"] == action["default_keys"]:
                continue

            overrides[(comp_id, act_id)] = action

        overrides_export = defaultdict(dict)
        for (comp_id, act_id), action in overrides.items():
            overrides_export[comp_id][act_id] = {
                "name": action["name"],
                "keys": [str(k) for k in action["keys"]],
            }

        self.env.write_cfg_file(
            "overrides.yaml", yaml.safe_dump(dict(overrides_export), sort_keys=False)
        )

    async def _active_shortcuts(self) -> dict[tuple[str, str], dict]:
        all_shortcuts = {}
        for s in await self.bus.kgl_all_shortcuts():
            if s.action_id.startswith("kwinctl_"):
                continue

            all_shortcuts[(s.component_id, s.action_id)] = {
                "name": s.action_name,
                "keys": s.active_keys,
                "default_keys": s.default_keys,
            }

        return all_shortcuts


if __name__ == "__main__":
    main()
