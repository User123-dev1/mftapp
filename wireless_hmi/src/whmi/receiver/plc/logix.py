"""Rockwell ControlLogix / CompactLogix / GuardLogix (standard tags) driver.

Uses EtherNet/IP CIP explicit messaging (via pycomm3) to read and write
controller- and program-scoped tags by name, e.g. ``Motor1_Speed``,
``Program:MainProgram.Fill_Level`` or ``Recipe.Steps[3].Temp``. The PLC
needs no changes for this: no I/O tree entry, no messaging logic.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from .base import PlcCommError, PlcDriver, ReadResult

log = logging.getLogger(__name__)

_INTEGER_TYPES = {"SINT", "INT", "DINT", "LINT", "USINT", "UINT", "UDINT", "ULINT", "BYTE", "WORD", "DWORD", "LWORD"}


def build_path(host: str, path: str) -> str:
    """``("192.168.1.10", "1,0")`` -> ``"192.168.1.10/1,0"`` (backplane port 1, slot 0)."""
    path = (path or "").strip().strip("/")
    return f"{host}/{path}" if path else host


class LogixPlc(PlcDriver):
    def __init__(self, host: str, path: str = "1,0", *, timeout_ms: int = 2000, driver_factory=None) -> None:
        self.host = host
        self.path = path
        self.timeout_ms = timeout_ms
        self._factory = driver_factory
        self._plc = None
        self._types: dict[str, str] = {}

    def _make(self):
        if self._factory is not None:
            return self._factory(build_path(self.host, self.path))
        from pycomm3 import LogixDriver  # imported lazily: optional dependency

        return LogixDriver(build_path(self.host, self.path), init_program_tags=True)

    def connect(self) -> None:
        from pycomm3.exceptions import PycommError

        self.close()
        plc = self._make()
        try:
            plc.socket_timeout = max(self.timeout_ms / 1000, 0.5)
            if not plc.open():
                raise PlcCommError(f"could not open connection to {self.host}")
        except PycommError as e:
            raise PlcCommError(f"{self.host}: {e}") from e
        except OSError as e:
            raise PlcCommError(f"{self.host}: {e}") from e
        self._plc = plc
        info = self.describe()
        log.info("connected to Logix %s at %s (rev %s)", info.get("product_name", "?"), self.host,
                 info.get("revision", "?"))

    def close(self) -> None:
        if self._plc is not None:
            try:
                self._plc.close()
            except Exception:  # pragma: no cover - best effort
                pass
        self._plc = None

    @property
    def connected(self) -> bool:
        return self._plc is not None and bool(getattr(self._plc, "connected", False))

    def _require(self):
        if not self.connected:
            raise PlcCommError("not connected")
        return self._plc

    def read(self, addresses: list[str]) -> dict[str, ReadResult]:
        from pycomm3.exceptions import PycommError

        plc = self._require()
        if not addresses:
            return {}
        try:
            results = plc.read(*addresses)
        except PycommError as e:
            self.close()
            raise PlcCommError(str(e)) from e
        except OSError as e:
            self.close()
            raise PlcCommError(str(e)) from e
        if not isinstance(results, list):
            results = [results]
        out: dict[str, ReadResult] = {}
        for addr, res in zip(addresses, results):
            if res is None:
                out[addr] = ReadResult(error="no response")
            elif res.error:
                out[addr] = ReadResult(error=str(res.error))
            else:
                if res.type:
                    self._types[addr] = str(res.type)
                out[addr] = ReadResult(res.value)
        if not plc.connected:
            self.close()
            raise PlcCommError("connection lost")
        return out

    def write(self, items: list[tuple[str, Any]]) -> dict[str, str | None]:
        from pycomm3.exceptions import PycommError

        plc = self._require()
        if not items:
            return {}
        prepared = [(addr, self._fit(addr, value)) for addr, value in items]
        try:
            results = plc.write(*prepared)
        except PycommError as e:
            self.close()
            raise PlcCommError(str(e)) from e
        except OSError as e:
            self.close()
            raise PlcCommError(str(e)) from e
        if not isinstance(results, list):
            results = [results]
        out: dict[str, str | None] = {}
        for (addr, _), res in zip(items, results):
            if res is None:
                out[addr] = "no response"
            else:
                out[addr] = str(res.error) if res.error else None
        return out

    def _fit(self, addr: str, value: Any) -> Any:
        """Round engineering->raw converted floats when the PLC tag is an integer type."""
        ptype = self._types.get(addr, "").split("[")[0].upper()
        if ptype in _INTEGER_TYPES and isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError(f"cannot write {value} to integer tag {addr}")
            return int(round(value))
        if ptype == "BOOL" and not isinstance(value, bool):
            return bool(value)
        return value

    def describe(self) -> dict[str, Any]:
        if self._plc is None:
            return {}
        try:
            info = dict(self._plc.info or {})
        except Exception:  # pragma: no cover
            return {}
        return {k: info[k] for k in ("product_name", "revision", "serial", "name", "keyswitch") if k in info}
