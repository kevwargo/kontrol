import logging
from functools import wraps
from typing import Callable

from PyQt6.QtCore import pyqtBoundSignal


def connect(sig: pyqtBoundSignal, slot: Callable):
    @wraps(slot)
    def wrapped(*args, **kwargs):
        try:
            return slot(*args, **kwargs)
        except Exception:
            logging.exception(f"signal:{sig} slot:{slot}")

    sig.connect(wrapped)
