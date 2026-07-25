import logging
import os
from dataclasses import dataclass

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import QGridLayout, QLabel, QPushButton, QSlider, QWidget

from kontrol.utils.asynch import AsyncTaskWatcher
from kontrol.utils.dbus import SessionBus
from kontrol.utils.qt.dialog import AsyncDialog
from kontrol.utils.qt.signals import safe_connect

DBUS_NAME = "org.kde.ScreenBrightness"
DBUS_BASE_PATH = "/org/kde/ScreenBrightness"
DBUS_BASE_IFACE = DBUS_NAME
DBUS_DISPLAY_IFACE = f"{DBUS_BASE_IFACE}.Display"

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", logging.INFO),
    format="%(asctime)s | [%(levelname)s] %(message)s",
)


def main():
    Dialog.exec()


class Dialog(AsyncDialog):
    desktop_filename = "kbrite"

    _delta_keys = [
        ("PgUp", "PgDown"),
        ("Left", "Right"),
        ("-", "="),
    ]

    def __init__(self):
        super().__init__()

        self._manager = DisplayManager()
        self._tw = AsyncTaskWatcher()
        self._layout = QGridLayout(self)
        self._displays: dict[str, UIDisplay] = {}
        self._shortcuts: list[QShortcut] = []

        self.setWindowTitle("Display brightness")
        self.setWindowFlag(Qt.WindowType.Dialog)
        self.setWindowIcon(QIcon.fromTheme("video-display-brightness"))

    async def setup(self):
        for name in await self._manager.display_names():
            await self._handle_display_added(name)

        await self._manager.on_display_added(self._tw.as_task(self._handle_display_added))
        await self._manager.on_display_removed(self._handle_display_removed)
        await self._manager.on_brightness_changed(self._handle_brightness_changed)

        quit_shortcut = QShortcut(QKeySequence("Q"), self)
        quit_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        safe_connect(quit_shortcut.activated, self.quit)

    async def cleanup(self):
        await self._tw.cleanup()

    async def _handle_display_added(self, name: str):
        if name in self._displays:
            self._handle_display_removed(name)

        display = UIDisplay(self._layout, await self._manager.get_display(name))
        safe_connect(
            display.control.value_changed,
            self._tw.as_task(self._set_brightness, display_name=name),
        )
        self._displays[name] = display
        self._refresh_layout()

    def _handle_display_removed(self, name: str):
        if display := self._displays.pop(name, None):
            display.deleteLater()
            self._refresh_layout()

    async def _set_brightness(self, val: int, *, display_name: str):
        logging.debug(f"Setting {display_name} -> {val}")
        await self._manager.set_brightness(display_name, val)

    def _refresh_layout(self):
        while self._layout.count():
            self._layout.takeAt(0)

        for s in self._shortcuts:
            s.deleteLater()
        self._shortcuts = []

        for row, display in enumerate(self._displays.values()):
            if row < len(self._delta_keys):
                dec_sc = QShortcut(QKeySequence(self._delta_keys[row][0]), self)
                inc_sc = QShortcut(QKeySequence(self._delta_keys[row][1]), self)
                dec_sc.setContext(Qt.ShortcutContext.WindowShortcut)
                inc_sc.setContext(Qt.ShortcutContext.WindowShortcut)
                self._shortcuts.extend([dec_sc, inc_sc])
                display.control.set_shortcuts(dec_sc, inc_sc)

            display.add_to_grid(row)

    def _handle_brightness_changed(self, name: str, val: int, client_name: str, client_ctx: str):
        logging.debug(
            f"Brightness update: {name!r} -> {val}"
            f" client_name:{client_name!r} client_ctx:{client_ctx!r}"
        )
        if display := self._displays.get(name):
            display.control.set_brightness(val)


