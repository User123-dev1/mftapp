"""WHMI-Link client used by the panel runtime, Studio and the CLI tools."""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import logging
import time
from typing import Any, Callable

from ..common.codes import ClientKind, Status
from ..common.datatypes import DataType
from . import crypto
from .codec import ProtocolError, Reader, Writer
from .frames import DEFAULT_PORT, PROJECT_CHUNK, PROTOCOL_VERSION, MsgType
from .messages import (
    MAX_READ_TAGS,
    MAX_SUBSCRIBE_TAGS,
    SessionInfo,
    TagValue,
    decode_data_update,
    decode_error,
    decode_project_data,
    decode_project_info,
    decode_project_rsp,
    decode_read_rsp,
    decode_status_list,
    encode_id_list,
    encode_momentary,
    encode_write_req,
)
from .stream import LinkStream

log = logging.getLogger(__name__)


class AuthenticationError(Exception):
    pass


class LinkError(Exception):
    """A request failed or the link went down while it was outstanding."""


_RESPONSES = {
    MsgType.READ_RSP: decode_read_rsp,
    MsgType.WRITE_RSP: decode_status_list,
    MsgType.SUB_RSP: decode_status_list,
}


class LinkClient:
    def __init__(
        self,
        host: str,
        client_id: str,
        key: bytes,
        *,
        port: int = DEFAULT_PORT,
        kind: ClientKind = ClientKind.PANEL,
        request_timeout: float = 3.0,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.key = key
        self.kind = kind
        self.request_timeout = request_timeout
        self.session: SessionInfo | None = None
        self.receiver_id = ""
        self.on_data: Callable[[list[TagValue]], None] | None = None
        self.on_project_changed: Callable[[bytes], None] | None = None
        self.on_disconnect: Callable[[BaseException | None], None] | None = None
        self._stream: LinkStream | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._req_ids = itertools.cycle(range(1, 0x10000))
        self._tasks: list[asyncio.Task] = []
        self._closed = asyncio.Event()
        self._close_reason: BaseException | None = None

    @property
    def connected(self) -> bool:
        return self._stream is not None and not self._stream.closed

    # ------------------------------------------------------------------ lifecycle

    async def connect(self, timeout: float = 5.0) -> SessionInfo:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), timeout)
        self._stream = LinkStream(reader, writer, is_client=True)
        try:
            await asyncio.wait_for(self._handshake(), timeout)
        except BaseException:
            await self._stream.close()
            raise
        self._closed.clear()
        self._tasks = [asyncio.create_task(self._reader_loop()), asyncio.create_task(self._keepalive())]
        return self.session  # type: ignore[return-value]

    async def _handshake(self) -> None:
        s = self._stream
        assert s is not None
        client_nonce = crypto.new_nonce()
        await s.send(MsgType.HELLO, Writer().u8(PROTOCOL_VERSION).u8(int(self.kind)).str8(self.client_id)
                     .raw(client_nonce).getvalue())
        mtype, payload = await s.recv()
        if mtype is not MsgType.CHALLENGE:
            raise ProtocolError("expected CHALLENGE")
        r = Reader(payload)
        self.receiver_id = r.str8()
        server_nonce = r.raw(crypto.NONCE_SIZE)
        r.done()
        ctx = crypto.auth_context(client_nonce, server_nonce, self.client_id, self.receiver_id)
        await s.send(MsgType.AUTH, crypto.client_mac(self.key, ctx))
        mtype, payload = await s.recv()
        if mtype is MsgType.AUTH_FAIL:
            raise AuthenticationError("receiver rejected the credentials")
        if mtype is not MsgType.AUTH_OK:
            raise ProtocolError("expected AUTH_OK")
        if not crypto.macs_equal(payload, crypto.server_mac(self.key, ctx)):
            raise AuthenticationError("receiver failed to prove knowledge of the key (possible impostor)")
        s.enable_encryption(*crypto.derive_keys(self.key, client_nonce, server_nonce))
        mtype, payload = await s.recv()
        if mtype is not MsgType.SESSION_INFO:
            raise ProtocolError("expected SESSION_INFO")
        self.session = SessionInfo.decode(payload)

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self._stream:
            await self._stream.close()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._fail_pending(LinkError("link closed"))
        self._closed.set()

    async def wait_closed(self) -> BaseException | None:
        await self._closed.wait()
        return self._close_reason

    async def __aenter__(self) -> LinkClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------ background

    async def _reader_loop(self) -> None:
        assert self._stream is not None
        reason: BaseException | None = None
        try:
            while True:
                mtype, payload = await self._stream.recv()
                self._dispatch(mtype, payload)
        except asyncio.CancelledError:
            raise
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as e:
            reason = e
        except ProtocolError as e:
            log.warning("protocol error from receiver: %s", e)
            reason = e
        finally:
            await self._stream.close()
            self._close_reason = reason
            self._fail_pending(LinkError(f"link lost: {reason}"))
            for t in self._tasks:
                if t is not asyncio.current_task():
                    t.cancel()
            if not self._closed.is_set():
                self._closed.set()
                if self.on_disconnect:
                    self.on_disconnect(reason)

    def _dispatch(self, mtype: MsgType, payload: bytes) -> None:
        if mtype is MsgType.HEARTBEAT:
            return
        if mtype is MsgType.DATA_UPDATE:
            values = decode_data_update(payload)
            if self.on_data:
                self.on_data(values)
            return
        if mtype is MsgType.PROJECT_CHANGED:
            if self.on_project_changed:
                self.on_project_changed(payload)
            return
        if mtype is MsgType.ERROR:
            code, msg = decode_error(payload)
            log.warning("receiver reported error %d: %s", code, msg)
            return
        if mtype in _RESPONSES:
            req_id, result = _RESPONSES[mtype](payload)
            self._resolve(req_id, result)
        elif mtype is MsgType.PROJECT_INFO:
            req_id, *rest = decode_project_info(payload)
            self._resolve(req_id, tuple(rest))
        elif mtype is MsgType.PROJECT_DATA:
            req_id, *rest = decode_project_data(payload)
            self._resolve(req_id, tuple(rest))
        elif mtype is MsgType.PROJECT_WRITE_RSP:
            req_id, *rest = decode_project_rsp(payload)
            self._resolve(req_id, tuple(rest))
        else:
            raise ProtocolError(f"unexpected {mtype.name} from receiver")

    def _resolve(self, req_id: int, result: Any) -> None:
        fut = self._pending.pop(req_id, None)
        if fut and not fut.done():
            fut.set_result(result)

    def _fail_pending(self, exc: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    async def _keepalive(self) -> None:
        s = self._stream
        assert s is not None and self.session is not None
        hb = self.session.heartbeat_ms / 1000
        wd = self.session.watchdog_ms / 1000
        try:
            while not s.closed:
                await asyncio.sleep(min(hb, wd) / 4)
                now = time.monotonic()
                if now - s.last_rx > wd:
                    log.warning("receiver watchdog expired")
                    await s.close()
                    return
                if now - s.last_tx >= hb:
                    await s.send(MsgType.HEARTBEAT)
        except (ConnectionError, OSError):
            await s.close()

    # ------------------------------------------------------------------ requests

    async def _request(self, mtype: MsgType, build: Callable[[int], bytes]) -> Any:
        if not self.connected:
            raise LinkError("not connected")
        req_id = next(self._req_ids)
        while req_id in self._pending:
            req_id = next(self._req_ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self._stream.send(mtype, build(req_id))  # type: ignore[union-attr]
            return await asyncio.wait_for(fut, self.request_timeout)
        except asyncio.TimeoutError:
            raise LinkError(f"{mtype.name} timed out") from None
        except (ConnectionError, OSError) as e:
            raise LinkError(str(e)) from e
        finally:
            self._pending.pop(req_id, None)

    async def read(self, tag_ids: list[int]) -> list[TagValue]:
        out: list[TagValue] = []
        for i in range(0, len(tag_ids), MAX_READ_TAGS):
            chunk = tag_ids[i:i + MAX_READ_TAGS]
            out.extend(await self._request(MsgType.READ_REQ, lambda rid, c=chunk: encode_id_list(rid, c)))
        return out

    async def write(self, writes: list[tuple[int, DataType, Any]]) -> list[tuple[int, Status]]:
        out: list[tuple[int, Status]] = []
        for i in range(0, len(writes), MAX_READ_TAGS):
            chunk = writes[i:i + MAX_READ_TAGS]
            out.extend(await self._request(MsgType.WRITE_REQ, lambda rid, c=chunk: encode_write_req(rid, c)))
        return out

    async def momentary(self, tag_id: int, dtype: DataType, value: Any, hold_ms: int) -> Status:
        result = await self._request(
            MsgType.MOMENTARY, lambda rid: encode_momentary(rid, tag_id, dtype, value, hold_ms))
        return result[0][1] if result else Status.INTERNAL

    async def subscribe(self, tag_ids: list[int], interval_ms: int = 100) -> list[tuple[int, Status]]:
        out: list[tuple[int, Status]] = []
        for i in range(0, len(tag_ids), MAX_SUBSCRIBE_TAGS):
            chunk = tag_ids[i:i + MAX_SUBSCRIBE_TAGS]
            out.extend(await self._request(
                MsgType.SUBSCRIBE, lambda rid, c=chunk: encode_id_list(rid, c, interval_ms=interval_ms)))
        return out

    async def unsubscribe(self, tag_ids: list[int]) -> list[tuple[int, Status]]:
        out: list[tuple[int, Status]] = []
        for i in range(0, len(tag_ids), MAX_SUBSCRIBE_TAGS):
            chunk = tag_ids[i:i + MAX_SUBSCRIBE_TAGS]
            out.extend(await self._request(MsgType.UNSUBSCRIBE, lambda rid, c=chunk: encode_id_list(rid, c)))
        return out

    async def project_info(self) -> tuple[bytes, int, str]:
        return await self._request(MsgType.PROJECT_INFO_REQ, lambda rid: Writer().u16(rid).getvalue())

    async def fetch_project(self) -> bytes:
        """Download the compiled project from the receiver and verify its hash."""
        expected_hash, size, _name = await self.project_info()
        if size == 0:
            raise LinkError("receiver has no project")
        buf = bytearray()
        while len(buf) < size:
            offset = len(buf)
            length = min(PROJECT_CHUNK, size - offset)
            status, off, data = await self._request(
                MsgType.PROJECT_READ, lambda rid, o=offset, n=length: Writer().u16(rid).u32(o).u16(n).getvalue())
            if status is not Status.OK or off != offset or not data:
                raise LinkError(f"project read failed at offset {offset}: {status.name}")
            buf += data
        if hashlib.sha256(buf).digest() != expected_hash:
            raise LinkError("project hash mismatch (project changed during transfer?)")
        return bytes(buf)

    async def download_project(self, data: bytes) -> str:
        """Upload and activate a compiled project (engineer role)."""
        digest = hashlib.sha256(data).digest()

        async def step(mtype: MsgType, build: Callable[[int], bytes]) -> str:
            status, msg = await self._request(mtype, build)
            if status is not Status.OK:
                raise LinkError(f"{mtype.name} failed: {status.name} {msg}".strip())
            return msg

        await step(MsgType.PROJECT_WRITE_BEGIN, lambda rid: Writer().u16(rid).u32(len(data)).raw(digest).getvalue())
        for offset in range(0, len(data), PROJECT_CHUNK):
            chunk = data[offset:offset + PROJECT_CHUNK]
            await step(MsgType.PROJECT_WRITE_CHUNK,
                       lambda rid, o=offset, c=chunk: Writer().u16(rid).u32(o).blob16(c).getvalue())
        old_timeout = self.request_timeout
        self.request_timeout = max(old_timeout, 15.0)  # activation reconnects PLCs etc.
        try:
            return await step(MsgType.PROJECT_WRITE_COMMIT, lambda rid: Writer().u16(rid).getvalue())
        finally:
            self.request_timeout = old_timeout
