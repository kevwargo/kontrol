from dbus_next import BusType, Message
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface


class AutoConnectBus(MessageBus):
    def __init__(self, bus_type: BusType):
        super().__init__(bus_type=bus_type)
        self.__connected = False
        self.__iface_cache = {}

    async def iface(self, bus_name: str, path: str, iface_name: str):
        if iface := self.__iface_cache.get((bus_name, path, iface_name)):
            return iface

        intr = await self.introspect(bus_name, path)
        iface = self.get_proxy_object(bus_name, path, intr).get_interface(iface_name)
        self.__iface_cache[(bus_name, path, iface_name)] = iface

        return iface

    async def introspect(self, bus_name: str, path: str, timeout: float = 30.0):
        await self.__ensure_connected()
        return await super().introspect(bus_name, path, timeout)

    async def call(self, msg: Message):
        await self.__ensure_connected()
        return await super().call(msg)

    async def export_name(self, bus_name: str, path: str, iface: ServiceInterface):
        await self.__ensure_connected()
        self.export(path, iface)
        await self.request_name(bus_name)

    async def __ensure_connected(self):
        if not self.__connected:
            await self.connect()
            self.__connected = True


class SessionBus(AutoConnectBus):
    def __init__(self):
        super().__init__(BusType.SESSION)


class SystemBus(AutoConnectBus):
    def __init__(self):
        super().__init__(BusType.SYSTEM)
