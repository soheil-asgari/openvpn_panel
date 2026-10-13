"""Server-side session store with a signed cookie handle.

Sessions live in the database so they survive restarts. The browser only holds
a signed session identifier; the ``SECRET_KEY`` is used to sign it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings


@dataclass(slots=True)
class SessionData:
    session_id: str
    user_id: int
    role: str
    csrf_token: str
    created_at: float
    last_seen: float


class SessionSigner:
    """Signs and verifies opaque session identifiers."""

    def __init__(self, secret_key: str, max_age: int) -> None:
        self._serializer = URLSafeTimedSerializer(secret_key, salt="ovpn-session")
        self.max_age = max_age

    def sign(self, session_id: str) -> str:
        return self._serializer.dumps(session_id)

    def verify(self, token: str) -> str | None:
        try:
            return self._serializer.loads(token, max_age=self.max_age)
        except (BadSignature, SignatureExpired):
            return None


def build_signer(settings: Settings) -> SessionSigner:
    return SessionSigner(settings.secret_key, settings.session_max_age)


def is_expired(session: SessionData, max_age: int) -> bool:
    return (time.time() - session.last_seen) > max_age
