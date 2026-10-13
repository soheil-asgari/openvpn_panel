"""OpenVPN management interface client and connection accounting.

Talks to the OpenVPN management unix socket. All reads/writes are line based
and strictly parsed; nothing here is executed as a shell command.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field

from app.config import get_settings


class ManagementError(RuntimeError):
    """Raised when the OpenVPN management interface errors out."""


@dataclass(slots=True)
class ClientStatus:
    common_name: str
    address: str
    bytes_received: int
    bytes_sent: int
    connected_since: str


@dataclass(slots=True)
class ServerStatus:
    clients: list[ClientStatus] = field(default_factory=list)

    @property
    def online_count(self) -> int:
        return len(self.clients)


class ManagementInterface:
    """Thin synchronous client for the OpenVPN management socket."""

    def __init__(self, socket_path: str | None = None, timeout: float = 5.0) -> None:
        self.socket_path = socket_path or str(get_settings().ovpn_management_socket)
        self.timeout = timeout

    def _connect(self) -> socket.socket:
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect(self.socket_path)
            return sock
        except OSError as exc:
            raise ManagementError(f"cannot connect to management socket: {exc}") from exc

    def _command(self, sock: socket.socket, command: str) -> list[str]:
        sock.sendall(command.encode("utf-8") + b"\n")
        lines: list[str] = []
        while True:
            try:
                data = sock.recv(65536)
            except TimeoutError:
                break
            if not data:
                break
            for line in data.decode("utf-8", errors="replace").splitlines():
                if line.strip() == "END":
                    return lines
                lines.append(line)
        return lines

    def status(self) -> ServerStatus:
        with self._connect() as sock:
            lines = self._command(sock, "status 3")
        return parse_status(lines)

    def connected_clients(self) -> list[ClientStatus]:
        """Return the live clients with their byte counters."""

        return self.status().clients

    def kill(self, common_name: str) -> None:
        if not common_name:
            raise ManagementError("common name required")
        with self._connect() as sock:
            self._command(sock, f"kill {common_name}")

    def reload(self) -> None:
        """Ask OpenVPN to re-read its configuration (SIGHUP)."""

        self.signal("SIGHUP")

    def signal(self, name: str) -> None:
        if name not in {"SIGHUP", "SIGTERM", "SIGUSR1", "SIGUSR2"}:
            raise ManagementError("unsupported signal")
        with self._connect() as sock:
            self._command(sock, f"signal {name}")


def parse_status(lines: list[str]) -> ServerStatus:
    """Parse ``status 3`` output. Tolerant of the common variants."""

    status = ServerStatus()
    in_clients = False
    for raw in lines:
        line = raw.strip()
        if line.startswith("CLIENT_LIST"):
            in_clients = True
            parts = line.split("\t")
            if len(parts) < 6:
                parts = line.split(",")
            if len(parts) < 6:
                continue
            try:
                status.clients.append(
                    ClientStatus(
                        common_name=parts[1],
                        address=parts[2],
                        bytes_received=int(float(parts[5])),
                        bytes_sent=int(float(parts[6])) if len(parts) > 6 else 0,
                        connected_since=parts[7] if len(parts) > 7 else "",
                    )
                )
            except (ValueError, IndexError):
                continue
        elif in_clients and line.startswith("END"):
            in_clients = False
    return status


def parse_auth_line(line: str) -> tuple[str, str] | None:
    """Parse a line from OpenVPN's auth file hook stdin.

    Expected format: ``username:password`` (one credential per line).
    """

    line = line.strip()
    if not line or ":" not in line:
        return None
    username, password = line.split(":", 1)
    if not username:
        return None
    return username, password
