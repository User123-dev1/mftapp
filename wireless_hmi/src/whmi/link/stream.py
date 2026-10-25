"""Framed, optionally encrypted, sequence-checked WHMI-Link stream over asyncio TCP."""

from __future__ import annotations

import asyncio
import time

from .codec import ProtocolError
from .crypto import DIR_C2S, DIR_S2C, FrameCipher
from .frames import FLAG_ENCRYPTED, HANDSHAKE_TYPES, HEADER_SIZE, MAX_PLAINTEXT, FrameHeader, MsgType

_SEQ_MAX = 0xFFFFFFFF


class LinkStream:
    """One end of a WHMI-Link connection.

    Handles framing, sequence numbering and (after :meth:`enable_encryption`)
    AES-GCM sealing. It knows nothing about message semantics.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, is_client: bool) -> None:
        self._reader = reader
        self._writer = writer
        self._is_client = is_client
        self._tx_seq = 0
        self._rx_seq = 0
        self._tx_cipher: FrameCipher | None = None
        self._rx_cipher: FrameCipher | None = None
        self._send_lock = asyncio.Lock()
        self.last_rx = time.monotonic()
        self.last_tx = time.monotonic()
        self.closed = False

    @property
    def peer(self) -> str:
        info = self._writer.get_extra_info("peername")
        return f"{info[0]}:{info[1]}" if info else "?"

    @property
    def encrypted(self) -> bool:
        return self._tx_cipher is not None

    def enable_encryption(self, k_c2s: bytes, k_s2c: bytes) -> None:
        if self._is_client:
            self._tx_cipher = FrameCipher(k_c2s, DIR_C2S)
            self._rx_cipher = FrameCipher(k_s2c, DIR_S2C)
        else:
            self._tx_cipher = FrameCipher(k_s2c, DIR_S2C)
            self._rx_cipher = FrameCipher(k_c2s, DIR_C2S)
        # Encrypted frames start at seq 1 in each direction (PROTOCOL.md §1).
        self._tx_seq = 1
        self._rx_seq = 1

    async def send(self, mtype: MsgType, payload: bytes = b"") -> None:
        if len(payload) > MAX_PLAINTEXT:
            raise ValueError(f"payload too large ({len(payload)} bytes)")
        async with self._send_lock:
            if self.closed:
                raise ConnectionError("link closed")
            if self._tx_seq > _SEQ_MAX:
                raise ConnectionError("sequence number exhausted; reconnect required")
            seq = self._tx_seq
            self._tx_seq += 1
            if self._tx_cipher is None:
                if mtype not in HANDSHAKE_TYPES:
                    raise RuntimeError(f"refusing to send {mtype.name} unencrypted")
                header = FrameHeader(mtype, 0, seq, len(payload)).pack()
                body = payload
            else:
                header = FrameHeader(mtype, FLAG_ENCRYPTED, seq, len(payload) + 16).pack()
                body = self._tx_cipher.seal(seq, header, payload)
            self._writer.write(header + body)
            await self._writer.drain()
            self.last_tx = time.monotonic()

    async def recv(self) -> tuple[MsgType, bytes]:
        """Receive one frame. Raises ProtocolError / ConnectionError / IncompleteReadError."""
        raw_header = await self._reader.readexactly(HEADER_SIZE)
        header = FrameHeader.unpack(raw_header)
        body = await self._reader.readexactly(header.length)
        if header.seq != self._rx_seq:
            raise ProtocolError(f"sequence error: expected {self._rx_seq}, got {header.seq}")
        self._rx_seq += 1
        if self._rx_cipher is None:
            if header.encrypted:
                raise ProtocolError("unexpected encrypted frame during handshake")
            payload = body
        else:
            if not header.encrypted:
                raise ProtocolError("unencrypted frame in an encrypted session")
            payload = self._rx_cipher.open(header.seq, raw_header, body)
        try:
            mtype = MsgType(header.type)
        except ValueError:
            raise ProtocolError(f"unknown message type 0x{header.type:02X}") from None
        if self._rx_cipher is None and mtype not in HANDSHAKE_TYPES:
            raise ProtocolError(f"{mtype.name} not allowed before authentication")
        self.last_rx = time.monotonic()
        return mtype, payload

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._writer.close()
            await asyncio.wait_for(self._writer.wait_closed(), timeout=1.0)
        except (OSError, asyncio.TimeoutError, ConnectionError):
            pass
