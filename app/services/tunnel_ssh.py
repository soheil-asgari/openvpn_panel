"""SSH transport used by the Tunnel Manager provisioning wizard.

This is the *only* place in the panel that speaks SSH. It is deliberately thin
so it can be replaced by a fake in unit tests (see ``tests/test_tunnels.py``).

Design notes
------------
* Paramiko is used (optional import) because the wizard must authenticate with a
  **password once**, then install a panel-owned key for subsequent runs.
* The remote host key fingerprint the admin confirmed is pinned: paramiko
  refuses to connect if the presented key does not match.
* Every remote command is produced from an allow-listed template by the backends
  (see :mod:`app.services.tunnel_backend`); nothing here concatenates untrusted
  strings into a shell.
"""

from __future__ import annotations

import io
import logging
import shlex
from dataclasses import dataclass
from typing import Protocol

from app.core.validators import ValidationError, validate_hostname

logger = logging.getLogger(__name__)


class SSHError(RuntimeError):
    """Raised when an SSH connection or command fails."""


@dataclass(slots=True)
class CommandResult:
    ok: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class SSHTransport(Protocol):
    """Minimal SSH surface the backends depend on (easy to fake in tests)."""

    def run(self, command: str, *, timeout: float = 120.0) -> CommandResult: ...

    def upload_bytes(self, data: bytes, remote_path: str, *, mode: int = 0o600) -> None: ...

    def read_text(self, remote_path: str) -> str: ...

    def close(self) -> None: ...


def quote(value: object) -> str:
    """Shell-quote a value for a remote command (never trust input)."""

    return shlex.quote(str(value))


class ParamikoSSH:
    """Production :class:`SSHTransport` backed by paramiko."""

    def __init__(
        self,
        host: str,
        *,
        port: int = 22,
        username: str = "root",
        password: str | None = None,
        private_key: str | None = None,
        host_key_fingerprint: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        validate_hostname(host)
        try:
            import paramiko
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise SSHError("paramiko is required for the Tunnel Manager") from exc

        self.host = host
        self.port = int(port)
        self.username = username
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.RejectPolicy())

        if host_key_fingerprint:
            # Pin the expected key: verify on connect via our own callback.
            expected = host_key_fingerprint.strip()

            class _Pinned(paramiko.MissingHostKeyPolicy):
                def missing_host_key(self, client, hostname, key):  # noqa: ANN001
                    presented = _fingerprint(key)
                    if presented != expected:
                        raise SSHError(f"host key mismatch for {hostname}: got {presented}")
                    client.get_host_keys().add(hostname, key.get_name(), key)

            self._client.set_missing_host_key_policy(_Pinned())

        try:
            self._client.connect(
                hostname=host,
                port=self.port,
                username=username,
                password=password or None,
                key_filename=None if password else private_key,
                pkey=_load_key(paramiko, private_key) if private_key and not password else None,
                timeout=timeout,
                allow_agent=False,
                look_for_keys=False,
            )
        except SSHError:
            raise
        except Exception as exc:  # noqa: BLE001 - paramiko raises many types
            raise SSHError(f"ssh connect failed: {exc}") from exc

    # -- transport API ----------------------------------------------------- #
    def run(self, command: str, *, timeout: float = 120.0) -> CommandResult:
        assert command  # guard against empty templates
        try:
            _stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            code = stdout.channel.recv_exit_status()
        except Exception as exc:  # noqa: BLE001
            raise SSHError(f"remote command failed: {exc}") from exc
        return CommandResult(ok=code == 0, exit_code=code, stdout=out, stderr=err)

    def upload_bytes(self, data: bytes, remote_path: str, *, mode: int = 0o600) -> None:
        directory = remote_path.rsplit("/", 1)[0] or "/"
        try:
            with self._client.open_sftp() as sftp:
                _mkdirs(sftp, directory)
                with sftp.file(remote_path, "wb") as handle:
                    handle.write(data)
                sftp.chmod(remote_path, mode)
        except Exception as exc:  # noqa: BLE001
            raise SSHError(f"sftp upload failed: {exc}") from exc

    def read_text(self, remote_path: str) -> str:
        try:
            with self._client.open_sftp() as sftp, sftp.file(remote_path, "rb") as handle:
                return handle.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            raise SSHError(f"sftp read failed: {exc}") from exc

    def close(self) -> None:
        import contextlib

        with contextlib.suppress(Exception):  # best effort
            self._client.close()


def _load_key(paramiko, private_key: str):  # noqa: ANN001
    """Load a private key from PEM/OpenSSH text or a path."""

    import os

    candidates: list[str] = []
    if private_key and "PRIVATE KEY" in private_key:
        candidates = ["RSAKey", "Ed25519Key", "ECDSAKey", "DSSKey"]
        stream = io.StringIO(private_key)
        last: Exception | None = None
        for name in candidates:
            stream.seek(0)
            cls = getattr(paramiko, name, None)
            if cls is None:
                continue
            try:
                return cls.from_private_key(stream)
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise SSHError(f"unable to parse private key: {last}")
    if private_key and os.path.isfile(private_key):
        for name in ("Ed25519Key", "ECDSAKey", "RSAKey", "DSSKey"):
            cls = getattr(paramiko, name, None)
            if cls is None:
                continue
            try:
                return cls.from_private_key_file(private_key)
            except Exception:  # noqa: BLE001
                continue
        raise SSHError("unable to load private key file")
    raise ValidationError("a private key or password is required")


def _fingerprint(key) -> str:  # noqa: ANN001
    import base64
    import hashlib

    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _mkdirs(sftp, path: str) -> None:  # noqa: ANN001
    parts = [p for p in path.split("/") if p]
    current = ""
    for part in parts:
        current += "/" + part
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def probe_fingerprint(host: str, *, port: int = 22, timeout: float = 10.0) -> str:
    """Return the entry host's SHA256 key fingerprint without authenticating.

    Used by the wizard's "Verify fingerprint" step before any credential is sent.
    """

    validate_hostname(host)
    try:
        import paramiko
    except ImportError as exc:  # pragma: no cover
        raise SSHError("paramiko is required for the Tunnel Manager") from exc

    sock = None
    transport = None
    try:
        sock = __import__("socket").create_connection((host, int(port)), timeout=timeout)
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        return _fingerprint(key)
    except Exception as exc:  # noqa: BLE001
        raise SSHError(f"fingerprint probe failed: {exc}") from exc
    finally:
        if transport is not None:
            transport.close()
        if sock is not None:
            sock.close()
