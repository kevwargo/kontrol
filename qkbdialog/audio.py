#!/usr/bin/env python3

import asyncio
import json
import logging
import re
import sys
from functools import cached_property
from signal import SIGINT
from subprocess import PIPE, Popen

from dbus_next import BusType
from dbus_next.aio import MessageBus
from PyQt6.QtCore import QObject, QProcess, Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QRadioButton,
                             QVBoxLayout, QWidget)
from qasync import QEventLoop

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
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

    def __str__(self):
        return f"Sink<{self.name}({self.description}) available:{self.available}>"


class BTDevice:
    def __init__(self, dbus_path: str, mac: str, name: str | None):
        self.id = dbus_path
        self.mac = mac
        self.name = name

    def __str__(self):
        return f"BTDev<{self.mac}({self.name!r})>"


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

    def __str__(self):
        return f"AudioOutput<sink={self.sink} bt_dev={self.bt_dev}>"


class BTManager:
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


class SinkManager(QObject):
    EVENT_REGEX = re.compile(b"^Event '(new|remove|change)' on (card|sink) #[0-9]+")

    def __init__(self, parent):
        super().__init__(parent)

        self.watcher = QProcess(self)
        self.watcher.setProgram("pactl")
        self.watcher.setArguments(["subscribe"])
        self.watcher.readyReadStandardOutput.connect(self._on_pactl_event)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._update_sinks)

        self._last_sinks: dict[str, Sink] = {}
        self._last_default: str | None = None

    def start(self):
        self._update_sinks()
        self.watcher.start()

    def stop(self):
        self.timer.stop()

        if self.watcher.state() != QProcess.ProcessState.NotRunning:
            self.watcher.terminate()
            self.watcher.waitForFinished(1000)

    def _on_pactl_event(self):
        out = self.watcher.readAllStandardOutput()
        for line in out.data().splitlines():
            if self.EVENT_REGEX.match(line):
                self.timer.start()

    def _update_sinks(self):
        proc_sinks = Popen(["pactl", "--format=json", "list", "sinks"], stdout=PIPE, stderr=PIPE)
        proc_defsink = Popen(["pactl", "get-default-sink"], stdout=PIPE, stderr=PIPE)

        sinks_buf, sinks_err = proc_sinks.communicate()
        if proc_sinks.returncode != 0:
            raise RuntimeError(f"{proc_sinks}: {sinks_err}")
        defsink_buf, defsink_err = proc_defsink.communicate()
        if proc_defsink.returncode != 0:
            raise RuntimeError(f"{proc_defsink}: {defsink_err}")

        available_sinks = {s.name: s for s in map(Sink, json.loads(sinks_buf)) if s.available}
        default_sink = defsink_buf.decode().rstrip("\n")

        added = [s for s in available_sinks.values() if s.name not in self._last_sinks]
        removed = set(self._last_sinks).difference(available_sinks)
        self._last_sinks = available_sinks

        if default_sink == self._last_default:
            default_sink = None
        else:
            self._last_default = default_sink

        self.on_sinks_changed(added, removed, default_sink)

    def on_sinks_changed(self, added: list[Sink], removed: set[str], default: str | None): ...


class AudioOutputRow(QRadioButton):
    def __init__(
        self,
        parent: QWidget,
        bt_mgr: BTManager,
        key: str,
        *,
        sink: Sink | None = None,
        bt_dev: BTDevice | None = None,
    ):
        if not (sink or bt_dev):
            raise ValueError(
                f"At least one of `sink` or `bt_dev` must be specified for {type(self).__name__}"
            )

        self.sink = sink
        self.bt_dev = bt_dev
        self.bt_mgr = bt_mgr
        self.key = key

        super().__init__(parent=parent, text=f"({key}) {self._label()}")

    def _label(self) -> str:
        if self.bt_dev:
            return self.bt_dev.name

        return self.sink.description

    def __str__(self):
        return f"AudioOutputRow<sink={self.sink} bt_dev={self.bt_dev}>"

    def __lt__(self, o):
        if not isinstance(o, type(self)):
            return NotImplemented

        if (self_bt := bool(self.bt_dev)) != (o_bt := bool(o.bt_dev)):
            return self_bt < o_bt

        return self._label() < o._label()


