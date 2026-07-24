import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from signal import SIGINT

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QButtonGroup, QRadioButton, QWidget
from qasync import QEventLoop

from kontrol.utils.asynch import AsyncTaskWatcher
from kontrol.utils.qt.signals import safe_connect


class AsyncDialog(QWidget):
    desktop_filename: str | None = None

    @classmethod
    def exec(cls):
        app = QApplication(sys.argv)
        if cls.desktop_filename:
            app.setDesktopFileName(cls.desktop_filename)

        asyncio.run(cls.__exec_async(), loop_factory=QEventLoop)

    def __init__(self):
        super().__init__()
        self.__done = asyncio.Event()

    async def setup(self): ...
    async def cleanup(self): ...

    def closeEvent(self, ev):
        ev.ignore()
        self.hide()
        self.__done.set()

    def quit(self):
        self.__done.set()

    @classmethod
    async def __exec_async(cls):
        """Instantiate the dialog, start and wait for it to finish its job.
        It's done in this helpers because subclass often initialize a dbus_next.io.MessageBus
        which needs a running event loop.
        """
        await cls()._run()

    async def _run(self):
        try:
            asyncio.get_running_loop().add_signal_handler(SIGINT, self.__done.set)
            await self.setup()
            self.show()
            await self.__done.wait()
        finally:
            await self.cleanup()


class Keymap:
    def __init__(self, parent: QWidget, available_keys: list[str] | None = None):
        self._parent = parent
        self._shortcuts: dict[str, QShortcut | None] = {k: None for k in (available_keys or [])}

    def next_free_key(self) -> str | None:
        for k, shortcut in self._shortcuts.items():
            if shortcut is None:
                return k

        return None

    def bind(self, key: str, action: Callable[[], None]):
        if shortcut := self._shortcuts.get(key):
            shortcut.deleteLater()

        shortcut = self._shortcuts[key] = QShortcut(QKeySequence(key), self._parent)
        shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        safe_connect(shortcut.activated, lambda: self._call_action(key, action))

    def unbind_key(self, key: str):
        if shortcut := self._shortcuts.pop(key, None):
            shortcut.deleteLater()

    def _call_action(self, key: str, action: Callable):
        logging.debug(f"Key {key!r} pressed, calling {action} ...")
        action()


class _ActionRadioButton(QRadioButton):
    activation_requested = pyqtSignal()

    def __init__(
        self,
        *args,
        activate: Callable[[], Awaitable[bool]] | None,
        deactivate: Callable[[], Awaitable[None]] | None,
    ):
        super().__init__(*args)
        self.activate_fn = activate or self._noop_bool
        self.deactivate_fn = deactivate or self._noop

    def nextCheckState(self):
        if self.isChecked():
            logging.debug(f"Skipping {self}.activation_requested(): it's checked")
            super().nextCheckState()
        else:
            logging.debug(f"Emitting {self}.activation_requested()")
            self.activation_requested.emit()

    def __str__(self):
        return f"{type(self).__name__}({self.text()!r})"

    __repr__ = __str__

    @staticmethod
    async def _noop(): ...

    @staticmethod
    async def _noop_bool():
        return True


class ActionButtonGroup(QButtonGroup):
    def __init__(self, parent: QObject, task_watcher: AsyncTaskWatcher):
        super().__init__(parent)
        self._tw = task_watcher
        self._active_buttons: set[_ActionRadioButton] = set()

    def create_button(
        self,
        *args,
        init_state: bool = False,
        activate: Callable[[], Awaitable[bool]] = None,
        deactivate: Callable[[], Awaitable[None]] = None,
    ) -> _ActionRadioButton:
        rb = _ActionRadioButton(*args, activate=activate, deactivate=deactivate)
        self.addButton(rb)
        safe_connect(rb.activation_requested, self._tw.as_task(self._handle_activation, button=rb))
        safe_connect(rb.clicked, self._tw.as_task(self._handle_click, button=rb))

        if init_state:
            logging.info(f"Setting {rb} as checked")
            with self._inclusive():
                rb.setChecked(True)
                self._active_buttons.add(rb)

        return rb

    async def deactivate_all(self):
        with self._buttons_disabled():
            await self._deactivate_all()

    @contextmanager
    def _buttons_disabled(self):
        for b in self.buttons():
            b.setEnabled(False)
        try:
            yield
        finally:
            for b in self.buttons():
                b.setEnabled(True)

    @contextmanager
    def _inclusive(self):
        self.setExclusive(False)
        try:
            yield
        finally:
            self.setExclusive(True)

    async def _handle_click(self, checked=False, *, button: _ActionRadioButton):
        if checked and button in self._active_buttons:
            logging.info(f"Clicked currently selected {button}, deactivating it")
            with self._buttons_disabled():
                await self._deactivate_button(button)

    async def _deactivate_button(self, button: _ActionRadioButton):
        await button.deactivate_fn()

        with self._inclusive():
            button.setChecked(False)
            self._active_buttons.discard(button)

    async def _deactivate_all(self):
        for b in list(self._active_buttons):
            await self._deactivate_button(b)

    async def _handle_activation(self, button: _ActionRadioButton):
        logging.debug(f"Received activation request from {button}")

        with self._buttons_disabled():
            await self._deactivate_all()

            try:
                res = await button.activate_fn()
            except Exception:
                logging.exception(f"Exception in <{button}>.activate()")
                res = False

            if res:
                logging.debug(f"Checking {button}")
                button.setChecked(True)
                self._active_buttons.add(button)
