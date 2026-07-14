#!/usr/bin/env python3

import asyncio
import json
import logging
import re
import sys
from collections import defaultdict
from collections.abc import Callable
from functools import cached_property, wraps
from signal import SIGINT
from subprocess import PIPE, Popen
from subprocess import run as run_cmd

from dbus_next import BusType
from dbus_next.aio import MessageBus
from dbus_next.errors import DBusError
from PyQt6.QtCore import QObject, QProcess, Qt, QTimer, pyqtBoundSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QGridLayout, QLabel,
                             QProgressBar, QPushButton, QRadioButton,
                             QVBoxLayout, QWidget)
from qasync import QEventLoop

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.info


def connect(sig: pyqtBoundSignal, slot: Callable):
    @wraps(slot)
    def wrapped(*args, **kwargs):
        try:
            return slot(*args, **kwargs)
        except Exception as e:
            logging.exception(f"{type(e).__name__}({e})")

    sig.connect(wrapped)


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
        self.mac = mac.replace(":", "_").upper()
        self.name = name

    def __str__(self):
        return f"BTDev<{self.mac}({self.name!r})>"


class BTManager:
    BUS_NAME = "org.bluez"
    DEVICE_IFACE = "org.bluez.Device1"
    ADAPTER_IFACE = "org.bluez.Adapter1"

    def __init__(self, bus: MessageBus):
        self.bus = bus

        self._ifaces: dict[str, set] = defaultdict(set)

        # TODO: adapt for multiple adapters (xD)
        self._adapter_path: str | None = None
        self._adapter_ready = asyncio.Event()

    async def start(self):
        root_intro = await self.bus.introspect(self.BUS_NAME, "/")
        manager = self.bus.get_proxy_object(self.BUS_NAME, "/", root_intro).get_interface(
            "org.freedesktop.DBus.ObjectManager"
        )
        manager.on_interfaces_added(self.iface_added)
        manager.on_interfaces_removed(self.iface_removed)

        objects = await manager.call_get_managed_objects()
        for path, obj_ifaces in objects.items():
            self.iface_added(path, obj_ifaces)

    def notify_new_device(self, path: str, dev: dict):
        name = None
        if name_var := dev.get("Name"):
            name = name_var.value

        self.on_new_device(BTDevice(dbus_path=path, mac=dev["Address"].value, name=name))

    def notify_adapter(self, path, adapter):
        log(f"New adapter at {path}: {adapter['Address']}")

        self._adapter_path = path
        self._adapter_ready.set()
        self.on_adapter_state_change(True)

    def iface_added(self, path: str, obj_ifaces: dict):
        log(f"dbus added: {path}({sorted(obj_ifaces)})")

        if dev := obj_ifaces.get(self.DEVICE_IFACE):
            if path not in self._ifaces:
                self.notify_new_device(path, dev)
        elif adapter := obj_ifaces.get(self.ADAPTER_IFACE):
            self.notify_adapter(path, adapter)

        self._ifaces[path].update(obj_ifaces)

    def iface_removed(self, path: str, obj_ifaces):
        self._ifaces[path].difference_update(obj_ifaces)
        if new := self._ifaces[path]:
            log(f"dbus removed: {path} - {sorted(obj_ifaces)} = {sorted(new)}")
        else:
            log(f"dbus removed completely: {path}")
            del self._ifaces[path]
            if path == self._adapter_path:
                self._adapter_ready.clear()
                self.on_adapter_state_change(False)

    def activate_adapter(self):
        run_cmd(["rfkill", "unblock", "bluetooth"], check=True)

    async def connect_device(self, dev: BTDevice):
        log("Waiting for BT adapter ...")
        self.activate_adapter()
        await self._adapter_ready.wait()
        log("BT adapter ready")

        intro = await self.bus.introspect(self.BUS_NAME, dev.id)
        iface = self.bus.get_proxy_object(self.BUS_NAME, dev.id, intro).get_interface(
            self.DEVICE_IFACE
        )
        log(f"Calling {dev}.{iface.call_connect} ...")
        await iface.call_connect()

    def on_new_device(self, dev: BTDevice): ...
    def on_adapter_state_change(self, state: bool): ...


class SinkManager(QObject):
    EVENT_REGEX = re.compile(b"^Event '(new|remove|change)' on (card|sink) #[0-9]+")

    def __init__(self, parent):
        super().__init__(parent)

        self.watcher = QProcess(self)
        self.watcher.setProgram("pactl")
        self.watcher.setArguments(["subscribe"])
        connect(self.watcher.readyReadStandardOutput, self._on_pactl_event)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(50)
        connect(self.timer.timeout, self._update_sinks)

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

    def on_sinks_changed(self, added: list[Sink], removed: set[str], new_default: str | None): ...


