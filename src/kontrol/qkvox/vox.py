import asyncio
import json
import logging
import os
import re
import sys
from collections import defaultdict
from collections.abc import Callable
from functools import cached_property, wraps
from signal import SIGINT
from subprocess import PIPE, CalledProcessError, Popen
from subprocess import run as run_cmd
from typing import Iterator, get_type_hints

from dbus_next import BusType
from dbus_next.aio import MessageBus
from dbus_next.errors import DBusError
from PyQt6.QtCore import (QObject, QProcess, Qt, QTimer, pyqtBoundSignal,
                          pyqtSignal)
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QGridLayout, QLabel,
                             QProgressBar, QPushButton, QRadioButton,
                             QVBoxLayout, QWidget)
from qasync import QEventLoop

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", logging.INFO),
    format="%(asctime)s | [%(levelname)s] %(message)s",
)


def main():
    app = QApplication(sys.argv)
    app.setDesktopFileName("qkvox")
    menu = MenuDialog(app)

    asyncio.run(menu.run(), loop_factory=QEventLoop)


def connect(sig: pyqtBoundSignal, slot: Callable):
    @wraps(slot)
    def wrapped(*args, **kwargs):
        try:
            return slot(*args, **kwargs)
        except Exception as e:
            logging.exception(f"{type(e).__name__}({e})")

    sig.connect(wrapped)


def multi_command(*commands: list[list[str]]) -> Iterator[bytes]:
    for p in [Popen(cmd, stdout=PIPE, stderr=PIPE) for cmd in commands]:
        out, err = p.communicate()
        if p.returncode:
            raise CalledProcessError(p.returncode, p.args, output=out, stderr=err)

        yield out


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
        availability = "" if self.available else "not available"
        return f"Sink<{self.name}({self.description}){availability}>"

    __repr__ = __str__


class QDataclass:
    def __init_subclass__(cls, /, **kwargs):
        super().__init_subclass__(**kwargs)

        emit_signal = issubclass(cls, QObject) and isinstance(
            cls.__dict__.get("props_changed"), pyqtSignal
        )

        prop_defaults = cls.__get_prop_defaults()
        cls.__wrap_init(prop_defaults, emit_signal)
        for p in prop_defaults:
            cls.__define_prop(p, emit_signal)

    @classmethod
    def __define_prop(cls, name: str, emit_signal: bool):
        def fget(o):
            return getattr(o, f"_{name}")

        setter_name = f"_set_{name}"
        orig_setter = cls.__dict__.get(setter_name, lambda self, val: None)

        def fset_signal(self, val):
            if (old_val := getattr(self, f"_{name}")) != val:
                setattr(self, f"_{name}", val)
                logging.info(f"changed {self}.{name}: {old_val} -> {val}")
                self._props_changed_timer.start()

            orig_setter(self, val)

        def fset_basic(self, val):
            setattr(self, f"_{name}", val)
            orig_setter(self, val)

        fset = fset_signal if emit_signal else fset_basic

        setattr(cls, setter_name, fset)
        setattr(cls, name, property(fget=fget, fset=fset))

    @classmethod
    def __get_prop_defaults(cls) -> dict:
        return {
            p: cls.__dict__.get(p)
            for p, t in get_type_hints(cls).items()
            if not p.startswith("_") and isinstance(t, type) and p not in ("parent",)
        }

    @classmethod
    def __wrap_init(cls, prop_defaults: dict, emit_signal: bool):
        orig = cls.__init__

        @wraps(orig)
        def wrapped(self, *args, **kwargs):
            for p, v in prop_defaults.items():
                setattr(self, f"_{p}", kwargs.pop(p, v))

            orig(self, *args, **kwargs)

            if emit_signal:
                self._props_changed_timer = QTimer(self)
                self._props_changed_timer.setInterval(20)
                self._props_changed_timer.setSingleShot(True)
                connect(self._props_changed_timer.timeout, self.props_changed.emit)

        cls.__init__ = wrapped


class BTDevice(QObject, QDataclass):
    props_changed = pyqtSignal()

    id: str
    mac: str
    name: str
    connected: bool

    def __str__(self):
        return f"BTDev<{self.mac}({self.name!r}){self.state_label}>"

    @property
    def state_label(self) -> str:
        return " [ON]" if self.connected else " [OFF]"

    def match_sink(self, sink: Sink) -> bool:
        return self.mac.replace(":", "_").upper() in sink.name.upper()


