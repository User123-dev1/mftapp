"""PLC driver interface.

Drivers are synchronous (most industrial protocol stacks are). The
:class:`~whmi.receiver.plc.manager.PlcManager` runs each one on its own worker
thread so a slow PLC can never stall the receiver's I/O scan or link service.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class PlcCommError(Exception):
    """The connection to the PLC failed; the driver must be reconnected."""


@dataclass
class ReadResult:
    value: Any = None
    error: str | None = None  # per-tag error (tag missing, wrong type...); connection is still fine


class PlcDriver(ABC):
    @abstractmethod
    def connect(self) -> None:
        """Open the connection. Raises PlcCommError."""

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @abstractmethod
    def read(self, addresses: list[str]) -> dict[str, ReadResult]:
        """Read many tags in as few requests as possible. Raises PlcCommError."""

    @abstractmethod
    def write(self, items: list[tuple[str, Any]]) -> dict[str, str | None]:
        """Write tags; returns address -> error (None = OK). Raises PlcCommError."""

    def describe(self) -> dict[str, Any]:
        """Controller identity (name, revision...) for diagnostics."""
        return {}
