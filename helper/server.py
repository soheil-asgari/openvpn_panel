"""Privileged helper server.

Listens on a unix socket, accepts one JSON request per connection, dispatches
to an allow-listed action and returns a JSON response. The socket is created
with mode 660 owned by ``root:ovpn-panel`` so only the panel group can connect.
"""

from __future__ import annotations

import json
import logging
import os
import socketserver
from pathlib import Path

from helper.actions import ActionResult, dispatch

logger = logging.getLogger("ovpn-panel-helper")

SOCKET_PATH = Path(os.environ.get("OVPN_HELPER_SOCKET", "/run/ovpn-panel/helper.sock"))
SOCKET_MODE = 0o660
MAX_REQUEST = 1024 * 1024  # 1 MiB


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:  # noqa: D401
        data = self.rfile.readline(MAX_REQUEST)
        if not data:
            return
        try:
            payload = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._respond(ActionResult(ok=False, error="invalid JSON"))
            return
        if not isinstance(payload, dict):
            self._respond(ActionResult(ok=False, error="payload must be an object"))
            return
        logger.info("action=%s", payload.get("action"))
        result = dispatch(payload)
        self._respond(result)

    def _respond(self, result: ActionResult) -> None:  # noqa: ANN001
        body = json.dumps({"ok": result.ok, "data": result.data, "error": result.error})
        self.wfile.write(body.encode("utf-8") + b"\n")


class ThreadedUnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def _prepare_socket(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()


def run(socket_path: Path | None = None) -> None:
    path = socket_path or SOCKET_PATH
    _prepare_socket(path)
    with ThreadedUnixServer(str(path), Handler) as server:
        os.chmod(path, SOCKET_MODE)
        logger.info("helper listening on %s", path)
        server.serve_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()


if __name__ == "__main__":
    main()
