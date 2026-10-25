"""WHMI-Link authentication and session encryption primitives."""

from __future__ import annotations

import hashlib
import hmac
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .codec import ProtocolError, Writer

KEY_SIZE = 32
NONCE_SIZE = 16
MAC_SIZE = 32

DIR_C2S = b"C2S\x00"
DIR_S2C = b"S2C\x00"


def generate_key() -> bytes:
    return os.urandom(KEY_SIZE)


def new_nonce() -> bytes:
    return os.urandom(NONCE_SIZE)


def parse_key(text: str) -> bytes:
    try:
        key = bytes.fromhex(text.strip())
    except ValueError:
        raise ValueError("key must be hex encoded") from None
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes ({KEY_SIZE * 2} hex characters)")
    return key


def auth_context(client_nonce: bytes, server_nonce: bytes, client_id: str, receiver_id: str) -> bytes:
    return Writer().raw(client_nonce).raw(server_nonce).str8(client_id).str8(receiver_id).getvalue()


def client_mac(key: bytes, ctx: bytes) -> bytes:
    return hmac.new(key, b"WHMI-AUTH-C" + ctx, hashlib.sha256).digest()


def server_mac(key: bytes, ctx: bytes) -> bytes:
    return hmac.new(key, b"WHMI-AUTH-S" + ctx, hashlib.sha256).digest()


def macs_equal(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)


def derive_keys(key: bytes, client_nonce: bytes, server_nonce: bytes) -> tuple[bytes, bytes]:
    """Return ``(k_c2s, k_s2c)``."""

    def hkdf(info: bytes) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=KEY_SIZE, salt=client_nonce + server_nonce,
                    info=info).derive(key)

    return hkdf(b"WHMI-Link v1 c2s"), hkdf(b"WHMI-Link v1 s2c")


class FrameCipher:
    """AES-256-GCM for one direction of a session."""

    def __init__(self, key: bytes, direction: bytes) -> None:
        self._aead = AESGCM(key)
        self._dir = direction

    def _nonce(self, seq: int) -> bytes:
        return self._dir + struct.pack("<Q", seq)

    def seal(self, seq: int, header: bytes, plaintext: bytes) -> bytes:
        return self._aead.encrypt(self._nonce(seq), plaintext, header)

    def open(self, seq: int, header: bytes, ciphertext: bytes) -> bytes:
        try:
            return self._aead.decrypt(self._nonce(seq), ciphertext, header)
        except InvalidTag:
            raise ProtocolError("frame authentication failed") from None
