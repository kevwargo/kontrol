import asyncio
import logging
import os
import sys
from signal import SIGINT

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (QApplication, QGridLayout, QLabel, QPushButton,
                             QSizePolicy, QSlider, QVBoxLayout, QWidget)
from qasync import QEventLoop

from kontrol.utils.asynch import AsyncTaskSupervisor
from kontrol.utils.dbus import SessionBus
from kontrol.utils.qt.signals import connect

DBUS_NAME = "org.kde.ScreenBrightness"
DBUS_BASE_PATH = "/org/kde/ScreenBrightness"
DBUS_BASE_IFACE = DBUS_NAME
DBUS_DISPLAY_IFACE = f"{DBUS_BASE_IFACE}.Display"

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", logging.INFO),
    format="%(asctime)s | [%(levelname)s] %(message)s",
)


def main():
    app = QApplication(sys.argv)
    app.setDesktopFileName("kbrite")

    asyncio.run(run(), loop_factory=QEventLoop)


async def run():
    await Menu().run()


class Menu(QWidget, AsyncTaskSupervisor):
    def __init__(self):
        super().__init__()

        self._bus = SessionBus()
        self._done = asyncio.Event()
        self._layout = QGridLayout(self)
        logging.info(f"Initial row count: {self._layout.rowCount()}")
        self._displays: dict[str, Display] = {}

        self.setWindowTitle("Display brightness")
        self.setWindowFlag(Qt.WindowType.Dialog)
        self.setWindowIcon(QIcon.fromTheme("video-display-brightness"))

    async def run(self):
        try:
            await self._setup()
            self.show()
            await self._done.wait()
        finally:
            await self.cleanup()

    async def _setup(self):
        asyncio.get_running_loop().add_signal_handler(SIGINT, self._done.set)

        manager = await self._bus.iface(DBUS_NAME, DBUS_BASE_PATH, DBUS_BASE_IFACE)
        display_names = await manager.get_displays_d_bus_names()
        for name in display_names:
            await self._add_display(name)

        manager.on_display_added(self.as_task(self._add_display))
        manager.on_display_removed(self._remove_display)

    async def _add_display(self, name: str):
        iface = await self._bus.iface(DBUS_NAME, f"{DBUS_BASE_PATH}/{name}", DBUS_DISPLAY_IFACE)
        self._displays[name] = Display(
            self._layout,
            await iface.get_label(),
            await iface.get_brightness(),
            await iface.get_max_brightness(),
        )
        self._refresh_layout()

    def _remove_display(self, name: str):
        if display := self._displays.pop(name, None):
            display.deleteLater()
            self._refresh_layout()

    def _refresh_layout(self):
        while self._layout.count():
            self._layout.takeAt(0)

        for row, display in enumerate(self._displays.values()):
            display.add_to_grid(row)

    def closeEvent(self, ev):
        logging.debug(f"CloseEvent: {ev}")
        ev.accept()
        self._done.set()


class Display(QObject):
    def __init__(self, grid: QGridLayout, label: str, brightness: int, max_brightness: int):
        super().__init__(parent := grid.parentWidget())

        self._grid = grid
        self._label = QLabel(label, parent)
        self._control = DisplayControl(parent, brightness, max_brightness)

    def add_to_grid(self, row: int):
        self._grid.addWidget(self._label, row, 0)
        self._grid.addWidget(self._control, row, 1)

    def deleteLater(self):
        self._label.deleteLater()
        self._control.deleteLater()
        super().deleteLater()


class DisplayControl(QWidget):
    def __init__(self, parent: QWidget, val: int, maxval: int):
        super().__init__(parent)

        self._maxval = maxval

        self._slider = QSlider(Qt.Orientation.Horizontal, self)
        self._slider.setMinimum(0)
        self._slider.setMaximum(maxval)
        self._slider.setValue(val)
        self._slider.setMinimumWidth(200)
        connect(self._slider.sliderReleased, self.on_move)
        connect(self._slider.valueChanged, self._update_label)

        self._val_label = QLabel(self)

        self._button_inc = QPushButton("+", self)
        self._button_dec = QPushButton("-", self)
        for but in (self._button_dec, self._button_inc):
            but.setStyleSheet("padding: 5px;")

        self._layout = QGridLayout(self)
        self._layout.addWidget(self._val_label, 0, 0, 1, 3, Qt.AlignmentFlag.AlignHCenter)
        self._layout.addWidget(self._button_dec, 1, 0)
        self._layout.addWidget(self._slider, 1, 1)
        self._layout.addWidget(self._button_inc, 1, 2)

        self._update_label()

    def on_move(self):
        print("user changed", self._slider.value())

    def _update_label(self, val: int | None = None):
        if val is None:
            val = self._slider.value()

        percent = float(val) / self._maxval * 100
        self._val_label.setText(f"{percent:.2f}%")


if __name__ == "__main__":
    main()
