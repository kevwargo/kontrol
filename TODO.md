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

- This
```
Aug 06 11:58:17 kwinctl[107346]: [INF] Started shell command [107752](qkvox)
Aug 06 11:58:18 kwinctl[107752]: 2026-08-06 11:58:18,203 [DBG] qkvox | Showing BT button
Aug 06 11:58:18 kwinctl[107752]: 2026-08-06 11:58:18,250 [DBG] qkvox | Default sink event <asyncio.locks.Event object at 0x7f5a44274180 [set]> for bluez_output.C4_A9_B8_0C_C0_A2.1 set
Aug 06 11:58:18 kwinctl[107752]: 2026-08-06 11:58:18,250 [DBG] qkvox | Default sink event <asyncio.locks.Event object at 0x7f5a442742b0 [unset]> for alsa_output.pci-0000_00_1f.3-platform-sof_sdw.HiFi__Speaker__sink cleared
Aug 06 11:58:18 kwinctl[107752]: 2026-08-06 11:58:18,251 [INF] qkvox | Sinks changed - added:[Sink<alsa_output.pci-0000_00_1f.3-platform-sof_sdw.HiFi__Speaker__sink(Core Ultra 200V Series Processors HD Audio Speaker)>, Sink<blu>
Aug 06 11:58:18 kwinctl[107752]: 2026-08-06 11:58:18,252 [INF] qkvox | Added to UI: AudioOutput<sink=Sink<alsa_output.pci-0000_00_1f.3-platform-sof_sdw.HiFi__Speaker__sink(Core Ultra 200V Series Processors HD Audio Speaker)> bt>
Aug 06 11:58:18 kwinctl[107752]: 2026-08-06 11:58:18,252 [INF] qkvox | Added to UI: AudioOutput<sink=Sink<bluez_output.C4_A9_B8_0C_C0_A2.1(JBL Vibe Beam 2)> bt_dev=None>
Aug 06 11:58:18 python3[107752]: Failed to register with host portal QDBusError("org.freedesktop.portal.Error.Failed", "Could not register app ID: App info not found for 'qkvox'")
Aug 06 11:58:18 kwinctl[107752]: 2026-08-06 11:58:18,283 [INF] qkvox | New adapter at /org/bluez/hci0: <dbus_next.signature.Variant ('s', 28:92:00:E3:A7:46)>
Aug 06 11:58:18 kwinctl[107752]: 2026-08-06 11:58:18,283 [DBG] qkvox | Hiding BT button
Aug 06 11:58:18 kwinctl[107752]: 2026-08-06 11:58:18,288 [DBG] qkvox | Cleanup...
Aug 06 11:58:18 kwinctl[107752]: 2026-08-06 11:58:18,289 [DBG] qkvox | Disconnected <kontrol.utils.dbus.SystemBus object at 0x7f5a44250980>
Aug 06 11:58:18 kwinctl[107752]: Traceback (most recent call last):
Aug 06 11:58:18 kwinctl[107752]:   File "/usr/bin/qkvox", line 8, in <module>
Aug 06 11:58:18 kwinctl[107752]:     sys.exit(main())
Aug 06 11:58:18 kwinctl[107752]:              ~~~~^^
Aug 06 11:58:18 kwinctl[107752]:   File "/usr/lib/python3.14/site-packages/kontrol/gui/qkvox.py", line 24, in main
Aug 06 11:58:18 kwinctl[107752]:     Dialog.exec()
Aug 06 11:58:18 kwinctl[107752]:     ~~~~~~~~~~~^^
Aug 06 11:58:18 kwinctl[107752]:   File "/usr/lib/python3.14/site-packages/kontrol/utils/qt/dialog.py", line 28, in exec
Aug 06 11:58:18 kwinctl[107752]:     asyncio.run(cls.__exec_async(), loop_factory=QEventLoop)
Aug 06 11:58:18 kwinctl[107752]:     ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Aug 06 11:58:18 kwinctl[107752]:   File "/usr/lib64/python3.14/asyncio/runners.py", line 205, in run
Aug 06 11:58:18 kwinctl[107752]:     return runner.run(main)
Aug 06 11:58:18 kwinctl[107752]:            ~~~~~~~~~~^^^^^^
Aug 06 11:58:18 kwinctl[107752]:   File "/usr/lib64/python3.14/asyncio/runners.py", line 128, in run
Aug 06 11:58:18 kwinctl[107752]:     return self._loop.run_until_complete(task)
Aug 06 11:58:18 kwinctl[107752]:            ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^
Aug 06 11:58:18 kwinctl[107752]:   File "/usr/lib/python3.14/site-packages/qasync/__init__.py", line 438, in run_until_complete
Aug 06 11:58:18 kwinctl[107752]:     return future.result()
Aug 06 11:58:18 kwinctl[107752]:            ~~~~~~~~~~~~~^^
Aug 06 11:58:18 kwinctl[107752]:   File "/usr/lib/python3.14/site-packages/kontrol/utils/qt/dialog.py", line 51, in __exec_async
Aug 06 11:58:18 kwinctl[107752]:     await cls()._run()
Aug 06 11:58:18 kwinctl[107752]:   File "/usr/lib/python3.14/site-packages/kontrol/utils/qt/dialog.py", line 56, in _run
Aug 06 11:58:18 kwinctl[107752]:     await self.setup()
Aug 06 11:58:18 kwinctl[107752]:   File "/usr/lib/python3.14/site-packages/kontrol/gui/qkvox.py", line 451, in setup
Aug 06 11:58:18 kwinctl[107752]:     await self.bt_mgr.start()
Aug 06 11:58:18 kwinctl[107752]:   File "/usr/lib/python3.14/site-packages/kontrol/gui/qkvox.py", line 119, in start
Aug 06 11:58:18 kwinctl[107752]:     await self._iface_added(path, obj_ifaces)
Aug 06 11:58:18 kwinctl[107752]:   File "/usr/lib/python3.14/site-packages/kontrol/gui/qkvox.py", line 186, in _iface_added
Aug 06 11:58:18 kwinctl[107752]:     await self._notify_device(path)
Aug 06 11:58:18 kwinctl[107752]:   File "/usr/lib/python3.14/site-packages/kontrol/gui/qkvox.py", line 157, in _notify_device
Aug 06 11:58:18 kwinctl[107752]:     name = await iface.get_name()
Aug 06 11:58:18 kwinctl[107752]:            ^^^^^^^^^^^^^^^^^^^^^^
Aug 06 11:58:18 kwinctl[107752]:   File "/usr/lib/python3.14/site-packages/dbus_next/aio/proxy_object.py", line 118, in property_getter
Aug 06 11:58:18 kwinctl[107752]:     BaseProxyInterface._check_method_return(msg, 'v')
Aug 06 11:58:18 kwinctl[107752]:     ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
Aug 06 11:58:18 kwinctl[107752]:   File "/usr/lib/python3.14/site-packages/dbus_next/proxy_object.py", line 62, in _check_method_return
Aug 06 11:58:18 kwinctl[107752]:     raise DBusError._from_message(msg)
Aug 06 11:58:18 kwinctl[107752]: dbus_next.errors.DBusError: No such property 'Name'
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

- implement unmuting channels
- adapt for multiple adapters (xD)
