import os
import re
from argparse import ArgumentParser
from pathlib import Path

from PyQt6.QtWidgets import QLabel, QVBoxLayout

from kontrol.utils.qt.dialog import AsyncDialog, Keymap


def main():
    args = parse_args()
    ppid = find_parent(args.parent) if args.parent else None
    Kopy.exec(ppid, args)


class Kopy(AsyncDialog):
    desktop_filename = "kopy"

    def __init__(self, parent_pid: int | None, args):
        super().__init__()

        self.parent_pid = parent_pid

        self.keymap = Keymap(self)
        self.keymap.bind("Q", self.quit)
        self.setWindowTitle("Kopy")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Parent PID: {self.parent_pid}", self))
        layout.addWidget(
            QLabel("<ul>" + "".join(f"<li>{p}</li>" for p in args.paths) + "</ul>", self)
        )

        self.setMinimumWidth(320)
        self.setMinimumHeight(240)


def parse_args():
    p = ArgumentParser()
    p.add_argument("--parent")
    p.add_argument("paths", nargs="*")
    return p.parse_args()


def find_parent(name: str) -> int:
    ppid = os.getppid()
    while ppid > 1:
        p = Path("/proc") / str(ppid)
        try:
            if (p / "exe").readlink().name == name:
                return ppid
            elif m := _PPID_RE.search((p / "status").read_text()):
                ppid = int(m.group(1))
            else:
                break
        except OSError:
            break

    raise RuntimeError(f"must be run from {name}")


_PPID_RE = re.compile(r"^PPid:\s*([0-9]+)", re.MULTILINE)


if __name__ == "__main__":
    main()
