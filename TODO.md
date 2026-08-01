# General

- Simplify build process - build the wheel instead of a source tarball in Makefile
- Change all `asyncio.create_task`s to `AsyncTaskWatcher.start_task`

## Bugs

- Wrap functions passed to `as_task` to exclude keyword args to avoid this
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

- Retry BT connection request when sth like this happens
```
2026-07-24 23:59:31,453 | [WARNING] Failed to connect to BTDev<C4:A9:B8:0C:C0:A2('JBL Vibe Beam 2') [OFF]>: br-connection-adapter-not-powered
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

## Builtin commands

Allow to bind builtin commands - the ones defined directly in JS.

Ideas for such commands:
- Center window

## Overrides

Improve registration (a.k.a. recording) of currently active global shortcuts and saving it in reproducible
form in `overrides.yaml`.

# QKVox

- implement unmuting channels
- adapt for multiple adapters (xD)
