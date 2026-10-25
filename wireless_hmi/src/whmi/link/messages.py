"""Encoders/decoders for WHMI-Link message payloads (see docs/PROTOCOL.md §4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..common.codes import Quality, Role, Status
from ..common.datatypes import DataType
from .codec import ProtocolError, Reader, Writer
from .frames import MAX_PLAINTEXT

MAX_READ_TAGS = 100  # per READ_REQ / WRITE_REQ: keeps responses well below MAX_PLAINTEXT
MAX_SUBSCRIBE_TAGS = 4000
HASH_SIZE = 32
NO_PROJECT_HASH = bytes(HASH_SIZE)


@dataclass
class TagValue:
    tag_id: int
    quality: Quality
    timestamp_ms: int
    data_type: DataType
    value: Any

    def write(self, w: Writer) -> None:
        w.u16(self.tag_id).u8(int(self.quality)).i64(self.timestamp_ms).value(self.data_type, self.value)

    @classmethod
    def read(cls, r: Reader) -> TagValue:
        tag_id = r.u16()
        q = r.u8()
        try:
            quality = Quality(q)
        except ValueError:
            quality = Quality.BAD if q & 0x80 else Quality.UNCERTAIN
        ts = r.i64()
        dtype, value = r.value()
        return cls(tag_id, quality, ts, dtype, value)


@dataclass
class SessionInfo:
    role: Role
    heartbeat_ms: int
    watchdog_ms: int
    project_hash: bytes
    project_size: int

    def encode(self) -> bytes:
        return (Writer().u8(int(self.role)).u16(self.heartbeat_ms).u16(self.watchdog_ms)
                .raw(self.project_hash).u32(self.project_size).getvalue())

    @classmethod
    def decode(cls, payload: bytes) -> SessionInfo:
        r = Reader(payload)
        try:
            role = Role(r.u8())
        except ValueError:
            raise ProtocolError("invalid role") from None
        info = cls(role, r.u16(), r.u16(), r.raw(HASH_SIZE), r.u32())
        r.done()
        return info

    @property
    def has_project(self) -> bool:
        return self.project_hash != NO_PROJECT_HASH


def _status(code: int) -> Status:
    try:
        return Status(code)
    except ValueError:
        return Status.INTERNAL


# ---------------------------------------------------------------------------- id lists


def encode_id_list(req_id: int, ids: Iterable[int], *, interval_ms: int | None = None) -> bytes:
    ids = list(ids)
    w = Writer().u16(req_id)
    if interval_ms is not None:
        w.u16(interval_ms)
    w.u16(len(ids))
    for i in ids:
        w.u16(i)
    return w.getvalue()


def decode_id_list(payload: bytes, *, with_interval: bool = False, limit: int) -> tuple[int, int, list[int]]:
    r = Reader(payload)
    req_id = r.u16()
    interval = r.u16() if with_interval else 0
    n = r.u16()
    if n > limit:
        raise ProtocolError(f"too many tags in request ({n} > {limit})")
    ids = [r.u16() for _ in range(n)]
    r.done()
    return req_id, interval, ids


def encode_status_list(req_id: int, results: Iterable[tuple[int, Status]]) -> bytes:
    results = list(results)
    w = Writer().u16(req_id).u16(len(results))
    for tag_id, status in results:
        w.u16(tag_id).u8(int(status))
    return w.getvalue()


def decode_status_list(payload: bytes) -> tuple[int, list[tuple[int, Status]]]:
    r = Reader(payload)
    req_id = r.u16()
    n = r.u16()
    out = []
    for _ in range(n):
        tag_id = r.u16()
        out.append((tag_id, _status(r.u8())))
    r.done()
    return req_id, out


# ---------------------------------------------------------------------------- values


def encode_read_rsp(req_id: int, values: list[TagValue]) -> bytes:
    w = Writer().u16(req_id).u16(len(values))
    for v in values:
        v.write(w)
    return w.getvalue()


def decode_read_rsp(payload: bytes) -> tuple[int, list[TagValue]]:
    r = Reader(payload)
    req_id = r.u16()
    values = [TagValue.read(r) for _ in range(r.u16())]
    r.done()
    return req_id, values


def encode_data_updates(values: list[TagValue]) -> list[bytes]:
    """Pack values into as many DATA_UPDATE payloads as needed to respect the frame size."""
    payloads: list[bytes] = []
    chunk = Writer()
    count = 0
    for v in values:
        item = Writer()
        v.write(item)
        if count and 2 + len(chunk) + len(item) > MAX_PLAINTEXT:
            payloads.append(Writer().u16(count).raw(chunk.getvalue()).getvalue())
            chunk, count = Writer(), 0
        chunk.raw(item.getvalue())
        count += 1
    if count:
        payloads.append(Writer().u16(count).raw(chunk.getvalue()).getvalue())
    return payloads


def decode_data_update(payload: bytes) -> list[TagValue]:
    r = Reader(payload)
    values = [TagValue.read(r) for _ in range(r.u16())]
    r.done()
    return values


def encode_write_req(req_id: int, writes: list[tuple[int, DataType, Any]]) -> bytes:
    w = Writer().u16(req_id).u16(len(writes))
    for tag_id, dtype, value in writes:
        w.u16(tag_id).value(dtype, value)
    return w.getvalue()


def decode_write_req(payload: bytes) -> tuple[int, list[tuple[int, DataType, Any]]]:
    r = Reader(payload)
    req_id = r.u16()
    n = r.u16()
    if n > MAX_READ_TAGS:
        raise ProtocolError(f"too many tags in write ({n} > {MAX_READ_TAGS})")
    writes = []
    for _ in range(n):
        tag_id = r.u16()
        dtype, value = r.value()
        writes.append((tag_id, dtype, value))
    r.done()
    return req_id, writes


def encode_momentary(req_id: int, tag_id: int, dtype: DataType, value: Any, hold_ms: int) -> bytes:
    return Writer().u16(req_id).u16(tag_id).value(dtype, value).u16(hold_ms).getvalue()


def decode_momentary(payload: bytes) -> tuple[int, int, DataType, Any, int]:
    r = Reader(payload)
    req_id = r.u16()
    tag_id = r.u16()
    dtype, value = r.value()
    hold_ms = r.u16()
    r.done()
    return req_id, tag_id, dtype, value, hold_ms


# ---------------------------------------------------------------------------- project transfer


def encode_project_info(req_id: int, project_hash: bytes, size: int, name: str) -> bytes:
    return Writer().u16(req_id).raw(project_hash).u32(size).str8(name[:255]).getvalue()


def decode_project_info(payload: bytes) -> tuple[int, bytes, int, str]:
    r = Reader(payload)
    out = (r.u16(), r.raw(HASH_SIZE), r.u32(), r.str8())
    r.done()
    return out


def encode_project_data(req_id: int, status: Status, offset: int, data: bytes) -> bytes:
    return Writer().u16(req_id).u8(int(status)).u32(offset).blob16(data).getvalue()


def decode_project_data(payload: bytes) -> tuple[int, Status, int, bytes]:
    r = Reader(payload)
    req_id = r.u16()
    status = _status(r.u8())
    offset = r.u32()
    data = r.blob16()
    r.done()
    return req_id, status, offset, data


def encode_project_rsp(req_id: int, status: Status, message: str = "") -> bytes:
    return Writer().u16(req_id).u8(int(status)).str16(message[:4000]).getvalue()


def decode_project_rsp(payload: bytes) -> tuple[int, Status, str]:
    r = Reader(payload)
    req_id = r.u16()
    status = _status(r.u8())
    msg = r.str16()
    r.done()
    return req_id, status, msg


def encode_error(code: int, message: str) -> bytes:
    return Writer().u16(code).str16(message[:1000]).getvalue()


def decode_error(payload: bytes) -> tuple[int, str]:
    r = Reader(payload)
    out = (r.u16(), r.str16())
    r.done()
    return out
