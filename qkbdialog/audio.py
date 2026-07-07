#!/usr/bin/env python3

import asyncio
import json
import logging
import re
import signal
import sys
from functools import cached_property
from subprocess import PIPE

from dbus_next import BusType
from dbus_next.aio import MessageBus
from PyQt6.QtCore import QProcess, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from qasync import QEventLoop

logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(asctime)s %(message)s")
log = logging.info


class Sink:
    def __init__(self, data: dict):
        self._data = data

    @cached_property
    def name(self) -> str:
        return self._data["name"]

    @cached_property
    def description(self) -> str:
        return self._data["description"]

    @cached_property
    def available(self) -> bool:
        return (act_port := self._data.get("active_port")) and any(
            p["name"] == act_port and p["availability"] != "not available"
            for p in self._data.get("ports") or []
        )


class BTDevice:
    def __init__(self, dbus_path: str, mac: str, name: str | None):
        self.id = dbus_path
        self.mac = mac
        self.name = name


class AudioOutput:
    def __init__(self, sink: Sink | None = None, bt_dev: BTDevice | None = None):
        self.sink = sink
        self.bt_dev = bt_dev
        self.is_default = False

    @property
    def id(self) -> str:
        if self.bt_dev:
            return f"bt:{self.bt_dev.mac}"

        return f"sink:{self.sink.name}"


class BTDeviceManager:
    def __init__(self, bus: MessageBus):
        self.bus = bus

        self._event = asyncio.Event()
        self._devices_dedup = set()

    async def start(self):
        root_intro = await self.bus.introspect("org.bluez", "/")
        manager = self.bus.get_proxy_object("org.bluez", "/", root_intro).get_interface(
            "org.freedesktop.DBus.ObjectManager"
        )
        manager.on_interfaces_added(self.iface_added)
        manager.on_interfaces_removed(lambda p, ii: log(f"dbus removed: {p}({sorted(ii)})"))

        objects = await manager.call_get_managed_objects()
        for path, obj_ifaces in objects.items():
            if dev := obj_ifaces.get("org.bluez.Device1"):
                self.notify_new_device(path, dev)
            elif adapter := obj_ifaces.get("org.bluez.Adapter1"):
                self.notify_adapter(adapter)

    def notify_new_device(self, path: str, dev: dict):
        name = None
        if name_var := dev.get("Name"):
            name = name_var.value

        self.on_new_device(BTDevice(dbus_path=path, mac=dev["Address"].value, name=name))
        self._devices_dedup.add(path)

    def notify_adapter(self, adapter):
        log(f"New adapter: {adapter}")
        self.on_adapter_state_change(True)

    def iface_added(self, path: str, obj_ifaces: dict):
        log(f"dbus added: {path}({sorted(obj_ifaces)})")
        if dev := obj_ifaces.get("org.bluez.Device1"):
            if path not in self._devices_dedup:
                self.notify_new_device(path, dev)
        elif adapter := obj_ifaces.get("org.bluez.Adapter1"):
            self.notify_adapter(adapter)

    async def activate_adapter(self):
        p = await asyncio.create_subprocess_exec("rfkill", ["unblock", "bluetooth"])
        await p.wait()

    def on_new_device(self, dev: BTDevice): ...
    def on_adapter_state_change(self, state: bool): ...


