import asyncio
import sys
from signal import SIGINT

from PyQt6.QtWidgets import QApplication, QWidget
from qasync import QEventLoop


class AsyncDialog(QWidget):
    desktop_filename: str | None = None

    @classmethod
    def exec(cls):
        app = QApplication(sys.argv)
        if cls.desktop_filename:
            app.setDesktopFileName(cls.desktop_filename)

        asyncio.run(cls._exec_async(), loop_factory=QEventLoop)

    def __init__(self):
        super().__init__()
        self.__done = asyncio.Event()

    async def setup(self): ...
    async def cleanup(self): ...

    def closeEvent(self, ev):
        ev.accept()
        self.__done.set()

    def quit(self):
        self.__done.set()

    @classmethod
    async def _exec_async(cls):
        await cls()._run()

    async def _run(self):
        try:
            asyncio.get_running_loop().add_signal_handler(SIGINT, self.__done.set)
            await self.setup()
            self.show()
            await self.__done.wait()
        finally:
            await self.cleanup()
