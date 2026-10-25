import asyncio
import time
from pathlib import Path

import pytest

from whmi.common.codes import ClientKind, Role
from whmi.common.project import Project
from whmi.link.client import LinkClient
from whmi.link.credentials import CredentialStore
from whmi.receiver.config import DeviceConfig
from whmi.receiver.io.simulated import SimulatedIo
from whmi.receiver.plc.simulator import SimulatedPlc
from whmi.receiver.service import Receiver

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "demo_line.whp"


async def eventually(predicate, timeout: float = 3.0, interval: float = 0.01):
    """Wait until ``predicate()`` is truthy (it may be a coroutine function)."""
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return result
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(interval)


class Rig:
    """A running receiver with simulated I/O + PLC and helpers to connect clients."""

    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.creds = CredentialStore()
        self.keys = {}
        for cid, role in (("studio", Role.ENGINEER), ("panel1", Role.OPERATOR), ("panel2", Role.OPERATOR),
                          ("viewer", Role.VIEWER)):
            self.keys[cid] = self.creds.create(cid, role).key
        self.io = SimulatedIo()
        self.plc = SimulatedPlc({
            "HMI_Start_PB": False, "HMI_Stop_PB": False, "Line_Running": False,
            "Conveyor.Speed_SP": 50.0, "Conveyor.Speed_Act": 0.0,
        })
        self.config = DeviceConfig(receiver_id="RX-TEST", listen_host="127.0.0.1", listen_port=0,
                                   data_dir=str(tmp_path / "rx"))
        self.receiver = self._make_receiver()
        self.clients: list[LinkClient] = []

    def _make_receiver(self) -> Receiver:
        return Receiver(self.config, self.creds, io_driver=self.io, plc_drivers={"PLC1": self.plc})

    @property
    def port(self) -> int:
        return self.receiver.server.bound_port

    async def start(self):
        await self.receiver.start()

    async def restart(self):
        await self.receiver.stop()
        self.receiver = self._make_receiver()
        await self.receiver.start()

    def client(self, cid: str, key: bytes | None = None, kind=ClientKind.TOOL) -> LinkClient:
        c = LinkClient("127.0.0.1", cid, key or self.keys[cid], port=self.port, kind=kind, request_timeout=5)
        self.clients.append(c)
        return c

    async def connect(self, cid: str) -> LinkClient:
        c = self.client(cid)
        await c.connect()
        return c

    async def load(self, project: Project | None = None):
        compiled = (project or Project.load(EXAMPLE)).build()
        await self.receiver.activate(compiled)
        return compiled

    async def close(self):
        for c in self.clients:
            await c.close()
        await self.receiver.stop()


@pytest.fixture
async def rig(tmp_path):
    r = Rig(tmp_path)
    await r.start()
    yield r
    await r.close()
