"""WHMI-Link server: accepts connections, authenticates them and runs sessions.

Message semantics are delegated to a :class:`SessionHandler` (the receiver).
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from typing import Protocol

from ..common.codes import ClientKind, Role
from . import crypto
from .codec import ProtocolError, Reader, Writer
from .credentials import CredentialStore
from .frames import DEFAULT_PORT, PROTOCOL_VERSION, ErrorCode, MsgType
from .messages import SessionInfo, encode_error
from .stream import LinkStream

log = logging.getLogger(__name__)

HANDSHAKE_TIMEOUT = 5.0
AUTH_FAIL_DELAY = 1.0
MAX_QUEUED_REQUESTS = 64


class Session:
    _ids = itertools.count(1)

    def __init__(self, stream: LinkStream, client_id: str, role: Role, kind: ClientKind) -> None:
        self.id = next(self._ids)
        self.stream = stream
        self.client_id = client_id
        self.role = role
        self.kind = kind
        self.opened_at = time.monotonic()
        self.closed = asyncio.Event()

    def __repr__(self) -> str:
        return f"<Session {self.id} {self.client_id}@{self.stream.peer} {self.role.name}>"

    async def send(self, mtype: MsgType, payload: bytes = b"") -> None:
        await self.stream.send(mtype, payload)

    async def send_error(self, code: ErrorCode, message: str) -> None:
        try:
            await self.send(MsgType.ERROR, encode_error(code, message))
        except (ConnectionError, OSError):
            pass

    async def close(self) -> None:
        await self.stream.close()


class SessionHandler(Protocol):
    def session_info(self, session: Session) -> SessionInfo: ...

    async def on_open(self, session: Session) -> None: ...

    async def on_message(self, session: Session, mtype: MsgType, payload: bytes) -> None: ...

    async def on_close(self, session: Session) -> None: ...


class LinkServer:
    def __init__(
        self,
        handler: SessionHandler,
        credentials: CredentialStore,
        receiver_id: str,
        *,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        heartbeat_ms: int = 250,
        watchdog_ms: int = 1000,
        max_sessions: int = 16,
    ) -> None:
        self.handler = handler
        self.credentials = credentials
        self.receiver_id = receiver_id
        self.host = host
        self.port = port
        self.heartbeat_ms = heartbeat_ms
        self.watchdog_ms = watchdog_ms
        self.max_sessions = max_sessions
        self.sessions: set[Session] = set()
        self._pending = 0
        self._server: asyncio.base_events.Server | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def bound_port(self) -> int:
        assert self._server is not None
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._on_connect, self.host, self.port)
        log.info("WHMI-Link listening on %s:%s", self.host, self.bound_port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
        for s in list(self.sessions):
            await s.send_error(ErrorCode.SHUTDOWN, "receiver shutting down")
            await s.close()
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._server:
            # Since Python 3.12 this waits for all connections, so it must come last.
            await self._server.wait_closed()

    async def broadcast(self, mtype: MsgType, payload: bytes) -> None:
        for s in list(self.sessions):
            try:
                await s.send(mtype, payload)
            except (ConnectionError, OSError):
                pass

    # ------------------------------------------------------------------ connection

    async def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        stream = LinkStream(reader, writer, is_client=False)
        try:
            if len(self.sessions) + self._pending >= self.max_sessions:
                log.warning("rejecting %s: session limit reached", stream.peer)
                return
            self._pending += 1
            try:
                session = await asyncio.wait_for(self._handshake(stream), HANDSHAKE_TIMEOUT)
            finally:
                self._pending -= 1
            if session is None:
                return
            await self._run_session(session)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError, ProtocolError) as e:
            log.info("connection %s ended during handshake: %s", stream.peer, e)
        finally:
            await stream.close()
            self._tasks.discard(task)

    async def _handshake(self, stream: LinkStream) -> Session | None:
        mtype, payload = await stream.recv()
        if mtype is not MsgType.HELLO:
            raise ProtocolError("expected HELLO")
        r = Reader(payload)
        ver = r.u8()
        kind_code = r.u8()
        client_id = r.str8()
        client_nonce = r.raw(crypto.NONCE_SIZE)
        r.done()
        if ver != PROTOCOL_VERSION:
            raise ProtocolError(f"client speaks protocol {ver}")
        try:
            kind = ClientKind(kind_code)
        except ValueError:
            raise ProtocolError("invalid client kind") from None

        server_nonce = crypto.new_nonce()
        await stream.send(MsgType.CHALLENGE, Writer().str8(self.receiver_id).raw(server_nonce).getvalue())

        mtype, payload = await stream.recv()
        if mtype is not MsgType.AUTH:
            raise ProtocolError("expected AUTH")
        r = Reader(payload)
        mac = r.raw(crypto.MAC_SIZE)
        r.done()

        ctx = crypto.auth_context(client_nonce, server_nonce, client_id, self.receiver_id)
        cred = self.credentials.get(client_id)
        # Always compute a MAC so unknown ids and wrong keys take the same time.
        key = cred.key if cred else bytes(crypto.KEY_SIZE)
        ok = crypto.macs_equal(mac, crypto.client_mac(key, ctx)) and cred is not None
        if not ok:
            log.warning("authentication failed for client %r from %s", client_id, stream.peer)
            await asyncio.sleep(AUTH_FAIL_DELAY)
            await stream.send(MsgType.AUTH_FAIL)
            return None

        await stream.send(MsgType.AUTH_OK, crypto.server_mac(key, ctx))
        stream.enable_encryption(*crypto.derive_keys(key, client_nonce, server_nonce))
        session = Session(stream, client_id, cred.role, kind)
        await session.send(MsgType.SESSION_INFO, self.handler.session_info(session).encode())
        return session

    async def _run_session(self, session: Session) -> None:
        log.info("session opened: %r", session)
        self.sessions.add(session)
        # Requests are processed in order by a worker so that a slow request (a PLC
        # write) never stops the reader from consuming heartbeats.
        queue: asyncio.Queue[tuple[MsgType, bytes]] = asyncio.Queue(MAX_QUEUED_REQUESTS)
        keepalive = asyncio.create_task(self._keepalive(session))
        worker = asyncio.create_task(self._worker(session, queue))
        try:
            await self.handler.on_open(session)
            while True:
                mtype, payload = await session.stream.recv()
                if mtype is MsgType.HEARTBEAT:
                    continue
                if worker.done():
                    break
                try:
                    queue.put_nowait((mtype, payload))
                except asyncio.QueueFull:
                    raise ProtocolError("too many outstanding requests") from None
        except ProtocolError as e:
            log.warning("protocol error from %r: %s", session, e)
            await session.send_error(ErrorCode.PROTOCOL, str(e))
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            keepalive.cancel()
            worker.cancel()
            await asyncio.gather(keepalive, worker, return_exceptions=True)
            self.sessions.discard(session)
            await session.close()
            session.closed.set()
            log.info("session closed: %r", session)
            try:
                await self.handler.on_close(session)
            except Exception:
                log.exception("on_close handler failed for %r", session)

    async def _worker(self, session: Session, queue: asyncio.Queue[tuple[MsgType, bytes]]) -> None:
        try:
            while True:
                mtype, payload = await queue.get()
                try:
                    await self.handler.on_message(session, mtype, payload)
                except ProtocolError as e:
                    log.warning("protocol error from %r: %s", session, e)
                    await session.send_error(ErrorCode.PROTOCOL, str(e))
                    await session.close()
                    return
                except (ConnectionError, OSError):
                    await session.close()
                    return
                except Exception:
                    log.exception("handler failed on %s from %r", mtype.name, session)
                    await session.send_error(ErrorCode.INTERNAL, f"internal error handling {mtype.name}")
        except asyncio.CancelledError:
            pass

    async def _keepalive(self, session: Session) -> None:
        """Send heartbeats and enforce the watchdog for one session."""
        stream = session.stream
        try:
            while not stream.closed:
                hb = self.heartbeat_ms / 1000
                wd = self.watchdog_ms / 1000
                await asyncio.sleep(min(hb, wd) / 4)
                now = time.monotonic()
                if now - stream.last_rx > wd:
                    log.warning("watchdog expired for %r (%.0f ms silent)", session, (now - stream.last_rx) * 1000)
                    await stream.close()  # unblocks the reader loop -> link-loss handling
                    return
                if now - stream.last_tx >= hb:
                    await stream.send(MsgType.HEARTBEAT)
        except (ConnectionError, OSError):
            await stream.close()
        except asyncio.CancelledError:
            pass
