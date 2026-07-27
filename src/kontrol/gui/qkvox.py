import asyncio
import json
import re
from collections import defaultdict
from functools import cached_property
from subprocess import run as run_cmd

from dbus_next import DBusError
from PyQt6.QtCore import QObject, QProcess, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QGridLayout, QLabel, QPushButton, QVBoxLayout

from kontrol.utils.asynch import AsyncTaskWatcher
from kontrol.utils.cmd import multi_command
from kontrol.utils.dbus import SystemBus
from kontrol.utils.log import get_logger
from kontrol.utils.qt.core import QDataclass, safe_connect
from kontrol.utils.qt.dialog import ActionButtonGroup, AsyncDialog, Keymap

logger = get_logger("qkvox")


def main():
    Dialog.exec()


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


class BTDevice(QObject, QDataclass):
    props_changed = pyqtSignal()

    id: str
    mac: str
    name: str
    connected: bool

    def __init__(self, mgr: BTManager):
        super().__init__(mgr)
        self._mgr = mgr
        self._activation_event = asyncio.Event()

    def __str__(self):
        return f"BTDev<{self.mac}({self.name!r}){self.state_label}>"

    def match_sink(self, sink: Sink) -> bool:
        return self.mac.replace(":", "_").upper() in sink.name.upper()

    async def wait_for_connection(self):
        await self._activation_event.wait()

    @property
    def state_label(self) -> str:
        return " [ON]" if self.connected else " [OFF]"

    def _set_connected(self, state: bool):
        if state:
            logger.debug(f"{self} connected, setting {self._activation_event}")
            self._activation_event.set()
        else:
            logger.debug(f"{self} disconnected, clearing {self._activation_event}")
            self._activation_event.clear()


class BTManager(QObject):
    BUS_NAME = "org.bluez"
    DEVICE_IFACE = "org.bluez.Device1"
    ADAPTER_IFACE = "org.bluez.Adapter1"

    device_added = pyqtSignal(BTDevice)
    adapter_state_changed = pyqtSignal(bool)

    def __init__(self, parent: QObject, bus: SystemBus, tw: AsyncTaskWatcher):
        super().__init__(parent)

        self._bus = bus

        self._ifaces: dict[str, set] = defaultdict(set)
        self._devices: dict[str, BTDevice] = {}

        self._adapter_path: str | None = None
        self._adapter_ready = asyncio.Event()

        self._tw = tw

    async def start(self):
        root_intro = await self._bus.introspect(self.BUS_NAME, "/")
        manager = self._bus.get_proxy_object(self.BUS_NAME, "/", root_intro).get_interface(
            "org.freedesktop.DBus.ObjectManager"
        )
        manager.on_interfaces_added(self._tw.as_task(self._iface_added))
        manager.on_interfaces_removed(self._tw.as_task(self._iface_removed))

        objects = await manager.call_get_managed_objects()
        for path, obj_ifaces in objects.items():
            await self._iface_added(path, obj_ifaces)

    async def activate_adapter(self):
        cmd = ["rfkill", "unblock", "bluetooth"]
        logger.debug(f"Running {cmd}")
        run_cmd(cmd, check=True)

        logger.info("Waiting for BT adapter ...")
        await self._adapter_ready.wait()
        logger.info("BT adapter ready")

    async def connect_device(self, dev: BTDevice):
        await self.activate_adapter()

        intro = await self._bus.introspect(self.BUS_NAME, dev.id)
        iface = self._bus.get_proxy_object(self.BUS_NAME, dev.id, intro).get_interface(
            self.DEVICE_IFACE
        )
        logger.debug(f"Calling {dev}.Connect() ...")
        await iface.call_connect()
        logger.debug(f"Call {dev}.Connect() suceeded, waiting for device to become connected")

        await dev.wait_for_connection()
        logger.debug(f"Fully connected to {dev}")

    async def _notify_device(self, path: str):
        dev = self._devices.get(path)

        if dev and not self._ifaces.get(path):
            logger.info(f"{dev} disappeared")
            dev.connected = False
            return

        intro = await self._bus.introspect(self.BUS_NAME, path)
        iface = self._bus.get_proxy_object(self.BUS_NAME, path, intro).get_interface(
            self.DEVICE_IFACE
        )

        name = await iface.get_name()
        address = await iface.get_address()
        if not await iface.get_paired():
            logger.debug(f"Ignoring unpaired BT device {address}({name})")
            return

        connected = await iface.get_connected()

        if dev:
            dev.name = name
            dev.address = address
            dev.connected = connected
        else:
            self._devices[path] = BTDevice(
                self, id=path, name=name, mac=address, connected=connected
            )
            self.device_added.emit(self._devices[path])

    def _notify_adapter(self, path, adapter):
        logger.info(f"New adapter at {path}: {adapter['Address']}")

        self._adapter_path = path
        self._adapter_ready.set()
        self.adapter_state_changed.emit(True)

    async def _iface_added(self, path: str, new_ifaces: dict):
        self._ifaces[path].update(new_ifaces)
        logger.debug(f"dbus added: {path} + {sorted(new_ifaces)} = {sorted(self._ifaces[path])}")

        if self.DEVICE_IFACE in self._ifaces[path]:
            await self._notify_device(path)
        elif adapter := new_ifaces.get(self.ADAPTER_IFACE):
            self._notify_adapter(path, adapter)

    async def _iface_removed(self, path: str, removed_ifaces):
        self._ifaces[path].difference_update(removed_ifaces)

        if not self._ifaces[path]:
            if path == self._adapter_path:
                self._adapter_ready.clear()
                self.adapter_state_changed.emit(False)

        if path in self._devices:
            await self._notify_device(path)


