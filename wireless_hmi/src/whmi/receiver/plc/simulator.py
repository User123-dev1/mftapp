"""Simulated PLC: an in-memory tag table with the semantics of a Logix controller."""

from __future__ import annotations

import threading
from typing import Any

from .base import PlcCommError, PlcDriver, ReadResult


class SimulatedPlc(PlcDriver):
    """A fake controller.

    Tag names are case-insensitive like Logix. Tests can take it "offline"
    with :attr:`online` to exercise the comm-loss path.
    """

    def __init__(self, tags: dict[str, Any] | None = None, *, auto_create: bool = False) -> None:
        self.auto_create = auto_create  # demo mode: unknown tags spring into existence as 0
        self._tags: dict[str, Any] = {}
        self._names: dict[str, str] = {}
        for k, v in (tags or {}).items():
            self.set(k, v)
        self.online = True
        self._connected = False
        self._lock = threading.Lock()
        self.read_count = 0
        self.write_log: list[tuple[str, Any]] = []

    def set(self, name: str, value: Any) -> None:
        self._names[name.lower()] = name
        self._tags[name.lower()] = value

    def get(self, name: str) -> Any:
        return self._tags[name.lower()]

    def connect(self) -> None:
        if not self.online:
            raise PlcCommError("simulated PLC offline")
        self._connected = True

    def close(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self.online

    def _check(self) -> None:
        if not self.online:
            self._connected = False
            raise PlcCommError("simulated PLC offline")
        if not self._connected:
            raise PlcCommError("not connected")

    def read(self, addresses: list[str]) -> dict[str, ReadResult]:
        with self._lock:
            self._check()
            self.read_count += 1
            out = {}
            for a in addresses:
                if self.auto_create and a.lower() not in self._tags:
                    self.set(a, 0)
                if a.lower() in self._tags:
                    out[a] = ReadResult(self._tags[a.lower()])
                else:
                    out[a] = ReadResult(error="tag does not exist")
            return out

    def write(self, items: list[tuple[str, Any]]) -> dict[str, str | None]:
        with self._lock:
            self._check()
            out: dict[str, str | None] = {}
            for a, v in items:
                if a.lower() in self._tags:
                    self._tags[a.lower()] = v
                    self.write_log.append((a, v))
                    out[a] = None
                else:
                    out[a] = "tag does not exist"
            return out

    def describe(self) -> dict[str, Any]:
        return {"product_name": "WHMI Simulated Logix", "tags": len(self._tags)}