class MenuDialog(QWidget):
    PACTL_EVENT_RX = re.compile(b"^Event '(new|remove|change)' on (card|sink) #[0-9]+")

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app

        self.setWindowTitle("Choose audio output")
        self.setWindowFlag(Qt.WindowType.Dialog)

        self.layout = QVBoxLayout(self)

        self.rows: list[QLabel] = []
        self.audio_outputs: dict[str, AudioOutput] = {}

        self.pactl_subscribe: QProcess | None = None
        self.sinks_timer: QTimer | None = None
        self.sysbus: MessageBus | None = None

        self.done_event = asyncio.Event()

    async def run(self):
        asyncio.get_running_loop().add_signal_handler(signal.SIGINT, self.on_exit)
        self.start_watcher()

        await self.update_sinks()
        self.show()

        self.sysbus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        btdev_mgr = BTDeviceManager(self.sysbus)
        btdev_mgr.on_new_device = self.on_new_bt
        await btdev_mgr.start()

        await self.done_event.wait()

    async def update_sinks(self):
        sinks_buf, defsink_buf = await asyncio.gather(
            cmd_output("pactl", "--format=json", "list", "sinks"),
            cmd_output("pactl", "get-default-sink"),
        )
        available_sinks = {s.name: s for s in map(Sink, json.loads(sinks_buf)) if s.available}
        default_sink = defsink_buf.decode().rstrip("\n")

        self.audio_outputs = {
            i: o
            for i, o in self.audio_outputs.items()
            if o.bt_dev or (o.sink and o.sink.name in available_sinks)
        }

        for sink in available_sinks.values():
            bt_matches = [
                o
                for o in self.audio_outputs.values()
                if o.bt_dev and o.bt_dev.mac.replace(":", "_").upper() in sink.name.upper()
            ]
            if bt_matches:
                o = bt_matches[0]
                o.sink = sink
            else:
                o = AudioOutput(sink=sink)
                self.audio_outputs[o.id] = o

            o.is_default = sink.name == default_sink

        for o in self.audio_outputs.values():
            if o.bt_dev and o.sink and o.sink.name not in available_sinks:
                o.sink = None
                o.is_default = False

        self.update_ui()

    def update_ui(self):
        for row in self.rows:
            self.layout.removeWidget(row)
            row.deleteLater()

        self.rows = []

        for o in self.audio_outputs.values():
            if o.sink:
                label = o.sink.description
            elif o.bt_dev:
                label = f"BT(off): {o.bt_dev.name or o.bt_dev.mac}"

            if o.is_default:
                label = f"<b>{label}</b>"

            widget = QLabel(label, parent=self)
            self.layout.addWidget(widget)
            self.rows.append(widget)

    def start_watcher(self):
        self.pactl_subscribe = QProcess(self)
        self.pactl_subscribe.setProgram("pactl")
        self.pactl_subscribe.setArguments(["subscribe"])
        self.pactl_subscribe.readyReadStandardOutput.connect(self.on_pactl_event)

        self.sinks_timer = QTimer(self)
        self.sinks_timer.setSingleShot(True)
        self.sinks_timer.setInterval(50)
        self.sinks_timer.timeout.connect(lambda: asyncio.create_task(self.update_sinks()))

        self.pactl_subscribe.start()

    def on_pactl_event(self):
        out = self.pactl_subscribe.readAllStandardOutput()
        for line in out.data().splitlines():
            if self.PACTL_EVENT_RX.match(line):
                self.sinks_timer.start()

    def on_new_bt(self, dev: BTDevice):
        # TODO: fix extremely unlikely scenario, where sink is already present
        # when adding new BTDevice
        new_output = AudioOutput(bt_dev=dev)
        self.audio_outputs[new_output.id] = new_output
        self.update_ui()

    def closeEvent(self, ev):
        log(f"CloseEvent: {ev}")
        self.on_exit()
        ev.accept()

    def on_exit(self):
        log("Cleanup on_exit()")

        if (
            self.pactl_subscribe
            and self.pactl_subscribe.state() != QProcess.ProcessState.NotRunning
        ):
            self.pactl_subscribe.terminate()
            self.pactl_subscribe.waitForFinished(1000)

        if self.sysbus:
            self.sysbus.disconnect()
            log(f"Disconnected {self.sysbus}")

        self.done_event.set()


async def cmd_output(program: str, *args) -> bytes:
    p = await asyncio.create_subprocess_exec(program, *args, stdout=PIPE)
    out, _ = await p.communicate()
    return out


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setDesktopFileName("qkb-audio")
    menu = MenuDialog(app)

    asyncio.run(menu.run(), loop_factory=QEventLoop)
