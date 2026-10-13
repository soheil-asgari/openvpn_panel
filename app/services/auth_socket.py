"""Unix-socket authentication service for OpenVPN's ``auth-user-pass-verify``.

The OpenVPN hook script connects to a local unix socket (default
``/run/ovpn-panel/auth.sock``), sends the username and password as a single
JSON line and receives ``{"ok": true}`` or ``{"ok": false, "reason": ...}``.

Running the check over a unix socket avoids requiring the HTTP port to be
reachable from the OpenVPN process and keeps credentials off the network.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socketserver
import threading
from pathlib import Path

from app.config import get_settings
from app.core.security import verify_password
from app.core.validators import ValidationError, validate_username

logger = logging.getLogger("ovpn-panel.auth")

MAX_REQUEST = 64 * 1024
_SERVER: "AuthSocketServer | None" = None


def _check(username: str, password: str) -> tuple[bool, str | None]:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models.vpn_user import VpnUser
    from app.services import vpn_users as vpn_users_service

    try:
        validate_username(username)
    except ValidationError:
        return False, "invalid username"

    with session_scope() as db:
        vpn_user = db.scalar(select(VpnUser).where(VpnUser.username == username))
        if vpn_user is None:
            return False, "unknown user"
        if not verify_password(password, vpn_user.password_hash):
            return False, "bad password"
        if not vpn_users_service.is_connectable(
            vpn_user, owner_active=vpn_users_service.owner_active(db, vpn_user)
        ):
            return False, "not connectable"
        limit = vpn_users_service.effective_connection_limit(
            db, vpn_user, get_settings().max_concurrent_connections
        )
        if vpn_users_service.exceeds_concurrent_limit(db, vpn_user, limit):
            return False, "concurrent connection limit reached"
        vpn_user.is_online = True
        from app.models.base import utcnow

        vpn_user.last_connected_at = utcnow()
        db.flush()
    return True, None


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:  # noqa: D401
        raw = self.rfile.readline(MAX_REQUEST)
        if not raw:
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
            username = str(payload.get("username", ""))
            password = str(payload.get("password", ""))
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            self._respond(False, "invalid request")
            return
        try:
            ok, reason = _check(username, password)
        except Exception as exc:  # noqa: BLE001 - never leak a stack to the client
            logger.exception("auth check failed: %s", exc)
            self._respond(False, "internal error")
            return
        self._respond(ok, reason)

    def _respond(self, ok: bool, reason: str | None) -> None:
        body = {"ok": ok}
        if reason:
            body["reason"] = reason
        self.wfile.write(json.dumps(body).encode("utf-8") + b"\n")


# ``socketserver.ThreadingUnixStreamServer`` only exists on Python >= 3.12; on
# older interpreters we compose the equivalent from the threading mixin so the
# auth socket works across the supported runtimes.
if hasattr(socketserver, "ThreadingUnixStreamServer"):
    _BaseAuthServer = socketserver.ThreadingUnixStreamServer
elif hasattr(socketserver, "UnixStreamServer"):

    class _BaseAuthServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        pass
else:

    class _BaseAuthServer:  # pragma: no cover - non-POSIX platform
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("unix domain sockets are not supported on this platform")


class AuthSocketServer(_BaseAuthServer):  # type: ignore[misc,valid-type]
    daemon_threads = True
    allow_reuse_address = True


def _prepare(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()


def start_auth_socket(path: Path | None = None) -> threading.Thread:
    """Start the auth socket server on a background thread (idempotent)."""

    global _SERVER
    if _SERVER is not None:
        return threading.current_thread()

    socket_path = path or get_settings().auth_socket
    _prepare(socket_path)
    server = AuthSocketServer(str(socket_path), _Handler)

    # The OpenVPN daemon (user nobody/nogroup) opens this socket, so it must be
    # connectable regardless of the box's user/group layout. The socket only
    # performs credential verification against the panel database and lives under
    # a local-only runtime directory, so 0666 is safe here.
    with contextlib.suppress(PermissionError, OSError):
        os.chmod(socket_path, 0o666)

    _SERVER = server

    def _serve() -> None:
        logger.info("auth socket listening on %s", socket_path)
        server.serve_forever()

    thread = threading.Thread(target=_serve, name="ovpn-auth-socket", daemon=True)
    thread.start()
    return thread


def stop_auth_socket() -> None:
    global _SERVER
    if _SERVER is not None:
        _SERVER.shutdown()
        _SERVER.server_close()
        _SERVER = None


def main() -> None:  # pragma: no cover - exercised as a systemd unit
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    thread = start_auth_socket()
    thread.join()


if __name__ == "__main__":  # pragma: no cover
    main()
