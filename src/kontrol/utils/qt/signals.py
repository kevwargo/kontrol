import logging
from functools import wraps
from typing import Callable

from PyQt6.QtCore import pyqtBoundSignal


def safe_connect(sig: pyqtBoundSignal, slot: Callable):
    """Wraps the slot before passing it to the signal's .connect() method
    in order to avoid process crash if slot raises an exception.
    """

    @wraps(slot)
    def wrapped(*args, **kwargs):
        try:
            return slot(*args, **kwargs)
        except Exception:
            logging.exception(f"signal:{sig} slot:{slot}")

    sig.connect(wrapped)
