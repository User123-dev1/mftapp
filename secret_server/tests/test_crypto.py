import string

import pytest

from secret_server.crypto import (CryptoError, KeyRing, decrypt_field, encrypt_field,
                                  generate_password, hash_password, new_dek, verify_password)


def _keyring(*ids, current=None):
    import os
    return KeyRing({i: os.urandom(32) for i in ids}, current or ids[-1])


def test_envelope_roundtrip_and_aad_binding():
    ring = _keyring("k1")
    dek = new_dek()
    wrapped = ring.wrap_dek(dek, "secret:1:dek")
    assert ring.unwrap_dek(wrapped, "secret:1:dek") == dek
    with pytest.raises(CryptoError):
        ring.unwrap_dek(wrapped, "secret:2:dek")

    blob = encrypt_field(dek, "hunter2", "secret:1:field:password")
    assert decrypt_field(dek, blob, "secret:1:field:password") == "hunter2"
    with pytest.raises(CryptoError):
        decrypt_field(dek, blob, "secret:1:field:username")


def test_old_keys_still_unwrap_after_rotation():
    import os
    k1 = os.urandom(32)
    old = KeyRing({"k1": k1}, "k1")
    wrapped = old.wrap_dek(b"x" * 32, "aad")
    new = KeyRing({"k1": k1, "k2": os.urandom(32)}, "k2")
    assert new.needs_rewrap(wrapped)
    assert new.unwrap_dek(wrapped, "aad") == b"x" * 32
    assert not new.needs_rewrap(new.wrap_dek(b"x" * 32, "aad"))


def test_password_hashing():
    encoded = hash_password("correct horse")
    assert verify_password("correct horse", encoded)
    assert not verify_password("wrong", encoded)
    assert not verify_password("anything", None)


def test_password_policy():
    pw = generate_password({"length": 32, "symbols": False})
    assert len(pw) == 32
    assert any(c in string.ascii_uppercase for c in pw)
    assert any(c in string.digits for c in pw)
    assert not any(c in "!@#$%^&*" for c in pw)
    assert not set(pw) & set("Il1O0o")
    with pytest.raises(ValueError):
        generate_password({"length": 4})
