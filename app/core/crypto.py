"""Password-based authenticated encryption for backups (stdlib only).

Uses PBKDF2-HMAC-SHA256 for key derivation, an HMAC-SHA256 based keystream
(CTR-style) for confidentiality and encrypt-then-MAC for integrity. This avoids
adding a runtime crypto dependency while still being sound for archiving.

The on-disk container is::

    b"OVPNBK1\\x00" | salt(16) | nonce(16) | tag(32) | ciphertext(...)

Never reuse a nonce with the same key; :func:`encrypt` generates a fresh salt
and nonce per call.
"""

from __future__ import annotations

import hashlib
import hmac
import os

MAGIC = b"OVPNBK1\x00"
SALT_LEN = 16
NONCE_LEN = 16
TAG_LEN = 32
KEY_LEN = 32
PBKDF2_ROUNDS = 200_000
BLOCK = 32


class CryptoError(ValueError):
    """Raised when a container is malformed or authentication fails."""


def _derive(password: str, salt: bytes, rounds: int = PBKDF2_ROUNDS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds, dklen=KEY_LEN)


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _xor(data: bytes, key: bytes, nonce: bytes) -> bytes:
    stream = _keystream(key, nonce, len(data))
    return bytes(a ^ b for a, b in zip(data, stream, strict=False))


def encrypt(data: bytes, password: str, *, rounds: int = PBKDF2_ROUNDS) -> bytes:
    if not password:
        raise CryptoError("password is required")
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = _derive(password, salt, rounds)
    ciphertext = _xor(data, key, nonce)
    header = MAGIC + salt + nonce
    tag = hmac.new(key, header + ciphertext, hashlib.sha256).digest()
    return header + tag + ciphertext


def decrypt(container: bytes, password: str) -> bytes:
    min_len = len(MAGIC) + SALT_LEN + NONCE_LEN + TAG_LEN
    if len(container) < min_len or not container.startswith(MAGIC):
        raise CryptoError("not a panel backup container")
    offset = len(MAGIC)
    salt = container[offset : offset + SALT_LEN]
    nonce = container[offset + SALT_LEN : offset + SALT_LEN + NONCE_LEN]
    tag = container[offset + SALT_LEN + NONCE_LEN : min_len]
    ciphertext = container[min_len:]
    key = _derive(password, salt)
    expected = hmac.new(key, MAGIC + salt + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise CryptoError("invalid password or corrupted archive")
    return _xor(ciphertext, key, nonce)


def is_container(data: bytes) -> bool:
    return data.startswith(MAGIC)
