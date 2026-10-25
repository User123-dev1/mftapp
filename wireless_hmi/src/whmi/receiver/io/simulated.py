"""In-memory I/O driver for development, testing and demos."""

from __future__ import annotations

import threading

from ...common.project import IoAddress, IoModuleType
from .base import InputPoint, IoDriver


class SimulatedIo(IoDriver):
    """Simulated I/O slots.

    Inputs can be driven with :meth:`set_input` (tests, a simulator UI).
    ``loopback`` mirrors each DO/AO channel onto the DI/AI channel with the same
    index of the next slot that has a matching input module, which makes
    a self-contained demo possible.
    """

    def __init__(self, *, loopback: bool = False) -> None:
        self.loopback = loopback
        self.slots: dict[int, IoModuleType] = {}
        self.outputs: dict[IoAddress, float | bool] = {}
        self._inputs: dict[IoAddress, InputPoint] = {}
        self._faulted: set[int] = set()
        self._lock = threading.Lock()
        self.exchanges = 0

    def open(self, slots: dict[int, IoModuleType]) -> None:
        with self._lock:
            self.slots = dict(slots)
            self.outputs = {}
            self._inputs = {}
            for slot, mod in slots.items():
                if mod.is_output:
                    continue
                for ch in range(mod.channels):
                    raw = False if mod.kind == "DI" else mod.raw_min
                    self._inputs[IoAddress(slot, mod.kind, ch)] = InputPoint(raw)

    def set_input(self, address: str | IoAddress, raw: float | bool) -> None:
        addr = IoAddress.parse(address) if isinstance(address, str) else address
        with self._lock:
            if addr not in self._inputs:
                raise KeyError(f"no input {addr}")
            self._inputs[addr] = InputPoint(raw, self._inputs[addr].ok)

    def set_fault(self, slot: int, faulted: bool = True) -> None:
        with self._lock:
            (self._faulted.add if faulted else self._faulted.discard)(slot)

    def module_ok(self, slot: int) -> bool:
        return slot not in self._faulted

    def exchange(self, outputs: dict[IoAddress, float | bool]) -> dict[IoAddress, InputPoint]:
        with self._lock:
            self.exchanges += 1
            self.outputs = {a: v for a, v in outputs.items() if a.slot not in self._faulted}
            if self.loopback:
                self._apply_loopback()
            return {a: InputPoint(p.raw, p.ok and a.slot not in self._faulted) for a, p in self._inputs.items()}

    def _apply_loopback(self) -> None:
        for addr, value in self.outputs.items():
            want = "DI" if addr.kind == "DO" else "AI"
            for slot in sorted(s for s in self.slots if s > addr.slot):
                if self.slots[slot].kind == want:
                    target = IoAddress(slot, want, addr.channel)
                    if target in self._inputs:
                        self._inputs[target] = InputPoint(value)
                    break

    def close(self) -> None:
        with self._lock:
            self.outputs = {}