class AsyncTaskSupervisor:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__tasks: set[asyncio.Task] = set()

    def as_task(self, fn):
        return lambda *args, **kwargs: self.__start_task(fn(*args, **kwargs))

    async def cleanup(self):
        if not self.__tasks:
            return

        for task in self.__tasks:
            task.cancel()

        await asyncio.gather(*self.__tasks, return_exceptions=True)

    def __start_task(self, coro):
        task = asyncio.create_task(coro)
        self.__tasks.add(task)
        task.add_done_callback(self.__task_done)

    def __task_done(self, task: asyncio.Task):
        self.__tasks.discard(task)

        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logging.exception()


class BTManager(AsyncTaskSupervisor):
    BUS_NAME = "org.bluez"
    DEVICE_IFACE = "org.bluez.Device1"
    ADAPTER_IFACE = "org.bluez.Adapter1"

    def __init__(self, bus: MessageBus):
        super().__init__()

        self.bus = bus

        self._ifaces: dict[str, set] = defaultdict(set)
        self._devices: dict[str, BTDevice] = {}

        # TODO: adapt for multiple adapters (xD)
        self._adapter_path: str | None = None
        self._adapter_ready = asyncio.Event()

        self._tasks: set[asyncio.Task] = set()

    async def start(self):
        root_intro = await self.bus.introspect(self.BUS_NAME, "/")
        manager = self.bus.get_proxy_object(self.BUS_NAME, "/", root_intro).get_interface(
            "org.freedesktop.DBus.ObjectManager"
        )
        manager.on_interfaces_added(self.as_task(self.iface_added))
        manager.on_interfaces_removed(self.as_task(self.iface_removed))

        objects = await manager.call_get_managed_objects()
        for path, obj_ifaces in objects.items():
            await self.iface_added(path, obj_ifaces)

    async def notify_device(self, path: str):
        dev = self._devices.get(path)

        if dev and not self._ifaces.get(path):
            logging.info(f"{dev} disappeared")
            dev.connected = False
            return

        intro = await self.bus.introspect(self.BUS_NAME, path)
        iface = self.bus.get_proxy_object(self.BUS_NAME, path, intro).get_interface(
            self.DEVICE_IFACE
        )

        name = await iface.get_name()
        address = await iface.get_address()
        if not await iface.get_paired():
            logging.debug(f"Ignoring unpaired BT device {address}({name})")
            return

        connected = await iface.get_connected()

        if dev:
            dev.name = name
            dev.address = address
            dev.connected = connected
        else:
            self._devices[path] = BTDevice(id=path, name=name, mac=address, connected=connected)
            self.on_new_device(self._devices[path])

    def notify_adapter(self, path, adapter):
        logging.info(f"New adapter at {path}: {adapter['Address']}")

        self._adapter_path = path
        self._adapter_ready.set()
        self.on_adapter_state_change(True)

    async def iface_added(self, path: str, new_ifaces: dict):
        self._ifaces[path].update(new_ifaces)
        logging.debug(f"dbus added: {path} + {sorted(new_ifaces)} = {sorted(self._ifaces[path])}")

        if self.DEVICE_IFACE in self._ifaces[path]:
            await self.notify_device(path)
        elif adapter := new_ifaces.get(self.ADAPTER_IFACE):
            self.notify_adapter(path, adapter)

    async def iface_removed(self, path: str, removed_ifaces):
        self._ifaces[path].difference_update(removed_ifaces)

        if not self._ifaces[path]:
            if path == self._adapter_path:
                self._adapter_ready.clear()
                self.on_adapter_state_change(False)

        if path in self._devices:
            await self.notify_device(path)

    def activate_adapter(self):
        run_cmd(["rfkill", "unblock", "bluetooth"], check=True)

    async def connect_device(self, dev: BTDevice):
        logging.info("Waiting for BT adapter ...")
        self.activate_adapter()
        await self._adapter_ready.wait()
        logging.info("BT adapter ready")

        intro = await self.bus.introspect(self.BUS_NAME, dev.id)
        iface = self.bus.get_proxy_object(self.BUS_NAME, dev.id, intro).get_interface(
            self.DEVICE_IFACE
        )
        logging.debug(f"Calling {dev}.Connect() ...")
        await iface.call_connect()

    def on_new_device(self, dev: BTDevice): ...
    def on_adapter_state_change(self, state: bool): ...


