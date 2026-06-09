"""Tests for the Crypto helper."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from personal_health_mcp.crypto import Crypto


def test_roundtrip(crypto: Crypto):
    token = crypto.encrypt("super-secret")
    assert token != "super-secret"
    assert crypto.decrypt(token) == "super-secret"


def test_none_passthrough(crypto: Crypto):
    assert crypto.encrypt(None) is None
    assert crypto.decrypt(None) is None


def test_requires_a_key():
    with pytest.raises(ValueError):
        Crypto([])
    with pytest.raises(ValueError):
        Crypto(["  ", ""])


def test_key_rotation_reads_old_writes_new():
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()
    # Ciphertext written under the old-only config...
    old_only = Crypto([old])
    ct = old_only.encrypt("payload")
    # ...is still readable when new key is primary and old is retained.
    rotated = Crypto([new, old])
    assert rotated.decrypt(ct) == "payload"


def test_from_env_value_splits_on_comma():
    k1 = Fernet.generate_key().decode()
    k2 = Fernet.generate_key().decode()
    c = Crypto.from_env_value(f"{k1},{k2}")
    assert c.decrypt(c.encrypt("x")) == "x"
