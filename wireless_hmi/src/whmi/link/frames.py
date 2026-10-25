"""WHMI-Link frame header and message type constants (see docs/PROTOCOL.md)."""

from __future__ import annotations

import enum
import struct
from dataclasses import dataclass

from .codec import ProtocolError

MAGIC = b"WH"
PROTOCOL_VERSION = 1
DEFAULT_PORT = 47800

HEADER = struct.Struct("<2sBBBBIH")
HEADER_SIZE = HEADER.size  # 12
MAX_PAYLOAD = 16384
GCM_TAG_SIZE = 16
MAX_PLAINTEXT = MAX_PAYLOAD - GCM_TAG_SIZE
PROJECT_CHUNK = 8192

FLAG_ENCRYPTED = 0x01


class MsgType(enum.IntEnum):
    HELLO = 0x01
    CHALLENGE = 0x02
    AUTH = 0x03
    AUTH_OK = 0x04
    AUTH_FAIL = 0x05
    HEARTBEAT = 0x10
    SESSION_INFO = 0x11
    READ_REQ = 0x20
    READ_RSP = 0x21
    WRITE_REQ = 0x22
    WRITE_RSP = 0x23
    SUBSCRIBE = 0x24
    SUB_RSP = 0x25
    UNSUBSCRIBE = 0x26
    DATA_UPDATE = 0x30
    MOMENTARY = 0x31
    PROJECT_INFO_REQ = 0x40
    PROJECT_INFO = 0x41
    PROJECT_READ = 0x42
    PROJECT_DATA = 0x43
    PROJECT_WRITE_BEGIN = 0x44
    PROJECT_WRITE_CHUNK = 0x45
    PROJECT_WRITE_COMMIT = 0x46
    PROJECT_WRITE_RSP = 0x47
    PROJECT_CHANGED = 0x48
    ERROR = 0x7F


HANDSHAKE_TYPES = frozenset({MsgType.HELLO, MsgType.CHALLENGE, MsgType.AUTH, MsgType.AUTH_OK, MsgType.AUTH_FAIL})


class ErrorCode(enum.IntEnum):
    PROTOCOL = 1
    UNSUPPORTED = 2
    NOT_PERMITTED = 3
    INTERNAL = 4
    SHUTDOWN = 5


@dataclass(frozen=True)
class FrameHeader:
    type: int
    flags: int
    seq: int
    length: int
    version: int = PROTOCOL_VERSION

    def pack(self) -> bytes:
        return HEADER.pack(MAGIC, self.version, self.type, self.flags, 0, self.seq, self.length)

    @classmethod
    def unpack(cls, data: bytes) -> FrameHeader:
        magic, version, mtype, flags, reserved, seq, length = HEADER.unpack(data)
        if magic != MAGIC:
            raise ProtocolError("bad magic")
        if version != PROTOCOL_VERSION:
            raise ProtocolError(f"unsupported protocol version {version}")
        if reserved != 0 or flags & ~FLAG_ENCRYPTED:
            raise ProtocolError("reserved bits set")
        if length > MAX_PAYLOAD:
            raise ProtocolError(f"payload length {length} exceeds {MAX_PAYLOAD}")
        return cls(mtype, flags, seq, length, version)

    @property
    def encrypted(self) -> bool:
        return bool(self.flags & FLAG_ENCRYPTED)
