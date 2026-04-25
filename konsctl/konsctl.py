#!/usr/bin/env python3

import asyncio
import sys
from functools import cached_property
from pathlib import Path
from subprocess import DEVNULL, Popen

from dbus_next import BusType, DBusError, Message
from dbus_next.aio import MessageBus, ProxyInterface


async def main():
    await KonsoleService().run(sys.argv[1], sys.argv[2:])


class Bus(MessageBus):
    async def get_proxy_iface(self, bus_name: str, path: str, iface_name: str) -> ProxyInterface:
        intro = await self.introspect(bus_name, path)
        return self.get_proxy_object(bus_name, path, intro).get_interface(iface_name)


class KonsoleService:
    DBUS_GLOBAL = "org.kde.konsole"

    def __init__(self):
        self._commands = {
            "set-profile": self.set_profile,
            "cd": self.chdir,
        }
        self._active_service = self.DBUS_GLOBAL

    async def run(self, cmd: str, args: list[str]):
        cmd = self._commands[cmd]
        self._bus = await Bus(bus_type=BusType.SESSION).connect()
        await cmd(*args)

    async def set_profile(self, profile: str):
        session_ifaces = await self._get_sub_ifaces("/Sessions", "org.kde.konsole.Session")
        if session_ifaces is None:
            return

        for s in session_ifaces:
            await s.call_set_profile(profile)
        for w in await self._window_list():
            await w.set_profile(profile)

    async def chdir(self, path: str):
        path = Path(path).expanduser().resolve()
        candidate = await self._find_dir_candidate(path)
        if isinstance(candidate, Session):
            await candidate.window.set_session(candidate.id)
            await candidate.window.activate()
        elif isinstance(candidate, Window):
            await candidate.new_session(path)
            await candidate.activate()
        else:
            p = Popen(
                ["konsole", "--workdir", str(path)],
                stdin=DEVNULL,
                stdout=DEVNULL,
                stderr=DEVNULL,
                start_new_session=True,
            )
            print(f"No active Konsole instance found, started new {p.pid}")

    async def _find_dir_candidate(self, path: Path) -> Session | Window | None:
        window_list = await self._window_list()
        if window_list is None:
            return None

        for window in window_list:
            for session in await window.session_list():
                if session.fpid != session.pid or session.exe_name != "bash":
                    continue

                if session.cwd == path:
                    return session

        return window

    async def _window_list(self) -> list[Window] | None:
        window_ifaces = await self._get_sub_ifaces("/Windows", "org.kde.konsole.Window")
        if window_ifaces is None:
            return None

        return [Window(self._bus, self._active_service, w) for w in window_ifaces]

    async def _get_sub_ifaces(self, base_path: str, iface_name: str) -> list[ProxyInterface] | None:
        base_intro = None

        try:
            base_intro = await self._bus.introspect(self._active_service, base_path)
            print(f"Found Konsole DBus service {self._active_service}")
        except DBusError:
            print(f"Konsole DBus service {self._active_service} is not available")

        if base_intro is None:
            dbus_iface = await self._bus.get_proxy_iface(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus"
            )
            try:
                self._active_service = next(
                    n for n in await dbus_iface.call_list_names() if n.startswith(self.DBUS_GLOBAL)
                )
                print(f"Found Konsole DBus service {self._active_service}")
            except StopIteration:
                pass
            else:
                base_intro = await self._bus.introspect(self._active_service, base_path)

        if base_intro is None:
            return None

        ifaces = []
        for n in base_intro.nodes:
            ifaces.append(
                await self._bus.get_proxy_iface(
                    self._active_service, f"{base_path}/{n.name}", iface_name
                )
            )

        return ifaces


class Window:
    def __init__(self, bus: Bus, svc: str, iface: ProxyInterface):
        self._bus = bus
        self._iface = iface
        self._service = svc
        self.id = int(iface.path.split("/")[-1])

    async def set_profile(self, profile: str):
        await self._iface.call_set_default_profile(profile)

    async def session_list(self) -> list[Session]:
        return [
            await Session(self._bus, self._service, self, sess_id).resolve()
            for sess_id in await self._iface.call_session_list()
        ]

    async def set_session(self, sess_id: int):
        await self._iface.call_set_current_session(sess_id)

    async def activate(self):
        await self._iface.call_request_activate()

    async def new_session(self, working_dir: Path | str):
        msg = Message(
            destination=self._service,
            path=self._iface.path,
            interface=self._iface.introspection.name,
            member="newSession",
            signature="ss",
            body=["", str(working_dir)],
        )
        await self._bus.call(msg)


class Session:
    def __init__(self, bus: Bus, svc: str, window: Window, sess_id: str):
        self._bus = bus
        self.window = window
        self._service = svc
        self.id = int(sess_id)

    async def resolve(self) -> Session:
        self._iface = await self._bus.get_proxy_iface(
            self._service, f"/Sessions/{self.id}", "org.kde.konsole.Session"
        )

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
