"""Bounds-checked little-endian binary reader/writer for WHMI-Link payloads."""

from __future__ import annotations

import struct
from typing import Any

from ..common.datatypes import DataType, coerce


class ProtocolError(Exception):
    """Malformed or unexpected data on the link; the connection must be closed."""


_U8 = struct.Struct("<B")
_U16 = struct.Struct("<H")
_U32 = struct.Struct("<I")
_I64 = struct.Struct("<q")

_VALUE_STRUCTS = {dt: struct.Struct(dt.struct_format) for dt in DataType if dt.struct_format}


class Writer:
    __slots__ = ("_buf",)

    def __init__(self) -> None:
        self._buf = bytearray()

    def u8(self, v: int) -> Writer:
        self._buf += _U8.pack(v)
        return self

    def u16(self, v: int) -> Writer:
        self._buf += _U16.pack(v)
        return self

    def u32(self, v: int) -> Writer:
        self._buf += _U32.pack(v)
        return self

    def i64(self, v: int) -> Writer:
        self._buf += _I64.pack(v)
        return self

    def raw(self, b: bytes) -> Writer:
        self._buf += b
        return self

    def blob16(self, b: bytes) -> Writer:
        if len(b) > 0xFFFF:
            raise ValueError("blob too long")
        self.u16(len(b))
        self._buf += b
        return self

    def str8(self, s: str) -> Writer:
        b = s.encode("utf-8")
        if len(b) > 0xFF:
            raise ValueError("string too long for str8")
        self.u8(len(b))
        self._buf += b
        return self

    def str16(self, s: str) -> Writer:
        return self.blob16(s.encode("utf-8"))

    def value(self, dtype: DataType, v: Any) -> Writer:
        """Write a typed Value (type code + data). ``v`` is coerced to ``dtype``."""
        v = coerce(dtype, v)
        self.u8(int(dtype))
        if dtype is DataType.STRING:
            self.str16(v)
        else:
            self._buf += _VALUE_STRUCTS[dtype].pack(int(v) if dtype is DataType.BOOL else v)
        return self

    def __len__(self) -> int:
        return len(self._buf)

    def getvalue(self) -> bytes:
        return bytes(self._buf)


class Reader:
    __slots__ = ("_data", "_pos")

    def __init__(self, data: bytes) -> None:
        self._data = memoryview(data)
        self._pos = 0

    def _take(self, n: int) -> memoryview:
        if n < 0 or self._pos + n > len(self._data):
            raise ProtocolError("payload truncated")
        mv = self._data[self._pos:self._pos + n]
        self._pos += n
        return mv

    def u8(self) -> int:
        return _U8.unpack(self._take(1))[0]

    def u16(self) -> int:
        return _U16.unpack(self._take(2))[0]

    def u32(self) -> int:
        return _U32.unpack(self._take(4))[0]

    def i64(self) -> int:
        return _I64.unpack(self._take(8))[0]

    def raw(self, n: int) -> bytes:
        return bytes(self._take(n))

    def blob16(self) -> bytes:
        return self.raw(self.u16())

    def _utf8(self, b: bytes) -> str:
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            raise ProtocolError("invalid UTF-8") from None

    def str8(self) -> str:
        return self._utf8(self.raw(self.u8()))

    def str16(self) -> str:
        return self._utf8(self.blob16())

    def value(self) -> tuple[DataType, Any]:
        code = self.u8()
        try:
            dtype = DataType(code)
        except ValueError:
            raise ProtocolError(f"unknown value type code 0x{code:02X}") from None
        if dtype is DataType.STRING:
            return dtype, self.str16()
        v = _VALUE_STRUCTS[dtype].unpack(self._take(_VALUE_STRUCTS[dtype].size))[0]
        if dtype is DataType.BOOL:
            if v > 1:
                raise ProtocolError("BOOL value must be 0 or 1")
            v = bool(v)
        return dtype, v

    @property
    def remaining(self) -> int:
        return len(self._data) - self._pos

    def done(self) -> None:
        if self.remaining:
            raise ProtocolError(f"{self.remaining} unexpected trailing byte(s)")
