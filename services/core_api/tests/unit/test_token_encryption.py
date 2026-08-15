"""Unit test: AES-256-GCM token encryption round-trip.

Named test required by Sprint 1's Testing section: test_token_encryption_decryption.
"""

from __future__ import annotations

import base64
import os

import pytest

from app.domain.security import EncryptedPayload, TokenCipher


@pytest.fixture
def cipher() -> TokenCipher:
    key = base64.b64encode(os.urandom(32)).decode()
    return TokenCipher.from_base64_key(key)


def test_token_encryption_decryption(cipher: TokenCipher):
    plaintext_token = "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"

    encrypted = cipher.encrypt(plaintext_token)
    assert encrypted.ciphertext_b64 != plaintext_token

    decrypted = cipher.decrypt(encrypted)
    assert decrypted == plaintext_token


def test_serialize_deserialize_round_trip(cipher: TokenCipher):
    encrypted = cipher.encrypt("shpat_example_token")
    serialized = encrypted.serialize()

    restored = EncryptedPayload.deserialize(serialized)
    assert cipher.decrypt(restored) == "shpat_example_token"


def test_decrypt_fails_with_wrong_key():
    key_a = base64.b64encode(os.urandom(32)).decode()
    key_b = base64.b64encode(os.urandom(32)).decode()
    cipher_a = TokenCipher.from_base64_key(key_a)
    cipher_b = TokenCipher.from_base64_key(key_b)

    encrypted = cipher_a.encrypt("shpat_example_token")

    with pytest.raises(Exception):
        cipher_b.decrypt(encrypted)


def test_rejects_non_32_byte_key():
    with pytest.raises(ValueError):
        TokenCipher(key_material=b"too-short")
