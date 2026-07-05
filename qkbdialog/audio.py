#!/usr/bin/env python3

import asyncio
import json
import logging
import re
import signal
import sys
from functools import cached_property
from subprocess import check_output

from dbus_next import BusType
from dbus_next.aio import MessageBus
from PyQt6.QtCore import QProcess, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from qasync import QEventLoop

logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(asctime)s %(message)s")


class MenuDialog(QWidget):
    PACTL_EVENT_RX = re.compile(b"^Event '(new|remove|change)' on (card|sink) #[0-9]+")

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app

        self.setWindowTitle("Choose audio output")
        self.setWindowFlag(Qt.WindowType.Dialog)

        self.layout = QVBoxLayout(self)
        self.sink_rows: dict[str, QLabel] = {}
        self.bt_rows: dict[str, QLabel] = {}
        self.done_event = asyncio.Event()
        self.pactl_subscribe: QProcess | None = None
        self.sinks_timer: QTimer | None = None
        self.sysbus: MessageBus | None = None

    async def run(self):
        asyncio.get_running_loop().add_signal_handler(signal.SIGINT, self.on_exit)
        self.start_watcher()

        self.update_sinks()
        self.show()

        self.sysbus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        btdev_mgr = BTDeviceManager(self.sysbus, self.on_new_bt, self.on_bt_removed)
        await btdev_mgr.start()

        await self.done_event.wait()
        logging.info("done_event waited successfully")

    def update_sinks(self):
        logging.info("updating sinks...")
        new_sinks = {
            s.name: s.description
            for s in map(
                Sink, json.loads(check_output(["pactl", "--format=json", "list", "sinks"]))
            )
            if s.available
        }
        default_sink = check_output(["pactl", "get-default-sink"], encoding="utf-8").rstrip("\n")

        removed = set(self.sink_rows).difference(new_sinks)
        for name in removed:
            old = self.sink_rows.pop(name)
            self.layout.removeWidget(old)
            old.deleteLater()

        for name, description in new_sinks.items():
            if name == default_sink:
                description = f"<b>{description}</b>"

            if sink_row := self.sink_rows.get(name):
                sink_row.setText(description)
            else:
                sink_row = self.sink_rows[name] = QLabel(description)
                self.layout.addWidget(sink_row)

    def start_watcher(self):
        self.pactl_subscribe = QProcess(self)
        self.pactl_subscribe.setProgram("pactl")
        self.pactl_subscribe.setArguments(["subscribe"])
        self.pactl_subscribe.readyReadStandardOutput.connect(self.on_pactl_event)

        self.sinks_timer = QTimer(self)
        self.sinks_timer.setSingleShot(True)
        self.sinks_timer.setInterval(50)
        self.sinks_timer.timeout.connect(self.update_sinks)

        self.pactl_subscribe.start()

    def on_pactl_event(self):
        out = self.pactl_subscribe.readAllStandardOutput()
        for line in out.data().splitlines():
            if self.PACTL_EVENT_RX.match(line):
                self.sinks_timer.start()

    def on_new_bt(self, dev: dict):
        label = f"[BT] {dev['mac']}({dev['name']})"
        if row := self.bt_rows.get(dev["id"]):
            row.setText(label)
        else:
            row = self.bt_rows[dev["id"]] = QLabel(label)
            self.layout.addWidget(row)

    def on_bt_removed(self, dev_id: str):
        if row := self.bt_rows.pop(dev_id, None):
            self.layout.removeWidget(row)
            row.deleteLater()

    def closeEvent(self, ev):
        logging.info(f"CloseEvent: {ev}")
        self.on_exit()
        ev.accept()

    def on_exit(self):
        logging.info("Cleanup on_exit()")
        if (
            self.pactl_subscribe
            and self.pactl_subscribe.state() != QProcess.ProcessState.NotRunning
        ):
            self.pactl_subscribe.terminate()
            self.pactl_subscribe.waitForFinished(1000)

        if self.sysbus:
            self.sysbus.disconnect()
            logging.info(f"{self.sysbus} disconnected")

        self.done_event.set()


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
        a = (act_port := self._data.get("active_port")) and any(
            p["name"] == act_port and p["availability"] != "not available"
            for p in self._data.get("ports") or []
        )
        logging.info(f"sink {self.name}({self.description}) available: {a}")

        return a


class BTDeviceManager:
    def __init__(self, bus: MessageBus, on_new=None, on_removed=None):
        self.bus = bus
        self.on_new = on_new or (lambda dev: None)
        self.on_removed = on_removed or (lambda dev_id: None)

        self._event = asyncio.Event()
        self._devices_dedup = set()

    async def start(self):
        root_intro = await self.bus.introspect("org.bluez", "/")
        manager = self.bus.get_proxy_object("org.bluez", "/", root_intro).get_interface(
            "org.freedesktop.DBus.ObjectManager"
        )
        manager.on_interfaces_added(self.iface_added)
        manager.on_interfaces_removed(self.iface_removed)
        objects = await manager.call_get_managed_objects()
        for path, obj_ifaces in objects.items():
            if self.send_new(path, obj_ifaces):
                logging.info(f"Dev {path} already available")

    def send_new(self, path: str, obj: dict):
        if not (dev := obj.get("org.bluez.Device1")):
            return False

        name = None
        if name_var := dev.get("Name"):
            name = name_var.value

        self.on_new({"id": path, "mac": dev["Address"].value, "name": name})
        self._devices_dedup.add(path)

        return True

    def iface_added(self, path: str, obj_ifaces: dict):
        if path in self._devices_dedup:
            logging.info(f"Dev {path} appeared, duplicate")
        elif self.send_new(path, obj_ifaces):
            logging.info(f"Dev {path} appeared, notified")

    def iface_removed(self, path: str, ifaces: list):
        if path in self._devices_dedup:
            self.on_removed(path)
            self._devices_dedup.remove(path)
            logging.info(f"Dev {path} disappeared, notified")
        else:
            logging.info(f"Dev {path} disappeared, duplicate")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setDesktopFileName("qkb-audio")
    menu = MenuDialog(app)

    asyncio.run(menu.run(), loop_factory=QEventLoop)