class DisplayManager:
    def __init__(self):
        self._bus = SessionBus()
        self._iface = None

    async def display_names(self) -> list[Display]:
        await self._ensure_iface()
        return await self._iface.get_displays_d_bus_names()

    async def get_display(self, name: str) -> Display:
        iface = await self._bus.iface(DBUS_NAME, f"{DBUS_BASE_PATH}/{name}", DBUS_DISPLAY_IFACE)
        return Display(
            label=await iface.get_label(),
            brightness=await iface.get_brightness(),
            max_brightness=await iface.get_max_brightness(),
        )

    async def on_display_added(self, handler):
        await self._ensure_iface()
        self._iface.on_display_added(handler)

    async def on_display_removed(self, handler):
        await self._ensure_iface()
        self._iface.on_display_removed(handler)

    async def on_brightness_changed(self, handler):
        await self._ensure_iface()
        self._iface.on_brightness_changed(handler)

    async def set_brightness(self, display_name: str, val: int):
        iface = await self._bus.iface(
            DBUS_NAME, f"{DBUS_BASE_PATH}/{display_name}", DBUS_DISPLAY_IFACE
        )
        await iface.call_set_brightness_with_context(val, 0, "kbrite")

    async def _ensure_iface(self):
        if not self._iface:
            self._iface = await self._bus.iface(DBUS_NAME, DBUS_BASE_PATH, DBUS_BASE_IFACE)


@dataclass
class Display:
    label: str
    brightness: int
    max_brightness: int


class UIDisplay(QObject):
    def __init__(self, grid: QGridLayout, display: Display):
        super().__init__(parent := grid.parentWidget())

        self._grid = grid
        self._label = QLabel(display.label, parent)
        self.control = UIDisplayControl(parent, display.brightness, display.max_brightness)

    def add_to_grid(self, row: int):
        self._grid.addWidget(self._label, row, 0)
        self._grid.addWidget(self.control, row, 1)

    def deleteLater(self):
        self._label.deleteLater()
        self.control.deleteLater()
        super().deleteLater()


class UIDisplayControl(QWidget):
    value_changed = pyqtSignal(int)

    def __init__(self, parent: QWidget, val: int, maxval: int):
        super().__init__(parent)

        self._maxval = maxval

        self._slider = QSlider(Qt.Orientation.Horizontal, self)
        self._slider.setMinimum(0)
        self._slider.setMaximum(maxval)
        self._slider.setValue(val)
        self._slider.setMinimumWidth(200)
        safe_connect(self._slider.sliderReleased, self.on_move)
        safe_connect(self._slider.valueChanged, self._update_label)

        self._val_label = QLabel(self)

        self._button_inc = QPushButton("+10%", self)
        self._button_dec = QPushButton("-10%", self)
        safe_connect(self._button_dec.clicked, self._button_handler(-10))
        safe_connect(self._button_inc.clicked, self._button_handler(10))
        for but in (self._button_dec, self._button_inc):
            but.setStyleSheet("padding: 5px;")

        self._layout = QGridLayout(self)
        self._layout.addWidget(self._val_label, 0, 0, 1, 3, Qt.AlignmentFlag.AlignHCenter)
        self._layout.addWidget(self._button_dec, 1, 0)
        self._layout.addWidget(self._slider, 1, 1)
        self._layout.addWidget(self._button_inc, 1, 2)

        self._update_label()

    def set_brightness(self, val: int):
        self._slider.setValue(val)

    def on_move(self, val: int | None = None):
        if val is None:
            val = self._slider.value()

        logging.info(f"user changed: {val}")
        self.value_changed.emit(val)

    def set_shortcuts(self, dec_sc: QShortcut, inc_sc: QShortcut):
        self._button_dec.setText(f"[{dec_sc.key().toString()}]")
        safe_connect(dec_sc.activated, self._button_dec.animateClick)
        self._button_inc.setText(f"[{inc_sc.key().toString()}]")
        safe_connect(inc_sc.activated, self._button_inc.animateClick)

    def _button_handler(self, delta_percent: int):
        def clicked(checked=False):
            val = self._slider.value() + int(self._maxval / 100 * delta_percent)
            self._slider.setValue(val)
            self.on_move(val)

        return clicked

    def _update_label(self, val: int | None = None):
        if val is None:
            val = self._slider.value()

        percent = float(val) / self._maxval * 100
        self._val_label.setText(f"{percent:.2f}%")


if __name__ == "__main__":
    main()
