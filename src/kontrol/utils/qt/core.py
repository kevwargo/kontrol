import logging
from functools import wraps
from typing import get_type_hints

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from kontrol.utils.qt.signals import safe_connect


class QDataclass:
    def __init_subclass__(cls, /, **kwargs):
        super().__init_subclass__(**kwargs)

        emit_signal = issubclass(cls, QObject) and isinstance(
            cls.__dict__.get("props_changed"), pyqtSignal
        )

        prop_defaults = cls.__get_prop_defaults()
        cls.__wrap_init(prop_defaults, emit_signal)
        for p in prop_defaults:
            cls.__define_prop(p, emit_signal)

    @classmethod
    def __define_prop(cls, name: str, emit_signal: bool):
        def fget(o):
            return getattr(o, f"_{name}")

        setter_name = f"_set_{name}"
        orig_setter = cls.__dict__.get(setter_name, lambda self, val: None)

        def fset_signal(self, val):
            if (old_val := getattr(self, f"_{name}")) != val:
                setattr(self, f"_{name}", val)
                logging.info(f"changed {self}.{name}: {old_val} -> {val}")
                self._props_changed_timer.start()

            orig_setter(self, val)

        def fset_basic(self, val):
            setattr(self, f"_{name}", val)
            orig_setter(self, val)

        fset = fset_signal if emit_signal else fset_basic

        setattr(cls, setter_name, fset)
        setattr(cls, name, property(fget=fget, fset=fset))

    @classmethod
    def __get_prop_defaults(cls) -> dict:
        return {
            p: cls.__dict__.get(p)
            for p, t in get_type_hints(cls).items()
            if not p.startswith("_") and isinstance(t, type) and p not in ("parent",)
        }

    @classmethod
    def __wrap_init(cls, prop_defaults: dict, emit_signal: bool):
        orig = cls.__init__

        @wraps(orig)
        def wrapped(self, *args, **kwargs):
            for p, v in prop_defaults.items():
                setattr(self, f"_{p}", kwargs.pop(p, v))

            orig(self, *args, **kwargs)

            if emit_signal:
                self._props_changed_timer = QTimer(self)
                self._props_changed_timer.setInterval(20)
                self._props_changed_timer.setSingleShot(True)
                safe_connect(self._props_changed_timer.timeout, self.props_changed.emit)

        cls.__init__ = wrapped
