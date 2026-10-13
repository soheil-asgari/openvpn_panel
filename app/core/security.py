"""Password hashing (argon2id) and token generation."""

from __future__ import annotations

import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2id with sane defaults (argon2-cffi defaults to argon2id).
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=2,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password must not be empty")
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    if not password or not hashed:
        return False
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except (VerificationError, InvalidHashError):
        return True


def generate_token(nbytes: int = 32) -> str:
    """Return a URL-safe random token."""

    return secrets.token_urlsafe(nbytes)


def generate_api_key() -> tuple[str, str]:
    """Return ``(prefix, full_key)``.

    ``prefix`` is a short, non-secret identifier stored in the database so that
    keys can be listed and revoked without storing the raw secret.
    """

    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    return prefix, f"ovpn_{prefix}_{secret}"


def constant_time_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def generate_password(length: int = 20) -> str:
    """Generate a random, URL/shell safe password."""

    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#%^*-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))
