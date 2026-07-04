#!/usr/bin/env python3

import json
import logging
import re
import signal
import sys
from subprocess import check_output

from PyQt6.QtCore import QProcess, Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(asctime)s %(message)s")


class SinkRow(QLabel):
    pass


class MenuDialog(QWidget):
    PACTL_EVENT_RX = re.compile(b"^Event '(new|remove)' on sink #[0-9]+")

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app

        self.setWindowTitle("Choose audio output")
        self.setWindowFlag(Qt.WindowType.Dialog)

        self.layout = QVBoxLayout(self)

        self.sink_rows: dict[str, SinkRow] = {}
        self.refresh_sink_rows()
        self.start_watcher()

    def refresh_sink_rows(self):
        logging.info("refreshing sinks")

        new_sinks = {
            sink["name"]: sink["description"]
            for sink in json.loads(
                check_output(["pactl", "--format=json", "list", "sinks"], encoding="utf-8")
            )
        }

        current_names = set(self.sink_rows)
        for name in current_names.difference(new_sinks):
            old = self.sink_rows.pop(name)
            self.layout.removeWidget(old)
            old.deleteLater()

        for name, description in new_sinks.items():
            if sink_row := self.sink_rows.get(name):
                sink_row.setText(description)
            else:
                sink_row = self.sink_rows[name] = SinkRow(description)
                self.layout.addWidget(sink_row)

    def start_watcher(self):
        self.watch_proc = QProcess(self)
        self.watch_proc.setProgram("pactl")
        self.watch_proc.setArguments(["subscribe"])
        self.watch_proc.readyReadStandardOutput.connect(self.on_pactl_event)

        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.refresh_sink_rows)

        self.app.aboutToQuit.connect(self.stop_watcher)
        self.watch_proc.start()

    def stop_watcher(self):
        if self.watch_proc.state() != QProcess.ProcessState.NotRunning:
            self.watch_proc.terminate()
            self.watch_proc.waitForFinished(1000)

    def on_pactl_event(self):
        out = self.watch_proc.readAllStandardOutput()
        for line in out.data().splitlines():
            if self.PACTL_EVENT_RX.match(line):
                logging.info(line.decode())
                self.timer.start()


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QApplication(sys.argv)
    app.setDesktopFileName("qkb-audio")

    menu = MenuDialog(app)
    menu.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
