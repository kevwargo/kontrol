from dataclasses import dataclass

from PyQt6.QtGui import QKeySequence


class KeySequence(QKeySequence):
    def __init__(self, raw):
        super().__init__(raw)
        if not self.toString():
            raise ValueError(f"Invalid key {raw!r}")

    def __str__(self):
        return self.toString()

    def __repr__(self):
        return repr(self.toString())

    def to_dbus(self) -> list[int]:
        numeric = [k.toCombined() for k in self]
        if (rem := 4 - len(numeric)) > 0:
            numeric.extend([0] * rem)
        return numeric


@dataclass
class ShortcutInfo:
    action_id: str
    action_name: str
    component_id: str
    component_name: str
    context_id: str
    context_name: str
    active_keys: list[KeySequence]
    default_keys: list[KeySequence]
    remapped_keys: list[KeySequence] | None = None

    @classmethod
    def from_list(cls, fields: list[str]):
        return cls(
            action_id=fields[0],
            action_name=fields[1],
            component_id=fields[2],
            component_name=fields[3],
            context_id=fields[4],
            context_name=fields[5],
            active_keys=[KeySequence(k) for k in fields[6] if k],
            default_keys=[KeySequence(k) for k in fields[7] if k],
        )

    def __str__(self):
        remap = f" remapped to {self.remapped_keys}" if self.remapped_keys is not None else ""
        return (
            f"{self.component_id}({self.component_name}):{self.action_id}({self.action_name}):"
            f" {self.active_keys}{remap}"
        )

    def __repr__(self):
        return repr(str(self))

    def to_dbus(self) -> list[str]:
        return [self.component_id, self.action_id, self.component_name, self.action_name]
