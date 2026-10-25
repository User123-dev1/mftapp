"""WHMI-Link security and session behaviour."""

import asyncio

import pytest

from whmi.common.codes import Role
from whmi.link import server as server_mod
from whmi.link.client import AuthenticationError, LinkClient, LinkError
from whmi.link.codec import Reader, Writer
from whmi.link.crypto import NONCE_SIZE, generate_key
from whmi.link.frames import FrameHeader, MsgType
from whmi.link.stream import LinkStream

from .conftest import eventually


@pytest.fixture(autouse=True)
def fast_auth_fail(monkeypatch):
    monkeypatch.setattr(server_mod, "AUTH_FAIL_DELAY", 0.05)


async def test_handshake_and_session_info(rig):
    c = await rig.connect("panel1")
    assert c.receiver_id == "RX-TEST"
    assert c.session.role is Role.OPERATOR
    assert not c.session.has_project
    assert len(rig.receiver.server.sessions) == 1


async def test_wrong_key_rejected(rig):
    with pytest.raises(AuthenticationError):
        await rig.client("panel1", key=generate_key()).connect()
    assert not rig.receiver.server.sessions


async def test_unknown_client_rejected(rig):
    with pytest.raises(AuthenticationError):
        await rig.client("nobody", key=generate_key()).connect()


async def test_client_detects_impostor_receiver():
    """A fake receiver that doesn't know the key cannot complete the handshake."""

    async def fake(reader, writer):
        s = LinkStream(reader, writer, is_client=False)
        await s.recv()  # HELLO
        await s.send(MsgType.CHALLENGE, Writer().str8("EVIL").raw(bytes(NONCE_SIZE)).getvalue())
        await s.recv()  # AUTH
        await s.send(MsgType.AUTH_OK, bytes(32))
        await asyncio.sleep(1)

    srv = await asyncio.start_server(fake, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    try:
        with pytest.raises(AuthenticationError, match="impostor"):
            await LinkClient("127.0.0.1", "panel1", generate_key(), port=port).connect()
    finally:
        srv.close()


async def test_tampered_frame_closes_session(rig):
    c = await rig.connect("panel1")
    cipher = c._stream._tx_cipher
    real_seal = cipher.seal

    def corrupt(seq, header, plaintext):
        sealed = bytearray(real_seal(seq, header, plaintext))
        sealed[0] ^= 0x01
        return bytes(sealed)

    cipher.seal = corrupt
    with pytest.raises(LinkError):
        await c.project_info()
    await eventually(lambda: not rig.receiver.server.sessions)


async def test_replayed_sequence_closes_session(rig):
    c = await rig.connect("panel1")
    await c.project_info()
    c._stream._tx_seq -= 1  # resend an already used sequence number
    with pytest.raises(LinkError):
        await c.project_info()
    await eventually(lambda: not rig.receiver.server.sessions)


async def test_plaintext_request_before_auth_rejected(rig):
    reader, writer = await asyncio.open_connection("127.0.0.1", rig.port)
    payload = Writer().u16(1).getvalue()
    writer.write(FrameHeader(MsgType.PROJECT_INFO_REQ, 0, 0, len(payload)).pack() + payload)
    await writer.drain()
    assert await asyncio.wait_for(reader.read(), 2) == b""  # closed without a response
    writer.close()


async def test_handshake_timeout(rig, monkeypatch):
    monkeypatch.setattr(server_mod, "HANDSHAKE_TIMEOUT", 0.2)
    reader, writer = await asyncio.open_connection("127.0.0.1", rig.port)
    assert await asyncio.wait_for(reader.read(), 2) == b""
    writer.close()


async def test_watchdog_closes_silent_session(rig):
    await rig.load()
    c = await rig.connect("panel1")
    c._tasks[1].cancel()  # stop the client's heartbeats
    await eventually(lambda: not rig.receiver.server.sessions, timeout=3)
    await eventually(lambda: not c.connected)


async def test_heartbeats_keep_idle_session_alive(rig):
    await rig.load()
    c = await rig.connect("panel1")
    await asyncio.sleep(2.5)  # > 2 watchdog periods of silence at application level
    assert c.connected and len(rig.receiver.server.sessions) == 1


async def test_session_limit(rig):
    rig.receiver.server.max_sessions = 2
    await rig.connect("panel1")
    await rig.connect("panel2")
    with pytest.raises((ConnectionError, asyncio.IncompleteReadError, OSError, asyncio.TimeoutError)):
        await rig.client("viewer").connect(timeout=2)


async def test_read_request_limits(rig):
    await rig.load()
    c = await rig.connect("panel1")
    values = await c.read(list(range(1, 251)))  # client splits into 100-tag requests
    assert len(values) == 250
    assert Reader(b"").remaining == 0