class Keymap:
    def __init__(self, parent: QWidget):
        self._parent = parent
        self._shortcuts: dict[str, QShortcut | None] = {
            c: None
            for r in (("A", "Z"), ("0", "9"))
            for c in map(chr, range(ord(r[0]), ord(r[1]) + 1))
        }

    def bind(self, action: Callable[[], None], key: str, /, force=False):
        if self._shortcuts[key] is not None:
            if force:
                self.unbind(key)
            else:
                raise ValueError(f"Shortcut {key!r} is already bound")

        self._bind(key, action)

    def bind_available(self, action: Callable[[], None]) -> str:
        for k in self._shortcuts:
            if self._shortcuts[k] is None:
                log(f"Found free shortcut key: {k!r}")
                self._bind(k, action)

                return k

        raise ValueError("All available shortcuts are in use")

    def unbind(self, key: str):
        if key not in self._shortcuts:
            return

        if s := self._shortcuts[key]:
            log(f"Unbinding {key!r} (deleting {s})")
            s.deleteLater()
            self._shortcuts[key] = None

    def _bind(self, key: str, action: Callable[[], None]):
        s = self._shortcuts[key] = QShortcut(QKeySequence(key), self._parent)
        log(f"Binding {key!r} to {action}: {s}")
        s.setContext(Qt.ShortcutContext.WindowShortcut)
        connect(s.activated, action)


class AudioOutput(QWidget):
    def __init__(
        self, parent: QWidget, *, sink: Sink | None = None, bt_dev: BTDevice | None = None
    ):
        if not (sink or bt_dev):
            raise ValueError(
                f"At least one of `sink` or `bt_dev` must be specified for {type(self).__name__}"
            )

        super().__init__(parent)

        self._sink = sink
        self._bt_dev = bt_dev
        self.key: str | None = None

        self.button = QRadioButton(self.label, self)
        self._shortcut_label = QLabel(self)

    def match_sink(self, sink: Sink) -> bool:
        if self.sink:
            return self.sink.name == sink.name

        if self.bt_dev:
            return self.bt_dev.mac in sink.name.upper()

        return False

    def match_bt(self, bt_dev: BTDevice) -> bool:
        if self.bt_dev:
            return self.bt_dev.mac == bt_dev.mac

        if self.sink:
            return bt_dev.mac in self.sink.name.upper()

        return False

    def add_to_grid(self, grid: QGridLayout, row: int):
        grid.addWidget(self._shortcut_label, row, 0)
        grid.addWidget(self.button, row, 1)

    def set_key(self, key: str):
        self.key = key
        self._shortcut_label.setText(f"[{key}]")

    @property
    def label(self) -> str:
        if self.bt_dev:
            return self.bt_dev.name

        return self.sink.description

    @property
    def sink(self) -> Sink | None:
        return self._sink

    @sink.setter
    def sink(self, new_sink: Sink | None):
        if new_sink:
            log(f"Assigning new sink {new_sink} to {self}")
        else:
            log(f"Removing sink from {self}")

        self._sink = new_sink

    @property
    def bt_dev(self) -> BTDevice | None:
        return self._bt_dev

    @bt_dev.setter
    def bt_dev(self, new_bt_dev: BTDevice | None):
        if new_bt_dev:
            log(f"Assigning new bt_dev {new_bt_dev} to {self}")
        else:
            log(f"Removing bt_dev from {self}")

        self._bt_dev = new_bt_dev

    def __str__(self):
        return f"AudioOutput<sink={self._sink} bt_dev={self._bt_dev}>"

    def __repr__(self):
        return str(self)

    def __lt__(self, o: AudioOutput):
        if not isinstance(o, AudioOutput):
            return NotImplemented

        if (self_bt := bool(self._bt_dev)) != (o_bt := bool(o._bt_dev)):
            return self_bt < o_bt

        return self.label < o.label


