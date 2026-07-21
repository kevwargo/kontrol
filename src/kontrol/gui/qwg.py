import asyncio
import logging
import math
import os
import sys
from collections.abc import Callable
from functools import partial
from subprocess import PIPE, check_output

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QGridLayout

from kontrol.utils import nm
from kontrol.utils.asynch import AsyncTaskWatcher
from kontrol.utils.dbus import SystemBus
from kontrol.utils.qt.dialog import (ActionButtonGroup, AsyncDialog,
                                     AsyncRadioButton, Keymap)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", logging.INFO),
    format="%(asctime)s | [%(levelname)s] %(message)s",
)


def main():
    Dialog.exec()


class Dialog(AsyncDialog):
    def __init__(self):
        super().__init__()

        self.buttons: dict[str, AsyncRadioButton] = {}
        self.activation_events: dict[str, asyncio.Event] = {}
        self.deactivation_events: dict[str, asyncio.Event] = {}

        self.tw = AsyncTaskWatcher()
        self.netmgr = NetworkManager(self.tw)
        self.available_vpns = sorted(
            f.removesuffix(".conf")
            for f in check_output(["sudo", "ls", "/etc/wireguard"], text=True).strip().split()
        )

        self.init_keymap()
        self.init_layout()

        self.setWindowTitle("Wireguard VPNs")
        self.setWindowFlag(Qt.WindowType.Dialog)
        self.setWindowIcon(QIcon.fromTheme("network-vpn"))

    def init_keymap(self):
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

    def init_layout(self):
        self.layout = QGridLayout(self)
        self.rb_group = ActionButtonGroup(self, self.tw)

        columns = 5 if len(sys.argv) <= 1 else int(sys.argv[1])
        rows = math.ceil(len(self.available_vpns) / columns)

        for idx, vpn_name in enumerate(self.available_vpns):
            if not (key := self.keymap.next_free_key()):
                raise ValueError(f"Too many VPN configs: {len(self.available_vpns)}")

            rb = self.rb_group.create_button(
                f"{vpn_name} [{key}]",
                self,
                activate=partial(self.activate_vpn, vpn_name),
                deactivate=partial(self.deactivate_vpn, vpn_name),
            )

            self.buttons[vpn_name] = rb
            self.keymap.bind(key, rb.animateClick)
            self.layout.addWidget(rb, idx % rows, idx // rows)

    async def setup(self):
        await self.sync_vpns()
        await self.netmgr.watch_devices(self.dev_state_changed)

    async def sync_vpns(self):
        active_vpns = await self.netmgr.list_vpns()
        logging.info(f"Active VPNs: {active_vpns}")
        for vpn in active_vpns:
            if rb := self.buttons.get(vpn):
                rb.setChecked(True)

    def dev_state_changed(self, name, old: str | int | None, new: str | int, reason: str | int):
        if new == "ACTIVATED":
            logging.info(f"Device {name} activated")
            if event := self.activation_events.get(name):
                logging.info(f"Device {name} activated, notifying {event}")
                event.set()
            else:
                logging.warning(f"Device {name} activated but nothing listens")
        elif (old, new) == ("ACTIVATED", "UNMANAGED"):
            if event := self.deactivation_events.get(name):
                logging.info(f"Device {name} de-activated, notifying {event}")
                event.set()
            else:
                logging.warning(f"Device {name} de-activated but nothing listens")
        else:
            logging.debug(f"Device {name} {old} -> {new} reason:{reason}")

    async def activate_vpn(self, name: str) -> bool:
        event = self.activation_events[name] = asyncio.Event()
        logging.info(f"Registered activation event {event} for {name}")

        if res := await self.run_wg_quick(name, True):
            await event.wait()
            logging.info(f"Success: {name} fully activated")

        self.activation_events.pop(name, None)

        return res

    async def deactivate_vpn(self, name: str):
        event = self.deactivation_events[name] = asyncio.Event()
        logging.info(f"Registered de-activation event {event} for {name}")

        if await self.run_wg_quick(name, False):
            await event.wait()
            logging.info(f"Success: {name} fully de-activated")

        self.deactivation_events.pop(name, None)

    @staticmethod
    async def run_wg_quick(name: str, up: bool) -> bool:
        args = ["sudo", "wg-quick", "up" if up else "down", name]
        p = await asyncio.create_subprocess_exec(*args, stdout=PIPE, stderr=PIPE)
        out, err = await p.communicate()

        output = "\n".join(stream.decode().strip() for stream in (out, err))

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

    async def list_vpns(self) -> list[str]:
        iface = await self._bus.iface(self.SERVICE, self.BASE_PATH, self.SERVICE)
        devices = await iface.call_get_devices()

        vpn_devices = []
        for path in devices:
            dev_iface = await self._bus.iface(self.SERVICE, path, f"{self.SERVICE}.Device")
            driver = await dev_iface.get_driver()
            if driver == "wireguard":
                vpn_devices.append(await dev_iface.get_interface())

        return vpn_devices

    async def watch_devices(self, dev_state_changed: T_DEV_STATE_HANDLER):
        iface = await self._bus.iface(self.SERVICE, self.BASE_PATH, self.SERVICE)
        iface.on_device_added(
            lambda path: self._tw.start_task(self._added(path, dev_state_changed))
        )
        iface.on_device_removed(lambda path: logging.debug(f"NM: removed {path}"))

    async def _added(self, path: str, dev_state_changed: T_DEV_STATE_HANDLER):
        logging.debug(f"NM: added {path}")

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
