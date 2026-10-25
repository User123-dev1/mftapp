"""Receiver side of WHMI-Link: implements the message semantics for each session."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..common.codes import Quality, Role, Status
from ..common.datatypes import DataType
from ..common.project import CompiledProject, ProjectError
from ..link.codec import ProtocolError, Reader
from ..link.frames import PROJECT_CHUNK, MsgType
from ..link.messages import (
    MAX_READ_TAGS,
    MAX_SUBSCRIBE_TAGS,
    NO_PROJECT_HASH,
    SessionInfo,
    TagValue,
    decode_id_list,
    decode_momentary,
    decode_write_req,
    encode_data_updates,
    encode_project_data,
    encode_project_info,
    encode_project_rsp,
    encode_read_rsp,
    encode_status_list,
)
from ..link.server import Session
from .tagdb import TagState, now_ms

if TYPE_CHECKING:
    from .service import Receiver

log = logging.getLogger(__name__)

MIN_INTERVAL_MS = 20
MAX_INTERVAL_MS = 10000
DEFAULT_INTERVAL_MS = 100


@dataclass
class _Upload:
    size: int
    digest: bytes
    data: bytearray = field(default_factory=bytearray)


class _SessionState:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.subs: set[int] = set()
        self.dirty: set[int] = set()
        self.last_sent: dict[int, tuple[object, Quality]] = {}
        self.interval_ms = DEFAULT_INTERVAL_MS
        self.wake = asyncio.Event()
        self.upload: _Upload | None = None
        self.publisher: asyncio.Task | None = None


class LinkService:
    """Session handler plugged into :class:`~whmi.link.server.LinkServer`."""

    def __init__(self, receiver: Receiver) -> None:
        self.rx = receiver
        self.states: dict[int, _SessionState] = {}

    # ------------------------------------------------------------------ handler protocol

    def session_info(self, session: Session) -> SessionInfo:
        p = self.rx.project
        return SessionInfo(
            role=session.role,
            heartbeat_ms=self.rx.server.heartbeat_ms,
            watchdog_ms=self.rx.server.watchdog_ms,
            project_hash=p.hash if p else NO_PROJECT_HASH,
            project_size=len(p.data) if p else 0,
        )

    async def on_open(self, session: Session) -> None:
        st = _SessionState(session)
        self.states[session.id] = st
        st.publisher = asyncio.create_task(self._publisher(st), name=f"pub-{session.id}")

    async def on_close(self, session: Session) -> None:
        st = self.states.pop(session.id, None)
        if st and st.publisher:
            st.publisher.cancel()
            await asyncio.gather(st.publisher, return_exceptions=True)
        await self.rx.on_session_closed(session)

    async def on_message(self, session: Session, mtype: MsgType, payload: bytes) -> None:
        st = self.states[session.id]
        handler = self._handlers.get(mtype)
        if handler is None:
            raise ProtocolError(f"{mtype.name} is not a client request")
        await handler(self, st, payload)

    # ------------------------------------------------------------------ change tracking

    def on_tag_change(self, state: TagState) -> None:
        tid = state.tag.tag_id
        for st in self.states.values():
            if tid in st.subs:
                st.dirty.add(tid)
                st.wake.set()

    async def project_changed(self, compiled: CompiledProject) -> None:
        for st in self.states.values():
            st.subs.clear()
            st.dirty.clear()
            st.last_sent.clear()
            st.upload = None
        await self.rx.server.broadcast(MsgType.PROJECT_CHANGED, compiled.hash)

    async def _publisher(self, st: _SessionState) -> None:
        try:
            while True:
                await st.wake.wait()
                await asyncio.sleep(st.interval_ms / 1000)  # coalesce changes within one interval
                st.wake.clear()
                if not st.dirty:
                    continue
                ids, st.dirty = st.dirty, set()
                values = [v for v in (self._filtered(st, tid) for tid in sorted(ids)) if v is not None]
                await self._send_values(st, values)
        except (ConnectionError, OSError):
            pass

    def _filtered(self, st: _SessionState, tid: int) -> TagValue | None:
        """Current value of ``tid`` unless it is within the tag's deadband of what was last sent."""
        state = self.rx.tagdb.get(tid) if self.rx.tagdb else None
        if state is None:
            return None
        last = st.last_sent.get(tid)
        tag = state.tag
        if (last is not None and tag.deadband > 0 and tag.data_type.is_numeric
                and last[1] == state.quality and abs(state.value - last[0]) < tag.deadband):
            return None
        return state.to_tag_value()

    async def _send_values(self, st: _SessionState, values: list[TagValue]) -> None:
        for v in values:
            st.last_sent[v.tag_id] = (v.value, v.quality)
        for payload in encode_data_updates(values):
            await st.session.send(MsgType.DATA_UPDATE, payload)

    # ------------------------------------------------------------------ tag services

    def _value_of(self, tid: int) -> TagValue:
        state = self.rx.tagdb.get(tid) if self.rx.tagdb else None
        if state is None:
            return TagValue(tid, Quality.BAD_CONFIG, now_ms(), DataType.DINT, 0)
        return state.to_tag_value()

    async def _read(self, st: _SessionState, payload: bytes) -> None:
        req_id, _, ids = decode_id_list(payload, limit=MAX_READ_TAGS)
        await st.session.send(MsgType.READ_RSP, encode_read_rsp(req_id, [self._value_of(t) for t in ids]))

    async def _subscribe(self, st: _SessionState, payload: bytes) -> None:
        req_id, interval, ids = decode_id_list(payload, with_interval=True, limit=MAX_SUBSCRIBE_TAGS)
        interval = min(max(interval or DEFAULT_INTERVAL_MS, MIN_INTERVAL_MS), MAX_INTERVAL_MS)
        st.interval_ms = interval if not st.subs else min(st.interval_ms, interval)
        results: list[tuple[int, Status]] = []
        accepted: list[int] = []
        for tid in ids:
            if self.rx.tagdb is not None and self.rx.tagdb.get(tid) is not None:
                st.subs.add(tid)
                accepted.append(tid)
                results.append((tid, Status.OK))
            else:
                results.append((tid, Status.BAD_TAG))
        await st.session.send(MsgType.SUB_RSP, encode_status_list(req_id, results))
        # Initial snapshot so the client never shows an empty value.
        await self._send_values(st, [self._value_of(t) for t in accepted])

    async def _unsubscribe(self, st: _SessionState, payload: bytes) -> None:
        req_id, _, ids = decode_id_list(payload, limit=MAX_SUBSCRIBE_TAGS)
        for tid in ids:
            st.subs.discard(tid)
            st.dirty.discard(tid)
            st.last_sent.pop(tid, None)
        await st.session.send(MsgType.SUB_RSP, encode_status_list(req_id, [(t, Status.OK) for t in ids]))

    async def _write(self, st: _SessionState, payload: bytes) -> None:
        req_id, writes = decode_write_req(payload)
        results = []
        for tid, _dtype, value in writes:
            results.append((tid, await self.rx.write_tag(st.session, tid, value)))
        await st.session.send(MsgType.WRITE_RSP, encode_status_list(req_id, results))

    async def _momentary(self, st: _SessionState, payload: bytes) -> None:
        req_id, tid, _dtype, value, hold_ms = decode_momentary(payload)
        status = await self.rx.write_tag(st.session, tid, value, hold_ms=hold_ms)
        await st.session.send(MsgType.WRITE_RSP, encode_status_list(req_id, [(tid, status)]))

    # ------------------------------------------------------------------ project services

    async def _project_info(self, st: _SessionState, payload: bytes) -> None:
        r = Reader(payload)
        req_id = r.u16()
        r.done()
        p = self.rx.project
        await st.session.send(MsgType.PROJECT_INFO, encode_project_info(
            req_id, p.hash if p else NO_PROJECT_HASH, len(p.data) if p else 0, p.name if p else ""))

    async def _project_read(self, st: _SessionState, payload: bytes) -> None:
        r = Reader(payload)
        req_id, offset, length = r.u16(), r.u32(), r.u16()
        r.done()
        p = self.rx.project
        if p is None or offset > len(p.data) or length == 0 or length > PROJECT_CHUNK:
            await st.session.send(MsgType.PROJECT_DATA, encode_project_data(req_id, Status.INVALID, offset, b""))
            return
        await st.session.send(MsgType.PROJECT_DATA,
                              encode_project_data(req_id, Status.OK, offset, p.data[offset:offset + length]))

    async def _reply(self, st: _SessionState, req_id: int, status: Status, msg: str = "") -> None:
        await st.session.send(MsgType.PROJECT_WRITE_RSP, encode_project_rsp(req_id, status, msg))

    async def _write_begin(self, st: _SessionState, payload: bytes) -> None:
        r = Reader(payload)
        req_id, size, digest = r.u16(), r.u32(), r.raw(32)
        r.done()
        if st.session.role < Role.ENGINEER:
            await self._reply(st, req_id, Status.NOT_PERMITTED, "project download requires the engineer role")
            return
        if not 0 < size <= self.rx.config.max_project_bytes:
            limit = self.rx.config.max_project_bytes
            await self._reply(st, req_id, Status.OUT_OF_RANGE, f"project size must be 1..{limit}")
            return
        st.upload = _Upload(size, digest)
        log.info("project download started by %s (%d bytes)", st.session.client_id, size)
        await self._reply(st, req_id, Status.OK)

    async def _write_chunk(self, st: _SessionState, payload: bytes) -> None:
        r = Reader(payload)
        req_id, offset, data = r.u16(), r.u32(), r.blob16()
        r.done()
        up = st.upload
        if up is None:
            await self._reply(st, req_id, Status.INVALID, "no download in progress")
            return
        if offset != len(up.data) or offset + len(data) > up.size:
            st.upload = None
            await self._reply(st, req_id, Status.INVALID, "chunk out of sequence; download aborted")
            return
        up.data += data
        await self._reply(st, req_id, Status.OK)

    async def _write_commit(self, st: _SessionState, payload: bytes) -> None:
        r = Reader(payload)
        req_id = r.u16()
        r.done()
        up, st.upload = st.upload, None
        if up is None or len(up.data) != up.size:
            await self._reply(st, req_id, Status.INVALID, "download incomplete")
            return
        if hashlib.sha256(up.data).digest() != up.digest:
            await self._reply(st, req_id, Status.INVALID, "hash mismatch")
            return
        try:
            compiled = CompiledProject.from_bytes(bytes(up.data))
        except ProjectError as e:
            await self._reply(st, req_id, Status.INVALID, str(e))
            return
        except ValueError as e:
            await self._reply(st, req_id, Status.INVALID, f"invalid project: {e}")
            return
        try:
            await self.rx.activate(compiled)
        except Exception as e:
            log.exception("project activation failed")
            await self._reply(st, req_id, Status.INTERNAL, f"activation failed: {e}")
            return
        log.info("project %r downloaded by %s and activated", compiled.name, st.session.client_id)
        await self._reply(st, req_id, Status.OK, f"activated {compiled.name} ({compiled.hash_hex[:12]})")

    _handlers = {
        MsgType.READ_REQ: _read,
        MsgType.SUBSCRIBE: _subscribe,
        MsgType.UNSUBSCRIBE: _unsubscribe,
        MsgType.WRITE_REQ: _write,
        MsgType.MOMENTARY: _momentary,
        MsgType.PROJECT_INFO_REQ: _project_info,
        MsgType.PROJECT_READ: _project_read,
        MsgType.PROJECT_WRITE_BEGIN: _write_begin,
        MsgType.PROJECT_WRITE_CHUNK: _write_chunk,
        MsgType.PROJECT_WRITE_COMMIT: _write_commit,
    }
