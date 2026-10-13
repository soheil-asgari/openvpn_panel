"""Executors: the only component that touches a host for protocol management.

Three implementations are provided:

* :class:`LocalExecutor` — runs allow-listed argv on the panel host. In
  production the panel is unprivileged and this code path executes inside the
  privileged helper process; unit tests use it with ``dry_run`` or a fake.
* :class:`HelperExecutor` — talks to the privileged helper over its unix socket
  (used by the panel process for local-node protocol operations).
* :class:`AgentExecutor` — calls the node agent HTTP API for remote nodes.

All three accept a structured ``argv`` list; none ever builds a shell string.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.services.protocols.base import ExecResult, Executor, ProtocolError


class LocalExecutor:
    """Run commands and write files on the local host."""

    def __init__(self, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    def run(self, argv: list[str], *, timeout: float = 60.0) -> ExecResult:
        if self.dry_run:
            return ExecResult(ok=True, stdout="")
        try:
            proc = subprocess.run(  # noqa: S603 - argv list, shell disabled
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            return ExecResult(ok=False, exit_code=127, stderr=str(exc))
        return ExecResult(
            ok=proc.returncode == 0,
            exit_code=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )

    def write_file(self, path: str, content: str, *, mode: int = 0o600) -> None:
        if self.dry_run:
            return
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.chmod(mode)
            tmp.replace(target)
        except OSError as exc:
            raise ProtocolError(f"write failed: {path}: {exc}") from exc

    def read_file(self, path: str) -> str:
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise ProtocolError(f"read failed: {path}: {exc}") from exc

    def upload(self, data: bytes, path: str, *, mode: int = 0o600) -> None:
        self.write_file(path, data.decode("utf-8", errors="replace"), mode=mode)

    def service(self, unit: str, verb: str, *, timeout: float = 60.0) -> ExecResult:
        return self.run(["/usr/bin/systemctl", verb, unit], timeout=timeout)


class HelperExecutor:
    """Run commands through the privileged helper unix socket."""

    def __init__(self, timeout: float = 60.0) -> None:
        self.timeout = timeout

    def run(self, argv: list[str], *, timeout: float = 60.0) -> ExecResult:
        from app.services import helper_client

        try:
            result = helper_client.run_command(argv, timeout=timeout or self.timeout)
        except helper_client.HelperError as exc:
            return ExecResult(ok=False, exit_code=1, stderr=str(exc))
        data = result.data or {}
        return ExecResult(
            ok=bool(result.ok),
            exit_code=int(data.get("exit_code", 0 if result.ok else 1)),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "") or result.error or ""),
        )

    def write_file(self, path: str, content: str, *, mode: int = 0o600) -> None:
        from app.services import helper_client

        try:
            result = helper_client.write_protocol_file(path, content, mode=mode)
        except helper_client.HelperError as exc:
            raise ProtocolError(f"write failed: {path}: {exc}") from exc
        if not result.ok:
            raise ProtocolError(f"write failed: {path}: {result.error}")

    def read_file(self, path: str) -> str:
        from app.services import helper_client

        try:
            result = helper_client.read_config_file(path)
        except helper_client.HelperError as exc:
            raise ProtocolError(f"read failed: {path}: {exc}") from exc
        if not result.ok or result.data is None:
            raise ProtocolError(f"read failed: {path}: {result.error}")
        return str(result.data.get("content", ""))

    def upload(self, data: bytes, path: str, *, mode: int = 0o600) -> None:
        self.write_file(path, data.decode("utf-8", errors="replace"), mode=mode)

    def service(self, unit: str, verb: str, *, timeout: float = 60.0) -> ExecResult:
        from app.services import helper_client

        try:
            result = helper_client.unit_action(unit, verb)
        except helper_client.HelperError as exc:
            return ExecResult(ok=False, exit_code=1, stderr=str(exc))
        return ExecResult(
            ok=bool(result.ok),
            exit_code=0 if result.ok else 1,
            stderr="" if result.ok else str(result.error or ""),
        )


class AgentExecutor:
    """Run commands on a remote node through its agent HTTP API."""

    def __init__(self, node: object, timeout: float = 60.0) -> None:
        self.node = node
        self.timeout = timeout

    def _client(self):  # noqa: ANN202
        from app.services import node_client

        return node_client.NodeAgentClient(self.node, timeout=self.timeout)

    def run(self, argv: list[str], *, timeout: float = 60.0) -> ExecResult:
        from app.services import node_client

        try:
            result = self._client().exec_command(argv, timeout=timeout or self.timeout)
        except node_client.NodeClientError as exc:
            return ExecResult(ok=False, exit_code=1, stderr=str(exc))
        data = result.data or {}
        return ExecResult(
            ok=bool(result.ok),
            exit_code=int(data.get("exit_code", 0 if result.ok else 1)),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "") or result.error or ""),
        )

    def write_file(self, path: str, content: str, *, mode: int = 0o600) -> None:
        from app.services import node_client

        try:
            result = self._client().write_file(path, content, mode=mode)
        except node_client.NodeClientError as exc:
            raise ProtocolError(f"write failed: {path}: {exc}") from exc
        if not result.ok:
            raise ProtocolError(f"write failed: {path}: {result.error}")

    def read_file(self, path: str) -> str:
        from app.services import node_client

        try:
            result = self._client().read_file(path)
        except node_client.NodeClientError as exc:
            raise ProtocolError(f"read failed: {path}: {exc}") from exc
        if not result.ok:
            raise ProtocolError(f"read failed: {path}: {result.error}")
        return str((result.data or {}).get("content", ""))

    def upload(self, data: bytes, path: str, *, mode: int = 0o600) -> None:
        from app.services import node_client

        try:
            result = self._client().upload_file(path, data, mode=mode)
        except node_client.NodeClientError as exc:
            raise ProtocolError(f"upload failed: {path}: {exc}") from exc
        if not result.ok:
            raise ProtocolError(f"upload failed: {path}: {result.error}")

    def service(self, unit: str, verb: str, *, timeout: float = 60.0) -> ExecResult:
        from app.services import node_client

        try:
            result = self._client().unit_action(unit, verb)
        except node_client.NodeClientError as exc:
            return ExecResult(ok=False, exit_code=1, stderr=str(exc))
        return ExecResult(
            ok=bool(result.ok),
            exit_code=0 if result.ok else 1,
            stderr="" if result.ok else str(result.error or ""),
        )


def get_executor(node: object | None = None, *, local: bool = False) -> Executor:
    """Return the executor for a node (``None`` = panel/local node)."""

    if local or node is None:
        return HelperExecutor()
    return AgentExecutor(node)
