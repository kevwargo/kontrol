#!/usr/bin/env python3

import asyncio
import os
import shlex
import signal
import sys
from functools import cached_property
from pathlib import Path

from dbus_next import BusType, Message
from dbus_next.aio import MessageBus, ProxyInterface

DBUS_SERVICE = "org.kde.konsole"


async def main():
    await KonsoleService().run(sys.argv[1], sys.argv[2:])


class Bus(MessageBus):
    async def get_proxy_iface(self, bus_name: str, path: str, iface_name: str) -> ProxyInterface:
        intro = await self.introspect(bus_name, path)
        return self.get_proxy_object(bus_name, path, intro).get_interface(iface_name)


class KonsoleService:
    def __init__(self):
        self._commands = {
            "set-profile": self.set_profile,
            "cd": self.chdir,
        }

    async def run(self, cmd: str, args: list[str]):
        cmd = self._commands[cmd]
        self._bus = await Bus(bus_type=BusType.SESSION).connect()
        await cmd(*args)

    async def set_profile(self, profile: str):
        for s in await self._get_sub_ifaces("/Sessions", "org.kde.konsole.Session"):
            await s.call_set_profile(profile)
        for w in await self._window_list():
            await w.set_profile(profile)

    async def chdir(self, path: str):
        path = Path(path).expanduser().resolve()
        candidate = await self._find_dir_candidate(path)
        if isinstance(candidate, Session):
            await candidate.window.set_session(candidate.id)
            await candidate.window.activate()
        else:
            await candidate.new_session(path)
            await candidate.activate()

    async def _find_dir_candidate(self, path: Path) -> Session | Window:
        for window in await self._window_list():
            for session in await window.session_list():
                if session.fpid != session.pid or session.exe_name != "bash":
                    continue

                if session.cwd == path:
                    return session

        return window

    async def _window_list(self) -> list[Window]:
        return [
            Window(self._bus, w)
            for w in await self._get_sub_ifaces("/Windows", "org.kde.konsole.Window")
        ]

    async def _get_sub_ifaces(self, base_path: str, iface_name: str) -> list[ProxyInterface]:
        ifaces = []

        base = await self._bus.introspect(DBUS_SERVICE, base_path)
        for n in base.nodes:
            ifaces.append(
                await self._bus.get_proxy_iface(DBUS_SERVICE, f"{base_path}/{n.name}", iface_name)
            )

        return ifaces


class Window:
    def __init__(self, bus: Bus, iface: ProxyInterface):
        self._bus = bus
        self._iface = iface
        self.id = int(iface.path.split("/")[-1])

    async def set_profile(self, profile: str):
        await self._iface.call_set_default_profile(profile)

    async def session_list(self) -> list[Session]:
        return [
            await Session(self._bus, self, sess_id).resolve()
            for sess_id in await self._iface.call_session_list()
        ]

    async def set_session(self, sess_id: int):
        await self._iface.call_set_current_session(sess_id)

    async def activate(self):
        await self._iface.call_request_activate()

    async def new_session(self, working_dir: Path | str):
        msg = Message(
            destination=DBUS_SERVICE,
            path=self._iface.path,
            interface=self._iface.introspection.name,
            member="newSession",
            signature="ss",
            body=["", str(working_dir)],
        )
        await self._bus.call(msg)


class Session:
    def __init__(self, bus: Bus, window: Window, sess_id: str):
        self._bus = bus
        self.window = window
        self.id = int(sess_id)

    async def resolve(self) -> Session:
        args = DBUS_SERVICE, f"/Sessions/{self.id}"
        self._iface = self._bus.get_proxy_object(
            *args, await self._bus.introspect(*args)
        ).get_interface("org.kde.konsole.Session")

        self.fpid = await self._iface.call_foreground_process_id()
        self.pid = await self._iface.call_process_id()
        self.pid_path = Path(f"/proc/{self.pid}")
        self.exe_name = (self.pid_path / "exe").readlink().name

        return self

    @cached_property
    def cwd(self) -> Path:
        return (self.pid_path / "cwd").resolve()

    async def send_text(self, text: str):
        await self._iface.call_send_text(text)


if __name__ == "__main__":
    asyncio.run(main())
