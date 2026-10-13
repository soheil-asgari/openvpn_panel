"""HMAC request signing between the panel and node agents.

Every panel→agent call is signed over ``method\\npath\\ntimestamp\\nbody`` with a
per-node shared secret. The agent recomputes the signature and rejects requests
whose timestamp is outside a small window, which makes captured requests
non-replayable. Bearer-token auth remains supported for hand-provisioned nodes.
"""

from __future__ import annotations

import hashlib
import hmac
import time

SIGNATURE_HEADER = "X-OVPN-Signature"
TIMESTAMP_HEADER = "X-OVPN-Timestamp"
DEFAULT_WINDOW = 300  # seconds


def sign(secret: str, *, method: str, path: str, timestamp: str, body: bytes = b"") -> str:
    message = b"\n".join(
        [
            method.upper().encode("utf-8"),
            path.encode("utf-8"),
            timestamp.encode("utf-8"),
            body,
        ]
    )
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def build_headers(secret: str, *, method: str, path: str, body: bytes = b"") -> dict[str, str]:
    """Return the signature headers for a request."""

    timestamp = str(int(time.time()))
    signature = sign(secret, method=method, path=path, timestamp=timestamp, body=body)
    return {SIGNATURE_HEADER: signature, TIMESTAMP_HEADER: timestamp}


def verify(
    secret: str,
    *,
    method: str,
    path: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes = b"",
    window: int = DEFAULT_WINDOW,
    now: float | None = None,
) -> bool:
    """Constant-time verification of a signed request."""

    if not secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    current = int(now if now is not None else time.time())
    if abs(current - ts) > window:
        return False
    expected = sign(secret, method=method, path=path, timestamp=timestamp, body=body)
    return hmac.compare_digest(expected, signature)


def generate_secret(nbytes: int = 32) -> str:
    import secrets

    return secrets.token_urlsafe(nbytes)
