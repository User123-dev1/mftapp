"""Logix driver against a fake pycomm3 LogixDriver (no controller needed)."""

from collections import namedtuple

import pytest
from pycomm3.exceptions import CommError

from whmi.receiver.plc.base import PlcCommError
from whmi.receiver.plc.logix import LogixPlc, build_path

Tag = namedtuple("Tag", "tag value type error")


class FakeLogix:
    def __init__(self, path):
        self.path = path
        self.connected = False
        self.tags = {"Speed": (12.5, "REAL"), "Count": (3, "DINT"), "Run": (True, "BOOL")}
        self.fail = False
        self.socket_timeout = None
        self.info = {"product_name": "1769-L33ER", "revision": "33.11"}

    def open(self):
        self.connected = True
        return True

    def close(self):
        self.connected = False

    def read(self, *names):
        if self.fail:
            self.connected = False
            raise CommError("socket closed")
        out = [Tag(n, *self.tags[n], None) if n in self.tags else Tag(n, None, None, "Path segment error")
               for n in names]
        return out if len(out) > 1 else out[0]

    def write(self, *items):
        out = []
        for name, value in items:
            if name not in self.tags:
                out.append(Tag(name, None, None, "Path segment error"))
                continue
            self.tags[name] = (value, self.tags[name][1])
            out.append(Tag(name, value, self.tags[name][1], None))
        return out if len(out) > 1 else out[0]


@pytest.fixture
def plc():
    holder = {}

    def factory(path):
        holder["fake"] = FakeLogix(path)
        return holder["fake"]

    drv = LogixPlc("10.0.0.5", "1,2", timeout_ms=1500, driver_factory=factory)
    drv.connect()
    return drv, holder["fake"]


def test_build_path():
    assert build_path("10.0.0.5", "1,0") == "10.0.0.5/1,0"
    assert build_path("10.0.0.5", "") == "10.0.0.5"


def test_connect_uses_route_and_timeout(plc):
    drv, fake = plc
    assert fake.path == "10.0.0.5/1,2"
    assert fake.socket_timeout == 1.5
    assert drv.connected
    assert drv.describe()["product_name"] == "1769-L33ER"


def test_read_single_and_many(plc):
    drv, _ = plc
    assert drv.read(["Speed"])["Speed"].value == 12.5
    res = drv.read(["Speed", "Count", "Missing"])
    assert res["Count"].value == 3
    assert res["Missing"].error


def test_write_rounds_floats_for_integer_tags(plc):
    drv, fake = plc
    drv.read(["Count"])  # learns the PLC type
    assert drv.write([("Count", 41.6)]) == {"Count": None}
    assert fake.tags["Count"][0] == 42
    assert drv.write([("Nope", 1)])["Nope"]


def test_comm_error_raises_and_disconnects(plc):
    drv, fake = plc
    fake.fail = True
    with pytest.raises(PlcCommError):
        drv.read(["Speed"])
    assert not drv.connected
