# General

- Simplify build process - build the wheel instead of a source tarball in Makefile

## Bugs

- Wrap functions passed to as_task to exclude keyword args to avoid this
```
  File "~/kontrol/src/kontrol/utils/asynch.py", line 37, in __task_done
    task.result()
    ~~~~~~~~~~~^^
  File "~/kontrol/src/kontrol/gui/qwg.py", line 157, in _added
    iface.on_state_changed(self._tw.as_task(self._dev_state_changed, device_path=path))
    ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "~/kontrol/.venv/lib/python3.14/site-packages/dbus_next/proxy_object.py", line 109, in on_signal_fn
    raise TypeError(
        f'reply_notify must be a function with {len(intr_signal.args)} parameters')
TypeError: reply_notify must be a function with 3 parameters
```

# KWinCTL

## Rules

1. match multiple classes (or regexp) in a single rule

## Snippets: strip whitespace

```
<id>:
  snippet:
    cmd: ...
    whitespace:
      strip: bool
      strip-head: bool
      strip-tail: bool
      chars: (default ' \n\r\t')
```

# QKVox

- **fix**: BT devices don't switch to `[OFF]` in UI
- actually run `pactl set-default-sink` once previously inactive BT device comes online
- make loader last longer when connecting to a device with bt disabled (currently loader disappers quicker than needed - when adapter activates, while it should wait until the device in question appears)
- implement unmuting channels
- adapt for multiple adapters (xD)

# New stuff

- VPNs from /etc/wireguard
