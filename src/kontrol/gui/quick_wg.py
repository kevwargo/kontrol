import asyncio
import logging
import math
import os
import sys
from collections.abc import Callable
from functools import partial
from subprocess import PIPE, CalledProcessError

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QGridLayout

from kontrol.utils import nm
from kontrol.utils.asynch import AsyncTaskWatcher
from kontrol.utils.dbus import SystemBus
from kontrol.utils.qt.dialog import ActionButtonGroup, AsyncDialog, Keymap

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", logging.INFO),
    format="%(asctime)s | [%(levelname)s] %(message)s",
)


def main():
    Dialog.exec()


class Dialog(AsyncDialog):
    desktop_filename = "quick-wg"

    def __init__(self):
        super().__init__()

        self.activation_events: dict[str, asyncio.Event] = {}
        self.deactivation_events: dict[str, asyncio.Event] = {}

        self.tw = AsyncTaskWatcher()
        self.netmgr = NetworkManager(self.tw)

        self.keymap = Keymap(
            self,
            [
                mod + c
                for mod in ("", "Shift+")
                for r in (("0", "9"), ("A", "Z"))
                for c in map(chr, range(ord(r[0]), ord(r[1]) + 1))
            ],
        )
        self.keymap.bind("Q", self.quit)
        self.keymap.bind("Escape", self.quit)

        self.layout = QGridLayout(self)
        self.rb_group = ActionButtonGroup(self, self.tw)

        self.setWindowTitle("Wireguard VPNs")
        self.setWindowFlag(Qt.WindowType.Dialog)
        self.setWindowIcon(QIcon.fromTheme("network-vpn"))

    async def setup(self):
        active_vpns, available_vpns = await asyncio.gather(
            self.netmgr.initialize_vpns(self.dev_state_changed),
            self.list_available_vpns(),
        )
        logging.info(f"VPNs: active:{active_vpns} available:{available_vpns}")

        columns = 5 if len(sys.argv) <= 1 else int(sys.argv[1])
        rows = math.ceil(len(available_vpns) / columns)

        for idx, vpn_name in enumerate(available_vpns):
            key = self.keymap.next_free_key()
            button_label = f"{vpn_name} [{key}]" if key else vpn_name

            rb = self.rb_group.create_button(
                button_label,
                self,
                init_state=vpn_name in active_vpns,
                activate=partial(self.activate_vpn, vpn_name),
                deactivate=partial(self.deactivate_vpn, vpn_name),
            )

            self.layout.addWidget(rb, idx % rows, idx // rows)

            if key:
                self.keymap.bind(key, rb.animateClick)

    @staticmethod
    async def list_available_vpns() -> list[str]:
        cmd = ["sudo", "ls", "/etc/wireguard"]
        p = await asyncio.create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
        out, err = await p.communicate()
        if p.returncode != 0:
            raise CalledProcessError(p.returncode, cmd, stderr=err)

        return sorted(f.removesuffix(".conf") for f in out.decode().strip().split())

    def dev_state_changed(self, name, old: str | int | None, new: str | int, reason: str | int):
        if new == "ACTIVATED":
            if event := self.activation_events.get(name):
                logging.info(f"Device {name} activated, notifying {event}")
                event.set()
        elif (old, new) == ("ACTIVATED", "UNMANAGED"):
            if event := self.deactivation_events.get(name):
                logging.info(f"Device {name} de-activated, notifying {event}")
                event.set()
        else:
            logging.debug(f"Device {name} {old} -> {new} reason:{reason}")

    async def activate_vpn(self, name: str) -> bool:
        event = self.activation_events[name] = asyncio.Event()
        logging.info(f"Activating {name} and waiting on {event}")

        if res := await self.run_wg_quick(name, True):
            await event.wait()
            logging.info(f"Success: {name} fully activated")

        self.activation_events.pop(name, None)

        return res

    async def deactivate_vpn(self, name: str):
        event = self.deactivation_events[name] = asyncio.Event()
        logging.info(f"De-activating {name} and waiting on {event}")

        if await self.run_wg_quick(name, False):
            await event.wait()
            logging.info(f"Success: {name} fully de-activated")

        self.deactivation_events.pop(name, None)

    @staticmethod
    async def run_wg_quick(name: str, up: bool) -> bool:
        args = ["sudo", "wg-quick", "up" if up else "down", name]
        p = await asyncio.create_subprocess_exec(*args, stdout=PIPE, stderr=PIPE)
        out, err = await p.communicate()

        output = "\n".join(stream.decode() for stream in (out, err)).strip()

        res = p.returncode == 0
        (logging.info if res else logging.error)(f"Command {args}:\n{output}")

        return res

    async def cleanup(self):
        await self.tw.cleanup()


T_DEV_STATE_HANDLER = Callable[[str, str | int | None, str | int, str | int], None]


class NetworkManager:
    SERVICE = "org.freedesktop.NetworkManager"
    BASE_PATH = "/org/freedesktop/NetworkManager"

    def __init__(self, tw: AsyncTaskWatcher):
        self._bus = SystemBus()
        self._tw = tw

    async def initialize_vpns(self, dev_state_changed: T_DEV_STATE_HANDLER) -> set[str]:
        base_iface = await self._bus.iface(self.SERVICE, self.BASE_PATH, self.SERVICE)

        devices = await base_iface.call_get_devices()

        active_vpns = set()
        for path in devices:
            dev_iface = await self._bus.iface(self.SERVICE, path, f"{self.SERVICE}.Device")
            type_code = await dev_iface.get_device_type()
            if nm.DEVICE_TYPES.get(type_code) == "WIREGUARD":
                active_vpns.add(await dev_iface.get_interface())
                await self._added(path, dev_state_changed, dev_iface)

        base_iface.on_device_added(
            lambda path: self._tw.start_task(self._added(path, dev_state_changed))
        )
        base_iface.on_device_removed(lambda path: logging.debug(f"NM: removed {path}"))

        return active_vpns

    async def _added(self, path: str, dev_state_changed: T_DEV_STATE_HANDLER, iface=None):
        logging.debug(f"NM: added {path}")

        if iface is None:
            iface = await self._bus.iface(self.SERVICE, path, f"{self.SERVICE}.Device")

        dev_type_code = await iface.get_device_type()
        if not (dev_type := nm.DEVICE_TYPES.get(dev_type_code)):
            logging.warning(f"NM: {path} type code {dev_type_code} is not registered")
        elif dev_type != "WIREGUARD":
            logging.debug(f"NM: ignoring {dev_type} {path}")
        else:
            name, (state_code, reason_code) = await asyncio.gather(
                iface.get_interface(), iface.get_state_reason()
            )
            state = nm.DEVICE_STATES.get(state_code, state_code)
            reason = nm.DEVICE_STATE_REASONS.get(reason_code, reason_code)

            logging.info(f"NM: watching {path} ({name} {state} {reason})")

            dev_state_changed(name, None, state, reason)
            iface.on_state_changed(
                lambda n, o, r: dev_state_changed(
                    name,
                    nm.DEVICE_STATES.get(o, o),
                    nm.DEVICE_STATES.get(n, n),
                    nm.DEVICE_STATE_REASONS.get(r, r),
                )
            )


if __name__ == "__main__":
    main()
