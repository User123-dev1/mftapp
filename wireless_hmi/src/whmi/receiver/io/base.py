"""Local I/O module driver interface.

A driver performs one *exchange* per scan: it writes the output image to the
modules and returns the input image, just like a backplane scan. Real hardware
drivers (SPI/I2C to the I/O slots) implement the same interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ...common.project import IoAddress, IoModuleType


@dataclass
class InputPoint:
    raw: float | bool
    ok: bool = True  # False = module fault / wire break


class IoDriver(ABC):
    @abstractmethod
    def open(self, slots: dict[int, IoModuleType]) -> None:
        """Configure the driver for the given slot population."""

    @abstractmethod
    def exchange(self, outputs: dict[IoAddress, float | bool]) -> dict[IoAddress, InputPoint]:
        """Write all outputs, read all inputs. Must not block longer than a scan."""

    def module_ok(self, slot: int) -> bool:
        return True

    def close(self) -> None:  # noqa: B027 - optional hook
        """Drive all outputs off and release the hardware."""