class SinkManager(QObject):
    EVENT_REGEX = re.compile(b"^Event '(new|remove|change)' on (card|sink(-input)?) #[0-9]+")

    sinks_changed = pyqtSignal(list, set, str)

    def __init__(self, parent):
        super().__init__(parent)

        self.watcher = QProcess(self)
        self.watcher.setProgram("pactl")
        self.watcher.setArguments(["subscribe"])
        safe_connect(self.watcher.readyReadStandardOutput, self._on_pactl_event)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(50)
        safe_connect(self.timer.timeout, self._update_sinks)

        self._last_sinks: dict[str, Sink] = {}
        self._last_default: str | None = None
        self._default_sink_events: dict[str, asyncio.Event] = defaultdict(asyncio.Event)

    def start(self):
        self._update_sinks()
        self.watcher.start()

    def stop(self):
        self.timer.stop()

        if self.watcher.state() != QProcess.ProcessState.NotRunning:
            self.watcher.terminate()
            self.watcher.waitForFinished(1000)

    async def wait_until_sink_default(self, sink_name: str):
        await (e := self._default_sink_events[sink_name]).wait()
        logger.info(f"Wait on default sink event {e} for {sink_name} finished")

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
            for name in set(self._default_sink_events).union(available_sinks):
                e = self._default_sink_events[name]
                if name == default_sink:
                    e.set()
                    logger.debug(f"Default sink event {e} for {name} set")
                else:
                    e.clear()
                    logger.debug(f"Default sink event {e} for {name} cleared")

        if added or removed or default_sink:
            self.sinks_changed.emit(added, removed, default_sink)