class MenuDialog(QWidget):

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app

        self.setWindowTitle("Choose audio output")
        self.setWindowFlag(Qt.WindowType.Dialog)

        self.layout = QVBoxLayout(self)
        self.button_group = QButtonGroup(self)
        self.audio_outputs: list[AudioOutputRow] = []
        self.unused_shortcuts = [
            c for (b, e) in (("A", "Z"), ("0", "9")) for c in map(chr, range(ord(b), ord(e) + 1))
        ]

        self.sink_mgr = SinkManager(self)
        self.sysbus: MessageBus | None = None
        self.bt_mgr: BTManager | None = None

        self.done_event = asyncio.Event()

    async def run(self):
        asyncio.get_running_loop().add_signal_handler(SIGINT, self.on_exit)

        self.sink_mgr.on_sinks_changed = self.on_sinks_changed
        self.sink_mgr.start()

        self.sysbus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self.bt_mgr = BTManager(self.sysbus)
        self.bt_mgr.on_new_device = self.on_new_bt
        await self.bt_mgr.start()

        self.show()

        await self.done_event.wait()

    def on_sinks_changed(self, added: list[Sink], removed: set[str], default: str | None):
        for o in list(self.audio_outputs):
            if o.bt_dev is None and o.sink.name in removed:
                o.deleteLater()
                self.unused_shortcuts.insert(0, o.key)
                log(f"Freeing (kinda) shortcut {o.key!r}")
                self.audio_outputs.remove(o)

        for sink in added:
            if not self._update_existing_output(sink):
                self._add_row(sink=sink)

        if default is not None:
            for o in self.audio_outputs:
                if o.sink and o.sink.name == default:
                    o.setChecked(True)

        self._update_ui()

    def on_new_bt(self, dev: BTDevice):
        # TODO: fix extremely unlikely scenario, where sink is already present
        # when adding new BTDevice
        for o in self.audio_outputs:
            if o.bt_dev and o.bt_dev.mac == dev.mac:
                log(f"Ignoring {dev} existing as {o}")
                return

        self._add_row(bt_dev=dev)
        self._update_ui()

    def _update_ui(self):
        self.audio_outputs.sort()

        while self.layout.count():
            self.layout.takeAt(0)
        for row in self.audio_outputs:
            self.layout.addWidget(row)

        log("Update UI finished")

    def _add_row(self, *, sink: Sink | None = None, bt_dev: BTDevice | None = None):
        key = self.unused_shortcuts.pop(0)
        row = AudioOutputRow(self, self.bt_mgr, key, sink=sink, bt_dev=bt_dev)

        self.button_group.addButton(row)

        log(f"Grabbing shortcut {key!r}")
        s = QShortcut(QKeySequence(key), row)
        s.activated.connect(row.animateClick)
        row.toggled.connect(lambda checked: self._on_row_toggled(row, checked))

        self.audio_outputs.append(row)

        log(f"Added to UI: {row}")

    def _on_row_toggled(self, row: AudioOutputRow, checked: bool):
        log(f"{row} checked:{checked}")

    def _update_existing_output(self, sink: Sink) -> bool:
        for o in self.audio_outputs:
            if o.sink and sink.name == o.sink.name:
                log(f"{o} matched by sink name for {sink}")
            elif o.bt_dev and o.bt_dev.mac.replace(":", "_").upper() in sink.name.upper():
                log(f"{o} matched by BT dev MAC for {sink}")
            else:
                continue

            o.sink = sink

            return True

        return False

    def closeEvent(self, ev):
        log(f"CloseEvent: {ev}")
        self.on_exit()
        ev.accept()

    def on_exit(self):
        log("Cleanup on_exit()")

        self.sink_mgr.stop()

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
