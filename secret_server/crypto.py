"""
Cryptography for the vault.

Envelope encryption:
  master key (KEK, from key file / env / KMS)  --wraps-->  per-secret data key (DEK)
  DEK  --AES-256-GCM-->  each secret field value

Every ciphertext is bound to its location with AES-GCM associated data, so a
value copied to another secret or field fails to decrypt.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import string
from typing import Dict, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class CryptoError(Exception):
    pass


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


def generate_key() -> str:
    """Return a new random 256-bit key, base64 encoded."""
    return _b64e(AESGCM.generate_key(bit_length=256))


class KeyRing:
    """
    Holds master keys. The current key wraps new data keys; older keys remain
    available to unwrap existing data until they are re-wrapped.
    """

    def __init__(self, keys: Dict[str, bytes], current: str):
        if current not in keys:
            raise CryptoError(f"current key id '{current}' not present in key ring")
        for kid, key in keys.items():
            if len(key) != 32:
                raise CryptoError(f"master key '{kid}' must be 32 bytes")
            if ":" in kid:
                raise CryptoError("key ids may not contain ':'")
        self._keys = dict(keys)
        self.current_id = current

    @classmethod
    def from_settings(cls, settings) -> "KeyRing":
        if settings.master_key:
            return cls({"env": _b64d(settings.master_key)}, "env")
        if settings.master_key_file and os.path.exists(settings.master_key_file):
            with open(settings.master_key_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            keys = {kid: _b64d(value) for kid, value in data["keys"].items()}
            return cls(keys, data["current"])
        raise CryptoError(
            "No master key configured. Run `python -m secret_server init` "
            "or set SS_MASTER_KEY."
        )

    @staticmethod
    def write_key_file(path: str, keys: Dict[str, str], current: str) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"current": current, "keys": keys}, fh, indent=2)

    def wrap_dek(self, dek: bytes, aad: str) -> str:
        nonce = os.urandom(12)
        ct = AESGCM(self._keys[self.current_id]).encrypt(nonce, dek, aad.encode())
        return f"{self.current_id}:{_b64e(nonce + ct)}"

    def unwrap_dek(self, blob: str, aad: str) -> bytes:
        kid, _, payload = blob.partition(":")
        key = self._keys.get(kid)
        if key is None:
            raise CryptoError(f"master key '{kid}' is not loaded")
        raw = _b64d(payload)
        try:
            return AESGCM(key).decrypt(raw[:12], raw[12:], aad.encode())
        except Exception as exc:
            raise CryptoError("data key integrity check failed") from exc

    def needs_rewrap(self, blob: str) -> bool:
        return not blob.startswith(self.current_id + ":")

    def derive(self, purpose: str, kid: Optional[str] = None) -> bytes:
        """Derive a purpose-specific subkey (token signing, audit HMAC, ...)."""
        key = self._keys.get(kid or self.current_id)
        if key is None:
            raise CryptoError(f"master key '{kid}' is not loaded")
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=f"secret-server/{purpose}".encode(),
        ).derive(key)

    # Convenience for small values encrypted directly under the master key
    # (TOTP seeds, SSO client secrets).
    def encrypt_value(self, plaintext: str, aad: str) -> str:
        return self.wrap_dek(plaintext.encode(), aad)

    def decrypt_value(self, blob: str, aad: str) -> str:
        return self.unwrap_dek(blob, aad).decode()


def new_dek() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def encrypt_field(dek: bytes, plaintext: str, aad: str) -> str:
    nonce = os.urandom(12)
    return _b64e(nonce + AESGCM(dek).encrypt(nonce, plaintext.encode(), aad.encode()))


def decrypt_field(dek: bytes, blob: str, aad: str) -> str:
    raw = _b64d(blob)
    try:
        return AESGCM(dek).decrypt(raw[:12], raw[12:], aad.encode()).decode()
    except Exception as exc:
        raise CryptoError("field integrity check failed") from exc


# ---------------------------------------------------------------------------
# User password hashing (scrypt)
# ---------------------------------------------------------------------------

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 15, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        maxmem=64 * 1024 * 1024, dklen=32,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, encoded: Optional[str]) -> bool:
    if not encoded:
        # Burn comparable time so missing users are not distinguishable
        hash_password(password)
        return False
    try:
        _, n, r, p, salt, expected = encoded.split("$")
        digest = hashlib.scrypt(
            password.encode(), salt=_b64d(salt), n=int(n), r=int(r), p=int(p),
            maxmem=64 * 1024 * 1024, dklen=32,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, _b64d(expected))


# ---------------------------------------------------------------------------
# Password generation
# ---------------------------------------------------------------------------

DEFAULT_POLICY = {
    "length": 20,
    "uppercase": True,
    "lowercase": True,
    "digits": True,
    "symbols": True,
    "symbol_set": "!@#$%^&*()-_=+[]{}<>?",
    "exclude_ambiguous": True,
}

_AMBIGUOUS = set("Il1O0o")


def generate_password(policy: Optional[dict] = None) -> str:
    """Generate a password satisfying the policy (one char from each required class)."""
    policy = {**DEFAULT_POLICY, **(policy or {})}
    classes = []
    if policy["uppercase"]:
        classes.append(string.ascii_uppercase)
    if policy["lowercase"]:
        classes.append(string.ascii_lowercase)
    if policy["digits"]:
        classes.append(string.digits)
    if policy["symbols"]:
        classes.append(policy["symbol_set"])
    if not classes:
        raise ValueError("password policy must enable at least one character class")
    if policy["exclude_ambiguous"]:
        classes = ["".join(c for c in cls if c not in _AMBIGUOUS) for cls in classes]

    length = int(policy["length"])
    if length < max(len(classes), 8):
        raise ValueError("password length too short for policy")

    rng = secrets.SystemRandom()
    chars = [rng.choice(cls) for cls in classes]
    alphabet = "".join(classes)
    chars += [rng.choice(alphabet) for _ in range(length - len(chars))]
    rng.shuffle(chars)
    return "".join(chars)


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)
