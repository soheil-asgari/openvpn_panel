"""Node agent FastAPI application.

Authenticated with HMAC-SHA256 request signing (preferred) or a bearer token
for hand-provisioned nodes. Exposes health/metrics, client lifecycle, user
sync and config management.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel

from agent.config import get_agent_settings
from app.core import hmac_auth
from app.core.security import constant_time_compare, verify_password
from app.core.validators import validate_common_name, validate_username
from app.services.ovpn import ManagementError, ManagementInterface
from helper.actions import issue_client_certificate, revoke_client_certificate


class ClientInfo(BaseModel):
    common_name: str
    address: str
    bytes_received: int
    bytes_sent: int
    connected_since: str


class StatusResponse(BaseModel):
    node: str
    clients: list[ClientInfo]


class CommonNameRequest(BaseModel):
    common_name: str


class KillRequest(BaseModel):
    common_name: str


class UserSyncRequest(BaseModel):
    username: str
    common_name: str | None = None
    status: str = "active"
    data_limit_bytes: int = 0
    used_bytes: int = 0
    speed_limit_kbps: int = 0
    max_connections: int = 0
    expires_at: str | None = None


class ConfigWriteRequest(BaseModel):
    content: str
    name: str | None = None


class ExecRequest(BaseModel):
    argv: list[str]
    timeout: int = 60


class FileWriteRequest(BaseModel):
    path: str
    content: str | None = None
    content_b64: str | None = None
    mode: int = 0o600


class UnitRequest(BaseModel):
    unit: str
    verb: str


# Roots the agent may write protocol files into (defence against traversal).
ALLOWED_PROTOCOL_DIRS = (
    "/etc/wireguard",
    "/etc/sing-box",
    "/etc/ocserv",
    "/etc/ppp",
    "/etc/xl2tpd",
    "/var/lib/ovpn-panel/protocol-state",
    "/etc/systemd/system",
)
ALLOWED_PROTOCOL_FILES = ("/etc/ipsec.secrets", "/etc/ipsec.conf")
UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]{1,96}$")
UNIT_PREFIXES = ("ovpn-proto-", "wg-quick@", "sing-box", "ocserv", "xl2tpd", "ipsec")
UNIT_VERBS = {"start", "stop", "restart", "status", "enable", "disable", "reload"}


def _resolve_protocol_path(raw: str) -> Path:
    if not raw or not raw.startswith("/") or "\x00" in raw:
        raise HTTPException(status_code=400, detail="invalid path")
    resolved = Path(raw).resolve()
    for root in ALLOWED_PROTOCOL_DIRS:
        root_resolved = Path(root).resolve()
        if resolved == root_resolved or root_resolved in resolved.parents:
            return resolved
    if str(resolved) in {str(Path(p).resolve()) for p in ALLOWED_PROTOCOL_FILES}:
        return resolved
    raise HTTPException(status_code=400, detail="path not allowed")


def _bearer_ok(authorization: str | None) -> bool:
    settings = get_agent_settings()
    if not authorization or not authorization.lower().startswith("bearer "):
        return False
    token = authorization[7:].strip()
    stored = settings.token_hash
    if not stored:
        return False
    if stored.startswith("$argon2"):
        return verify_password(token, stored)
    return constant_time_compare(stored, token)


async def require_token(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> None:
    """Accept a valid HMAC signature or, failing that, a bearer token."""

    settings = get_agent_settings()
    if settings.hmac_secret:
        body = await request.body()
        signature = request.headers.get(hmac_auth.SIGNATURE_HEADER)
        timestamp = request.headers.get(hmac_auth.TIMESTAMP_HEADER)
        if hmac_auth.verify(
            settings.hmac_secret,
            method=request.method,
            path=request.url.path,
            timestamp=timestamp,
            signature=signature,
            body=body,
            window=settings.signature_window,
        ):
            return
    if _bearer_ok(authorization):
        return
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


def _read_meminfo() -> tuple[int, int]:
    total = available = 0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) * 1024
            elif line.startswith("MemAvailable:"):
                available = int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return total, max(0, total - available)


def _read_uptime() -> int:
    try:
        return int(float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


def _read_net() -> tuple[int, int]:
    rx = tx = 0
    try:
        for line in Path("/proc/net/dev").read_text(encoding="utf-8").splitlines()[2:]:
            if ":" not in line:
                continue
            fields = line.split(":", 1)[1].split()
            rx += int(fields[0])
            tx += int(fields[8])
    except (OSError, ValueError, IndexError):
        pass
    return rx, tx


def _client_count() -> int:
    try:
        return ManagementInterface().status().online_count
    except ManagementError:
        return 0


def _state_path(username: str) -> Path:
    settings = get_agent_settings()
    return settings.state_dir / f"{username}.json"


def create_app() -> FastAPI:
    app = FastAPI(title="OpenVPN Node Agent", version="0.2.0")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "node": get_agent_settings().node_name}

    @app.get("/metrics", dependencies=[Depends(require_token)])
    def metrics() -> dict:
        mem_total, mem_used = _read_meminfo()
        rx, tx = _read_net()
        try:
            load1 = os.getloadavg()[0]
        except (OSError, AttributeError):
            load1 = 0.0
        try:
            cpu_percent = float(os.getloadavg()[0]) * 100 / (os.cpu_count() or 1)
        except (OSError, AttributeError):
            cpu_percent = 0.0
        try:
            usage = shutil.disk_usage("/")
            disk_total, disk_used = usage.total, usage.used
        except OSError:
            disk_total = disk_used = 0
        return {
            "node": get_agent_settings().node_name,
            "cpu_percent": round(min(100.0, cpu_percent), 2),
            "load1": round(load1, 2),
            "mem_total": mem_total,
            "mem_used": mem_used,
            "disk_total": disk_total,
            "disk_used": disk_used,
            "net_rx": rx,
            "net_tx": tx,
            "client_count": _client_count(),
            "uptime_seconds": _read_uptime(),
        }

    @app.get("/status", response_model=StatusResponse, dependencies=[Depends(require_token)])
    def get_status() -> StatusResponse:
        try:
            result = ManagementInterface().status()
        except ManagementError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return StatusResponse(
            node=get_agent_settings().node_name,
            clients=[
                ClientInfo(
                    common_name=c.common_name,
                    address=c.address,
                    bytes_received=c.bytes_received,
                    bytes_sent=c.bytes_sent,
                    connected_since=c.connected_since,
                )
                for c in result.clients
            ],
        )

    @app.post("/clients", dependencies=[Depends(require_token)])
    def create_client(payload: CommonNameRequest) -> dict:
        validate_common_name(payload.common_name)
        result = issue_client_certificate(payload.common_name)
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.error or "failed")
        return result.data or {}

    @app.delete("/clients/{common_name}", dependencies=[Depends(require_token)])
    def delete_client(common_name: str) -> dict:
        validate_common_name(common_name)
        result = revoke_client_certificate(common_name)
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.error or "failed")
        return {"detail": "revoked"}

    @app.post("/kill", dependencies=[Depends(require_token)])
    def kill(payload: KillRequest) -> dict:
        validate_common_name(payload.common_name)
        try:
            ManagementInterface().kill(payload.common_name)
        except ManagementError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"detail": "killed"}

    @app.post("/reload", dependencies=[Depends(require_token)])
    def reload_config() -> dict:
        try:
            ManagementInterface().reload()
        except ManagementError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"detail": "reloaded"}

    @app.post("/users", dependencies=[Depends(require_token)])
    def sync_user(payload: UserSyncRequest) -> dict:
        """Materialise a user's limits locally for OpenVPN to enforce."""

        validate_username(payload.username)
        state_dir = get_agent_settings().state_dir
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            _state_path(payload.username).write_text(
                json.dumps(payload.model_dump()), encoding="utf-8"
            )
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"cannot write state: {exc}") from exc
        return {"detail": "synced", "username": payload.username}

    @app.delete("/users/{username}", dependencies=[Depends(require_token)])
    def delete_user(username: str) -> dict:
        validate_username(username)
        try:
            _state_path(username).unlink(missing_ok=True)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"detail": "deleted"}

    @app.get("/config", dependencies=[Depends(require_token)])
    def read_config() -> dict:
        path = get_agent_settings().config_file
        try:
            return {"path": str(path), "content": path.read_text(encoding="utf-8")}
        except OSError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/config", dependencies=[Depends(require_token)])
    def write_config(payload: ConfigWriteRequest) -> dict:
        import re

        if "\x00" in payload.content:
            raise HTTPException(status_code=400, detail="invalid content")
        path = get_agent_settings().config_file
        if payload.name:
            if not re.match(r"^[A-Za-z0-9_.-]{1,32}$", payload.name):
                raise HTTPException(status_code=400, detail="invalid config name")
            path = path.parent / f"{payload.name}.conf"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(payload.content, encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"detail": "written", "path": str(path)}

    @app.post("/protocol/exec", dependencies=[Depends(require_token)])
    def protocol_exec(payload: ExecRequest) -> dict:
        """Run a fixed argv list (allow-listed executable, no shell)."""

        argv = payload.argv
        if not argv or not all(isinstance(a, str) and "\x00" not in a for a in argv):
            raise HTTPException(status_code=400, detail="invalid argv")
        if not argv[0].startswith("/"):
            raise HTTPException(status_code=400, detail="executable must be absolute")
        try:
            proc = subprocess.run(  # noqa: S603 - argv list, shell disabled
                argv,
                capture_output=True,
                text=True,
                timeout=max(1, min(int(payload.timeout or 60), 600)),
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            raise HTTPException(status_code=400, detail=f"command failed: {exc}") from exc
        return {
            "exit_code": proc.returncode,
            "stdout": (proc.stdout or "")[-65536:],
            "stderr": (proc.stderr or "")[-16384:],
        }

    @app.post("/protocol/file", dependencies=[Depends(require_token)])
    def protocol_write_file(payload: FileWriteRequest) -> dict:
        target = _resolve_protocol_path(payload.path)
        if payload.mode not in {0o600, 0o640, 0o644, 0o700, 0o755}:
            raise HTTPException(status_code=400, detail="mode not allowed")
        content = payload.content
        if payload.content_b64:
            import base64

            try:
                content = base64.b64decode(payload.content_b64).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                raise HTTPException(status_code=400, detail="invalid base64 content") from exc
        if content is None:
            raise HTTPException(status_code=400, detail="content required")
        if len(content.encode("utf-8")) > 512 * 1024:
            raise HTTPException(status_code=400, detail="content too large")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.chmod(payload.mode)
            tmp.replace(target)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"detail": "written", "path": str(target)}

    @app.get("/protocol/file", dependencies=[Depends(require_token)])
    def protocol_read_file(path: str) -> dict:
        target = _resolve_protocol_path(path)
        try:
            return {"path": str(target), "content": target.read_text(encoding="utf-8")}
        except OSError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/protocol/unit", dependencies=[Depends(require_token)])
    def protocol_unit(payload: UnitRequest) -> dict:
        if not UNIT_RE.match(payload.unit or "") or not any(
            payload.unit.startswith(prefix) for prefix in UNIT_PREFIXES
        ):
            raise HTTPException(status_code=400, detail="unit not allowed")
        if payload.verb not in UNIT_VERBS:
            raise HTTPException(status_code=400, detail="verb not allowed")
        try:
            proc = subprocess.run(  # noqa: S603
                ["/usr/bin/systemctl", payload.verb, payload.unit],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if proc.returncode != 0:
            raise HTTPException(status_code=400, detail=(proc.stderr or "systemctl failed").strip())
        return {"detail": "ok", "stdout": (proc.stdout or "").strip()}

    return app


app = create_app()