class MenuDialog(QWidget):
    KEY_QUIT = "Q"
    KEY_ENABLE_BT = "B"

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app

        self.setWindowTitle("Choose audio output")
        self.setWindowFlag(Qt.WindowType.Dialog)

        self.button_group = QButtonGroup(self)
        self.audio_outputs: list[AudioOutput] = []
        self.keymap = Keymap(self)

        self.sink_mgr = SinkManager(self)
        self.sysbus: MessageBus | None = None
        self.bt_mgr: BTManager | None = None

        self._output_activation_task: asyncio.Task | None = None
        self._esc_shortcut = QShortcut(QKeySequence("ESC"), self)
        self._esc_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._esc_shortcut.setEnabled(False)
        connect(self._esc_shortcut.activated, self._cancel_output_activation_request)

        self.top_layout = QVBoxLayout(self)
        self.bt_activate_button = QPushButton(f"Enable BT ({self.KEY_ENABLE_BT})", self)
        connect(self.bt_activate_button.clicked, self.activate_bt)
        self.show_bt_button()
        self.loader = QProgressBar(self)
        self.loader.setRange(0, 0)
        self.loader.hide()

        self.grid = QGridLayout()

        self.top_layout.addLayout(self.grid)
        self.top_layout.addWidget(self.bt_activate_button)
        self.top_layout.addWidget(self.loader)

        self.done_event = asyncio.Event()

    async def run(self):
        asyncio.get_running_loop().add_signal_handler(SIGINT, self.done_event.set)

        try:
            self.sink_mgr.on_sinks_changed = self.on_sinks_changed
            self.sink_mgr.start()

            self.sysbus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            self.bt_mgr = BTManager(self.sysbus)
            self.bt_mgr.on_new_device = self.on_new_bt
            self.bt_mgr.on_adapter_state_change = self.on_bt_state_change
            await self.bt_mgr.start()

            self.keymap.bind(self.done_event.set, self.KEY_QUIT, force=True)

            self.show()

            await self.done_event.wait()
        finally:
            log("Cleanup...")

            self.sink_mgr.stop()

            self._cancel_output_activation_request()

            if self.sysbus:
                self.sysbus.disconnect()
                log(f"Disconnected {self.sysbus}")

    def on_sinks_changed(self, added: list[Sink], removed: set[str], new_default: str | None):
        for o in list(self.audio_outputs):
            if o.sink and o.sink.name in removed:
                if o.bt_dev:
                    o.sink = None
                else:
                    self.keymap.unbind(o.key)
                    o.deleteLater()
                    self.audio_outputs.remove(o)

        for sink in added:
            if not self._assign_sink(sink):
                self._add_output(sink=sink)

        if new_default is not None:
            for o in self.audio_outputs:
                if o.sink and o.sink.name == new_default:
                    o.button.setChecked(True)

        self._update_ui()

    def on_new_bt(self, bt_dev: BTDevice):
        for o in self.audio_outputs:
            if o.match_bt(bt_dev):
                o.bt_dev = bt_dev
                return

        self._add_output(bt_dev=bt_dev)
        self._update_ui()

    def on_bt_state_change(self, enabled: bool):
        if enabled:
            self._disable_loader()
            self.hide_bt_button()
        elif any(o.bt_dev for o in self.audio_outputs):
            log("BT adapter disabled, but devices present")
            self.hide_bt_button()
        else:
            log("BT adapter disabled")
            self.show_bt_button()

    def activate_bt(self, checked=False):
        self.bt_mgr.activate_adapter()
        self.hide_bt_button()
        self._enable_loader()

    def show_bt_button(self):
        self.bt_activate_button.show()
        for o in self.audio_outputs:
            if o.key == self.KEY_ENABLE_BT:
                self.keymap.unbind(self.KEY_ENABLE_BT)
                key = self._bind_output(o)
                o.set_key(key)
                break

        self.keymap.bind(self.bt_activate_button.animateClick, self.KEY_ENABLE_BT)

    def hide_bt_button(self):
        self.bt_activate_button.hide()
        self.keymap.unbind(self.KEY_ENABLE_BT)

    def _update_ui(self):
        self.audio_outputs.sort()

        for o in self.audio_outputs:
            self.keymap.unbind(o.key)

        while self.grid.count():
            self.grid.takeAt(0)

        for row, o in enumerate(self.audio_outputs):
            key = self._bind_output(o)
            o.set_key(key)
            o.add_to_grid(self.grid, row)

        log("Update UI finished")

    def _add_output(self, *, sink: Sink | None = None, bt_dev: BTDevice | None = None):
        o = AudioOutput(self, sink=sink, bt_dev=bt_dev)

        self.button_group.addButton(o.button)
        self.audio_outputs.append(o)

        log(f"Added to UI: {o}")

    def _bind_output(self, o: AudioOutput) -> str:
        return self.keymap.bind_available(lambda: self._request_activate_output(o))

    def _request_activate_output(self, o: AudioOutput):
        self._cancel_output_activation_request()
        self._output_activation_task = asyncio.create_task(self._activate_output(o))

    def _cancel_output_activation_request(self):
        if self._output_activation_task:
            log(f"Cancelling {self._output_activation_task}")
            self._output_activation_task.cancel()
            self._output_activation_task = None
        else:
            log("Skipping task cancellation")

    async def _activate_output(self, o: AudioOutput):
        self._enable_loader()
        self._esc_shortcut.setEnabled(True)
        try:
            if o.sink:
                p = await asyncio.create_subprocess_exec(
                    "pactl", ["set-default-sink", o.sink.name]
                )
                await p.wait()
            elif o.bt_dev:
                log(f"{o} does not have a sink, trying to connect BT device ...")
                await self.bt_mgr.connect_device(o.bt_dev)
        except asyncio.CancelledError:
            log(f"Cancelled {o} activation")
            raise
        except DBusError as e:
            log(f"Couldn't connect to {o.bt_dev}: {e.args} {e.reply!r} {e.text!r} {e.type}")
        finally:
            self._esc_shortcut.setEnabled(False)
            self._disable_loader()

    def _enable_loader(self):
        log("Enabling loader")
        self.loader.show()
        for o in self.audio_outputs:
            o.button.setDisabled(True)

    def _disable_loader(self):
        log("Disabling loader")
        self.loader.hide()
        for o in self.audio_outputs:
            o.button.setDisabled(False)

    def _assign_sink(self, sink: Sink) -> bool:
        for o in self.audio_outputs:
            if o.match_sink(sink):
                o.sink = sink
                return True

        return False

    def closeEvent(self, ev):
        log(f"CloseEvent: {ev}")
        ev.accept()
        self.done_event.set()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setDesktopFileName("qkb-audio")
    menu = MenuDialog(app)

    asyncio.run(menu.run(), loop_factory=QEventLoop)
