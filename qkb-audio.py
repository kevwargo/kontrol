#!/usr/bin/env python3


import json
import signal
import sys
from subprocess import check_output

from PyQt6.QtCore import QProcess, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


class SinkRow(QLabel):
    pass


class MenuDialog(QWidget):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app

        self.setWindowTitle("Choose audio output")
        self.setWindowFlag(Qt.WindowType.Dialog)

        self.layout = QVBoxLayout(self)

        self.sink_rows: dict[str, SinkRow] = {}
        self.refresh_sink_rows()

    def refresh_sink_rows(self):
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


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QApplication(sys.argv)
    app.setDesktopFileName("qkb-audio")

    menu = MenuDialog(app)
    menu.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
