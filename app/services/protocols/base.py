"""Protocol backend abstraction (Phase 12).

A *protocol backend* knows how to manage end users and server configuration for
one VPN/tunnel protocol. OpenVPN is the reference implementation; WireGuard,
AnyConnect (ocserv), L2TP/IPsec and sing-box are additional backends.

Design rules (mirrors the rest of the panel):

* Every backend receives an :class:`Executor` and never touches the operating
  system directly. The executor is the *only* component that runs commands or
  writes files, and it always receives a structured argv list — never a shell
  string built from untrusted input.
* Nothing here is copied from any commercial panel; the interface and the
  implementations are written from scratch against the public upstream docs.
"""

from __future__ import annotations

import abc
import shlex
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


class ProtocolError(RuntimeError):
    """Raised when a protocol backend operation fails."""


@dataclass(slots=True)
class ExecResult:
    """Outcome of running one command on a host."""

    ok: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


@runtime_checkable
class Executor(Protocol):
    """Minimal execution surface a backend depends on (easy to fake in tests)."""

    def run(self, argv: list[str], *, timeout: float = 60.0) -> ExecResult: ...

    def write_file(self, path: str, content: str, *, mode: int = 0o600) -> None: ...

    def read_file(self, path: str) -> str: ...

    def upload(self, data: bytes, path: str, *, mode: int = 0o600) -> None: ...

    def service(self, unit: str, verb: str, *, timeout: float = 60.0) -> ExecResult: ...


def quote(value: object) -> str:
    """Shell-quote a value that is embedded in a fixed command template."""

    return shlex.quote(str(value))


@dataclass(slots=True)
class UserSpec:
    """Everything a backend needs to materialise one end user."""

    username: str
    password: str | None = None
    data_limit_bytes: int | None = None
    used_bytes: int = 0
    speed_limit_kbps: int | None = None
    max_connections: int | None = None
    expires_at: datetime | None = None
    enabled: bool = True
    #: protocol specific credential material (uuid, private key, psk, ...).
    extra: dict = field(default_factory=dict)


@dataclass(slots=True)
class Usage:
    """Traffic counters for one user as reported by the protocol."""

    username: str
    bytes_in: int = 0
    bytes_out: int = 0

    @property
    def total(self) -> int:
        return self.bytes_in + self.bytes_out


@dataclass(slots=True)
class OnlineSession:
    """A live connection as reported by the protocol."""

    username: str
    address: str = ""
    since: str = ""
    bytes_in: int = 0
    bytes_out: int = 0


@dataclass(slots=True)
class UserConfig:
    """A rendered client configuration / share link for one user."""

    kind: str
    #: full text config (e.g. ``.ovpn`` or ``.conf``).
    text: str = ""
    #: share link (``vless://``, ``vmess://``, ``ss://``, ``wg://`` ...).
    uri: str = ""
    #: payload to encode in the subscription QR code.
    qr_payload: str = ""
    filename: str = ""
    extra: dict = field(default_factory=dict)


@dataclass(slots=True)
class HealthResult:
    ok: bool
    detail: str = ""
    version: str | None = None


@dataclass(slots=True)
class InstallResult:
    ok: bool
    version: str | None = None
    error: str | None = None
    log: list[str] = field(default_factory=list)


class ProtocolBackend(abc.ABC):
    """Abstract per-protocol user/server management contract."""

    #: stable identifier persisted in ``protocol_instances.kind``.
    kind: str = ""
    display_name: str = ""
    upstream: str = ""
    license: str = ""
    #: whether the backend can be installed from the panel UI.
    supports_install: bool = True
    supports_traffic: bool = True
    supports_online: bool = True
    #: whether users are addressed by password (vs. certificate/uuid only).
    supports_password: bool = True
    #: short capability tags shown in the UI.
    capabilities: tuple[str, ...] = ()

    def __init__(self, executor: Executor, instance: object | None = None) -> None:
        self.executor = executor
        self.instance = instance

    # -- lifecycle --------------------------------------------------------- #
    def install(self, *, offline: bytes | None = None) -> InstallResult:
        """Install or upgrade the protocol server on the target host."""

        raise NotImplementedError(f"{self.kind} does not support installation")

    @abc.abstractmethod
    def healthcheck(self) -> HealthResult:
        """Return whether the server for this instance is healthy."""

    # -- user lifecycle ---------------------------------------------------- #
    @abc.abstractmethod
    def create_user(self, spec: UserSpec) -> None:
        """Create or update the user's credential and enforcement state."""

    @abc.abstractmethod
    def delete_user(self, username: str) -> None:
        """Remove the user entirely (credentials + enforcement state)."""

    @abc.abstractmethod
    def set_limits(self, spec: UserSpec) -> None:
        """Apply quota/speed/expiry/concurrency limits to an existing user."""

    @abc.abstractmethod
    def disable(self, username: str) -> None:
        """Disable the user and terminate any live session."""

    @abc.abstractmethod
    def usage(self) -> list[Usage]:
        """Return per-user traffic counters from the protocol."""

    @abc.abstractmethod
    def online(self) -> list[OnlineSession]:
        """Return the currently connected users."""

    @abc.abstractmethod
    def config_for_user(self, spec: UserSpec) -> UserConfig:
        """Render the client configuration and/or share link for a user."""

    # -- helpers ----------------------------------------------------------- #
    @property
    def instance_name(self) -> str:
        if self.instance is not None:
            return str(getattr(self.instance, "name", "") or "")
        return ""

    @property
    def instance_settings(self) -> dict:
        if self.instance is None:
            return {}
        # ProtocolInstance exposes ``settings_dict``; plain objects/tests may
        # provide a ``settings`` mapping directly.
        if hasattr(self.instance, "settings_dict"):
            return dict(self.instance.settings_dict)
        settings = getattr(self.instance, "settings", None)
        return dict(settings) if isinstance(settings, dict) else {}

    def unit(self, name: str | None = None) -> str:
        return f"ovpn-proto-{self.kind}-{name or self.instance_name}"

    def run(self, argv: list[object], *, timeout: float = 60.0) -> ExecResult:
        return self.executor.run([str(a) for a in argv], timeout=timeout)

    def run_ok(self, argv: list[object], *, timeout: float = 60.0) -> ExecResult:
        """Run a command and raise :class:`ProtocolError` on failure."""

        result = self.run(argv, timeout=timeout)
        if not result.ok:
            raise ProtocolError(
                f"{argv[0] if argv else 'command'} failed: "
                f"{result.stderr.strip() or result.exit_code}"
            )
        return result