class AudioOutput(QDataclass):
    sink: Sink
    bt_dev: BTDevice
    shortcut: str

    def __init__(
        self,
        button_group: ActionButtonGroup,
        sink_mgr: SinkManager,
        bt_mgr: BTManager,
    ):
        if not (self.sink or self.bt_dev):
            raise ValueError(
                f"At least one of `sink` or `bt_dev` must be specified for {type(self).__name__}"
            )

        self._sink_mgr = sink_mgr
        self._bt_mgr = bt_mgr

        self.button = button_group.create_button(self._label, activate=self._activate)
        self._shortcut_label = QLabel()
        self._shortcut_label.hide()

        if self.bt_dev:
            safe_connect(self.bt_dev.props_changed, self._update_label)
            logger.info(f"Connected initial bt_dev.props_changed to {self}._update_label")

        self._sink_ready = asyncio.Event()

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
        grid.addWidget(self._shortcut_label, row, 0, alignment=Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.button, row, 1, alignment=Qt.AlignmentFlag.AlignLeft)

    async def _activate(self) -> bool:
        if self.sink is None:
            if not await self._wait_for_sink():
                return False

        cmd = ["pactl", "set-default-sink", self.sink.name]
        logger.debug(f"Running {cmd} ...")
        p = await asyncio.create_subprocess_exec(*cmd)
        await p.wait()

        if p.returncode != 0:
            logger.debug(f"Command {cmd} failed with {p.returncode}")
            return False
        else:
            logger.debug(f"Command {cmd} succeeded")

        logger.debug(f"Waiting until {self.sink} becomes default ...")
        await self._sink_mgr.wait_until_sink_default(self.sink.name)
        logger.debug(f"{self.sink} became default ...")

        return True

    async def _wait_for_sink(self):
        try:
            await self._bt_mgr.connect_device(self.bt_dev)
        except DBusError as e:
            logger.warning(f"Failed to connect to {self.bt_dev}: {e}")
            return False

        logger.debug(f"Waiting for sink in {self} ...")

        await self._sink_ready.wait()

        return True

    @property
    def _label(self) -> str:
        if self._bt_dev:
            return self._bt_dev.name + self._bt_dev.state_label

        return self._sink.description

    def _set_bt_dev(self, bt_dev: BTDevice):
        if bt_dev:
            self._update_label()
            safe_connect(bt_dev.props_changed, self._update_label)
            logger.info(f"Set bt_dev for {self} and connected props_changed")

    def _set_shortcut(self, shortcut: str | None):
        if shortcut:
            self._shortcut_label.setText(f"[{shortcut}]")
            self._shortcut_label.show()
        else:
            self._shortcut_label.hide()

    def _set_sink(self, sink: Sink | None):
        if sink is None:
            logger.debug(f"Sink unset for {self}, clearing {self._sink_ready}")
            self._sink_ready.clear()
        else:
            logger.debug(f"Sink set for {self}, setting {self._sink_ready}")
            self._sink_ready.set()

    def _update_label(self):
        logger.info(f"Updating label in {self}")
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