class SinkManager(QObject):
    EVENT_REGEX = re.compile(b"^Event '(new|remove|change)' on (card|sink(-input)?) #[0-9]+")

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
        sinks_buf, defsink_buf = multi_command(
            ["pactl", "--format=json", "list", "sinks"], ["pactl", "get-default-sink"]
        )
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
                logging.debug(f"Found free shortcut key: {k!r}")
                self._bind(k, action)

                return k

        raise ValueError("All available shortcuts are in use")

    def unbind(self, key: str):
        if key not in self._shortcuts:
            return

        if s := self._shortcuts[key]:
            logging.debug(f"Unbinding {key!r} (deleting {s})")
            s.deleteLater()
            self._shortcuts[key] = None

    def _bind(self, key: str, action: Callable[[], None]):
        s = self._shortcuts[key] = QShortcut(QKeySequence(key), self._parent)
        logging.debug(f"Binding {key!r} to {action}: {s}")
        s.setContext(Qt.ShortcutContext.WindowShortcut)
        connect(s.activated, action)


class AudioOutput(QDataclass):
    sink: Sink
    bt_dev: BTDevice
    shortcut: str

    def __init__(self, parent: QWidget):
        if not (self.sink or self.bt_dev):
            raise ValueError(
                f"At least one of `sink` or `bt_dev` must be specified for {type(self).__name__}"
            )

        self._shortcut_label = QLabel(parent)
        self._shortcut_label.hide()
        self.button = QRadioButton(self._label, parent)

        if self.bt_dev:
            connect(self.bt_dev.props_changed, self._update_label)
            logging.info(f"Connected initial bt_dev.props_changed to {self}._update_label")

    def deleteLater(self):
        self._shortcut_label.deleteLater()
        self.button.deleteLater()

    def match_sink(self, sink: Sink) -> bool:
        if self.sink:
            return self.sink.name == sink.name
        if self.bt_dev:
            return self.bt_dev.match_sink(sink)

        return False

    def match_bt(self, bt_dev: BTDevice) -> bool:
        if self.bt_dev:
            return self.bt_dev.mac == bt_dev.mac

        if self.sink:
            return bt_dev.match_sink(self.sink)

        return False

    def add_to_grid(self, grid: QGridLayout, row: int):
        grid.addWidget(self._shortcut_label, row, 0)
        grid.addWidget(self.button, row, 1)

    @property
    def _label(self) -> str:
        if self._bt_dev:
            return self._bt_dev.name + self._bt_dev.state_label

        return self._sink.description

    def _set_bt_dev(self, bt_dev: BTDevice):
        if bt_dev:
            self._update_label()
            connect(bt_dev.props_changed, self._update_label)
            logging.info(f"Set bt_dev for {self} and connected props_changed")

    def _set_shortcut(self, shortcut: str | None):
        if shortcut:
            self._shortcut_label.setText(f"[{shortcut}]")
            self._shortcut_label.show()
        else:
            self._shortcut_label.hide()

    def _update_label(self):
        logging.info(f"Updating label in {self}")
        self.button.setText(self._label)

    def __str__(self):
        return f"AudioOutput<sink={self._sink} bt_dev={self._bt_dev}>"

    __repr__ = __str__

    def __lt__(self, o: AudioOutput):
        if not isinstance(o, AudioOutput):
            return NotImplemented

        if (self_bt := bool(self._bt_dev)) != (o_bt := bool(o._bt_dev)):
            return self_bt < o_bt

        return self._label < o._label


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

        self._done = asyncio.Event()

    async def run(self):
        asyncio.get_running_loop().add_signal_handler(SIGINT, self._done.set)

        try:
            await self._start_services()
            self.keymap.bind(self._done.set, self.KEY_QUIT, force=True)
            self.show()
            await self._done.wait()
        finally:
            await self._cleanup()

    async def _start_services(self):
        self.sink_mgr.on_sinks_changed = self.on_sinks_changed
        self.sink_mgr.start()

        self.sysbus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self.bt_mgr = BTManager(self.sysbus)
        self.bt_mgr.on_new_device = self.on_new_bt
        self.bt_mgr.on_adapter_state_change = self.on_bt_state_change
        await self.bt_mgr.start()

    async def _cleanup(self):
        logging.debug("Cleanup...")

        self.sink_mgr.stop()
        self._cancel_output_activation_request()

        if self.bt_mgr:
            await self.bt_mgr.cleanup()

        if self.sysbus:
            self.sysbus.disconnect()
            logging.debug(f"Disconnected {self.sysbus}")

    def on_sinks_changed(self, added: list[Sink], removed: set[str], new_default: str | None):
        if not (added or removed or new_default):
            return

        logging.info(f"Menu sinks_changed: added:{added} removed:{removed} default:{new_default}")

        for o in list(self.audio_outputs):
            if o.sink and o.sink.name in removed:
                if o.bt_dev:
                    o.sink = None
                else:
                    self.keymap.unbind(o.shortcut)
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
        logging.info(f"New {bt_dev}")

        matches = [o for o in self.audio_outputs if o.match_bt(bt_dev)]
        if matches:
            matches[0].bt_dev = bt_dev
        else:
            self._add_output(bt_dev=bt_dev)

        self._update_ui()

    def on_bt_state_change(self, enabled: bool):
        if enabled:
            self._disable_loader()
            self.hide_bt_button()
        elif any(o.bt_dev for o in self.audio_outputs):
            logging.info("BT adapter disabled, but devices present")
            self.hide_bt_button()
        else:
            logging.info("BT adapter disabled")
            self.show_bt_button()

    def activate_bt(self, checked=False):
        self.bt_mgr.activate_adapter()
        self.hide_bt_button()
        self._enable_loader()

    def show_bt_button(self):
        self.bt_activate_button.show()
        for o in self.audio_outputs:
            if o.shortcut == self.KEY_ENABLE_BT:
                self.keymap.unbind(self.KEY_ENABLE_BT)
                self._bind_output(o)
                break

        self.keymap.bind(self.bt_activate_button.animateClick, self.KEY_ENABLE_BT)

    def hide_bt_button(self):
        self.bt_activate_button.hide()
        self.keymap.unbind(self.KEY_ENABLE_BT)

    def _update_ui(self):
        self.audio_outputs.sort()

        for o in self.audio_outputs:
            self.keymap.unbind(o.shortcut)

        while self.grid.count():
            self.grid.takeAt(0)

        for row, o in enumerate(self.audio_outputs):
            self._bind_output(o)
            o.add_to_grid(self.grid, row)

        logging.debug("Update UI finished")

    def _add_output(self, *, sink: Sink | None = None, bt_dev: BTDevice | None = None):
        o = AudioOutput(self, sink=sink, bt_dev=bt_dev)

        self.button_group.addButton(o.button)
        self.audio_outputs.append(o)

        logging.info(f"Added to UI: {o}")

    def _bind_output(self, o: AudioOutput) -> str:
        o.shortcut = self.keymap.bind_available(lambda: self._request_activate_output(o))

    def _request_activate_output(self, o: AudioOutput):
        self._cancel_output_activation_request()
        self._output_activation_task = asyncio.create_task(self._activate_output(o))

    def _cancel_output_activation_request(self):
        if self._output_activation_task:
            logging.debug(f"Cancelling {self._output_activation_task}")
            self._output_activation_task.cancel()
            self._output_activation_task = None
        else:
            logging.debug("Skipping task cancellation")

    async def _activate_output(self, o: AudioOutput):
        self._enable_loader()
        self._esc_shortcut.setEnabled(True)
        try:
            if o.sink:
                p = await asyncio.create_subprocess_exec("pactl", "set-default-sink", o.sink.name)
                await p.wait()
            elif o.bt_dev:
                logging.info(f"{o} does not have a sink, trying to connect BT device ...")
                await self.bt_mgr.connect_device(o.bt_dev)
        except asyncio.CancelledError:
            logging.info(f"Cancelled {o} activation")
            raise
        except DBusError as e:
            logging.warning(
                f"Couldn't connect to {o.bt_dev}: {e.args} {e.reply!r} {e.text!r} {e.type}"
            )
        finally:
            self._esc_shortcut.setEnabled(False)
            self._disable_loader()

    def _enable_loader(self):
        logging.debug("Enabling loader")
        self.loader.show()
        for o in self.audio_outputs:
            o.button.setDisabled(True)

    def _disable_loader(self):
        logging.debug("Disabling loader")
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
        logging.debug(f"CloseEvent: {ev}")
        ev.accept()
        self._done.set()


if __name__ == "__main__":
    main()
