"""Tag data types.

The set and type codes mirror the CIP elementary data types used by Rockwell
Logix controllers, so PLC tags map 1:1 to HMI tags and the same codes are used
on the WHMI-Link wire protocol.
"""

from __future__ import annotations

import enum
import math
import struct
from dataclasses import dataclass
from typing import Any

STRING_MAX_LEN = 82  # Logix STRING capacity


class DataType(enum.IntEnum):
    BOOL = 0xC1
    SINT = 0xC2
    INT = 0xC3
    DINT = 0xC4
    LINT = 0xC5
    USINT = 0xC6
    UINT = 0xC7
    UDINT = 0xC8
    ULINT = 0xC9
    REAL = 0xCA
    LREAL = 0xCB
    STRING = 0xD0

    @classmethod
    def parse(cls, name: str | DataType) -> DataType:
        if isinstance(name, DataType):
            return name
        try:
            return cls[name.strip().upper()]
        except KeyError:
            raise ValueError(f"unknown data type {name!r}") from None

    @property
    def is_integer(self) -> bool:
        return self in _INT_RANGES

    @property
    def is_float(self) -> bool:
        return self in (DataType.REAL, DataType.LREAL)

    @property
    def is_numeric(self) -> bool:
        return self.is_integer or self.is_float

    @property
    def struct_format(self) -> str | None:
        return _STRUCT_FORMATS.get(self)

    def zero(self) -> Any:
        if self is DataType.BOOL:
            return False
        if self is DataType.STRING:
            return ""
        if self.is_float:
            return 0.0
        return 0


_INT_RANGES: dict[DataType, tuple[int, int]] = {
    DataType.SINT: (-(2**7), 2**7 - 1),
    DataType.INT: (-(2**15), 2**15 - 1),
    DataType.DINT: (-(2**31), 2**31 - 1),
    DataType.LINT: (-(2**63), 2**63 - 1),
    DataType.USINT: (0, 2**8 - 1),
    DataType.UINT: (0, 2**16 - 1),
    DataType.UDINT: (0, 2**32 - 1),
    DataType.ULINT: (0, 2**64 - 1),
}

_STRUCT_FORMATS: dict[DataType, str] = {
    DataType.BOOL: "<B",
    DataType.SINT: "<b",
    DataType.INT: "<h",
    DataType.DINT: "<i",
    DataType.LINT: "<q",
    DataType.USINT: "<B",
    DataType.UINT: "<H",
    DataType.UDINT: "<I",
    DataType.ULINT: "<Q",
    DataType.REAL: "<f",
    DataType.LREAL: "<d",
}

_REAL_MAX = 3.4028234663852886e38


class CoercionError(ValueError):
    """A value cannot be represented in the target data type."""

    def __init__(self, message: str, *, out_of_range: bool = False):
        super().__init__(message)
        self.out_of_range = out_of_range


def int_range(dtype: DataType) -> tuple[int, int]:
    return _INT_RANGES[dtype]


def coerce(dtype: DataType, value: Any) -> Any:
    """Convert ``value`` to the Python representation of ``dtype``.

    Conversions that would lose information (fractional value into an integer,
    out-of-range numbers, over-long strings) raise :class:`CoercionError`
    instead of silently truncating, since a truncated setpoint written to a
    machine is worse than a rejected write.
    """
    if dtype is DataType.BOOL:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in ("0", "1", "true", "false", "on", "off"):
            return value.strip().lower() in ("1", "true", "on")
        raise CoercionError(f"cannot convert {value!r} to BOOL")

    if dtype is DataType.STRING:
        if not isinstance(value, str):
            raise CoercionError(f"cannot convert {value!r} to STRING")
        if len(value.encode("utf-8")) > STRING_MAX_LEN:
            raise CoercionError(f"string longer than {STRING_MAX_LEN} bytes", out_of_range=True)
        return value

    if isinstance(value, str):
        try:
            value = float(value) if dtype.is_float else int(value.strip(), 0)
        except ValueError:
            raise CoercionError(f"cannot convert {value!r} to {dtype.name}") from None

    if isinstance(value, bool):
        value = int(value)
    if not isinstance(value, (int, float)):
        raise CoercionError(f"cannot convert {value!r} to {dtype.name}")

    if dtype.is_float:
        f = float(value)
        if dtype is DataType.REAL and math.isfinite(f) and abs(f) > _REAL_MAX:
            raise CoercionError(f"{value!r} out of range for REAL", out_of_range=True)
        if dtype is DataType.REAL:
            f = struct.unpack("<f", struct.pack("<f", f))[0]
        return f

    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise CoercionError(f"{value!r} is not an integer value for {dtype.name}")
        value = int(value)
    lo, hi = _INT_RANGES[dtype]
    if not lo <= value <= hi:
        raise CoercionError(f"{value} out of range for {dtype.name} [{lo}..{hi}]", out_of_range=True)
    return value


@dataclass(frozen=True)
class Scaling:
    """Linear scaling from raw (device) units to engineering units."""

    raw_min: float
    raw_max: float
    eng_min: float
    eng_max: float
    clamp: bool = False

    def __post_init__(self) -> None:
        if self.raw_max == self.raw_min:
            raise ValueError("scaling raw_min and raw_max must differ")
        if self.eng_max == self.eng_min:
            raise ValueError("scaling eng_min and eng_max must differ")

    def to_eng(self, raw: float) -> float:
        span = (raw - self.raw_min) / (self.raw_max - self.raw_min)
        if self.clamp:
            span = min(max(span, 0.0), 1.0)
        return self.eng_min + span * (self.eng_max - self.eng_min)

    def to_raw(self, eng: float) -> float:
        span = (eng - self.eng_min) / (self.eng_max - self.eng_min)
        if self.clamp:
            span = min(max(span, 0.0), 1.0)
        return self.raw_min + span * (self.raw_max - self.raw_min)