class Dialog(AsyncDialog):
    desktop_filename = "qkvox"

    KEY_QUIT = "Q"
    KEY_ENABLE_BT = "B"

    def __init__(self):
        super().__init__()

        self.setWindowTitle("QKVox: audio outputs")
        self.setWindowFlag(Qt.WindowType.Dialog)
        self.setWindowIcon(QIcon.fromTheme("audio-on"))

        self.audio_outputs: list[AudioOutput] = []
        self.keymap = Keymap(self, [c for c in map(chr, range(ord("A"), ord("Z") + 1))])

        self.sink_mgr = SinkManager(self)
        self.sysbus = SystemBus()
        self.tw = AsyncTaskWatcher()
        self.bt_mgr = BTManager(self, self.sysbus, self.tw)
        self.button_group = ActionButtonGroup(self, self.tw)

        self.bt_activate_button = QPushButton(f"Enable ({self.KEY_ENABLE_BT})", self)
        safe_connect(self.bt_activate_button.clicked, self.tw.as_task(self.activate_bt))
        self.show_bt_button()

        # self.loader = QProgressBar(self)
        # self.loader.setRange(0, 0)
        # self.loader.hide()

        self.grid = QGridLayout()
        self.top_layout = QVBoxLayout(self)
        self.top_layout.addLayout(self.grid)
        self.top_layout.addWidget(self.bt_activate_button)
        # self.top_layout.addWidget(self.loader)

    async def setup(self):
        safe_connect(self.sink_mgr.sinks_changed, self.sinks_changed)
        self.sink_mgr.start()

        safe_connect(self.bt_mgr.device_added, self.bt_device_added)
        safe_connect(self.bt_mgr.adapter_state_changed, self.bt_adapter_state_changed)
        await self.bt_mgr.start()

        self.keymap.bind(self.KEY_QUIT, self.quit)

    async def cleanup(self):
        logger.debug("Cleanup...")

        self.sink_mgr.stop()

        await self.tw.cleanup()

        if self.sysbus:
            self.sysbus.disconnect()
            logger.debug(f"Disconnected {self.sysbus}")

    def sinks_changed(self, added: list[Sink], removed: set[str], new_default: str | None):
        logger.info(f"Sinks changed - added:{added} removed:{removed} default:{new_default}")

        for o in list(self.audio_outputs):
            if o.sink and o.sink.name in removed:
                if o.bt_dev:
                    o.sink = None
                else:
                    self.keymap.unbind_key(o.shortcut)
                    o.deleteLater()
                    self.audio_outputs.remove(o)

        for sink in added:
            if not self.assign_sink(sink):
                self.add_output(sink=sink)

        if new_default is not None:
            for o in self.audio_outputs:
                if o.sink and o.sink.name == new_default:
                    o.button.setChecked(True)

        self.update_ui()

    def bt_device_added(self, bt_dev: BTDevice):
        logger.info(f"New {bt_dev}")

        matches = [o for o in self.audio_outputs if o.match_bt(bt_dev)]
        if matches:
            matches[0].bt_dev = bt_dev
        else:
            self.add_output(bt_dev=bt_dev)

        self.update_ui()

    def bt_adapter_state_changed(self, state: bool):
        if state:
            self.hide_bt_button()
        else:
            self.show_bt_button()

    def add_output(self, *, sink: Sink | None = None, bt_dev: BTDevice | None = None):
        o = AudioOutput(
            sink=sink,
            bt_dev=bt_dev,
            sink_mgr=self.sink_mgr,
            bt_mgr=self.bt_mgr,
            button_group=self.button_group,
        )
        self.audio_outputs.append(o)

        logger.info(f"Added to UI: {o}")

    async def activate_bt(self, button_checked=False):
        with self.button_group.buttons_disabled():
            self.hide_bt_button()
            try:
                await self.bt_mgr.activate_adapter()
            except Exception:
                self.show_bt_button()
                raise

    def show_bt_button(self):
        logger.debug("Showing BT button")
        self.bt_activate_button.show()
        for o in self.audio_outputs:
            if o.shortcut == self.KEY_ENABLE_BT:
                self.keymap.unbind_key(self.KEY_ENABLE_BT)
                self.set_output_shortcut(o)
                break

        self.keymap.bind(self.KEY_ENABLE_BT, self.bt_activate_button.animateClick)

    def hide_bt_button(self):
        logger.debug("Hiding BT button")
        self.bt_activate_button.hide()
        self.keymap.unbind_key(self.KEY_ENABLE_BT)

    def update_ui(self):
        self.audio_outputs.sort()

        for o in self.audio_outputs:
            self.keymap.unbind_key(o.shortcut)

        while self.grid.count():
            self.grid.takeAt(0)

        for row, o in enumerate(self.audio_outputs):
            self.set_output_shortcut(o)
            o.add_to_grid(self.grid, row)

        QTimer.singleShot(0, self.adjustSize)

    def set_output_shortcut(self, o: AudioOutput) -> str:
        if not (key := self.keymap.next_free_key()):
            logger.warning(f"Failed to set shortcut for {o} - no free keys left")
        else:
            logger.debug(f"Binding {o} to {key!r}")

            # TODO: change this bind to animateClick on a button from self.button_group
            self.keymap.bind(key, o.button.animateClick)

            o.shortcut = key

    def assign_sink(self, sink: Sink) -> bool:
        for o in self.audio_outputs:
            if o.match_sink(sink):
                o.sink = sink
                return True

        return False


if __name__ == "__main__":
    main()
