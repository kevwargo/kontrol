import os
import re
import sys
from pathlib import Path

from annocli import Arg, Namespace, entrypoint
from PyQt6.QtWidgets import QLabel, QVBoxLayout

from kontrol.utils.qt.dialog import AsyncDialog, Keymap


class Args(Namespace):
    parent: str | None = None
    args: list[str] | None = Arg(positional=True, nargs="*")


@entrypoint
def main(args: Args):
    ppid = None

    if args.parent:
        ppid = _find_parent(args.parent)
        if not ppid:
            print(f"must be run from {args.parent}", file=sys.stderr)
            sys.exit(1)

    print(args)

    Kopy.exec(ppid, args)


class Kopy(AsyncDialog):
    desktop_filename = "kopy"

    def __init__(self, parent_pid: int | None, args: Args):
        super().__init__()

        self.parent_pid = parent_pid

        self.keymap = Keymap(self)
        self.keymap.bind("Q", self.quit)
        self.setWindowTitle("Kopy")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Parent PID: {self.parent_pid}", self))

        self.setMinimumWidth(320)
        self.setMinimumHeight(240)


def _find_parent(name: str) -> int | None:
    ppid = os.getppid()
    while ppid > 1:
        p = Path("/proc") / str(ppid)
        try:
            if (p / "exe").readlink().name == name:
                return ppid
            elif m := _PPID_RE.search((p / "status").read_text()):
                ppid = int(m.group(1))
            else:
                return None
        except OSError:
            return None


_PPID_RE = re.compile(r"^PPid:\s*([0-9]+)", re.MULTILINE)


if __name__ == "__main__":
    main()
