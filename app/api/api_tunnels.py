"""JSON API for the Tunnel Manager (mounted at ``/api/v1``).

Everything the UI wizard does is available here so the CLI and external
automation can provision tunnels without SSH-ing anywhere. Endpoints are
admin-only and rate-limited (provisioning), and every mutating call is written
to the audit log.
"""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, HTTPException, Request, UploadFile, status

from app.api.schemas import (
    Message,
    MtuOut,
    TunnelBenchmarkOut,
    TunnelCreate,
    TunnelDetail,
    TunnelFingerprintOut,
    TunnelOut,
    TunnelProvisionResult,
    TunnelUpdate,
)
from app.dependencies import ApiUser, DbSession, require_scope
from app.models.user import Role
from app.services import audit as audit_service
from app.services import tunnel_backend, tunnel_benchmark, tunnel_ssh, tunnel_worker
from app.services import tunnels as tunnels_service

router = APIRouter(prefix="/tunnels", tags=["tunnels"])


def _require_admin(user) -> None:  # noqa: ANN001
    if user.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")


def _out(tunnel) -> TunnelOut:  # noqa: ANN001
    data = TunnelOut.model_validate(tunnel)
    data.public_ports = tunnel.port_list()
    return data


def _job_dict(job) -> dict:  # noqa: ANN001
    if job is None:
        return {}
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status.value,
        "step": job.step,
        "progress": job.progress,
        "log": job.log,
        "error": job.error,
        "rolled_back": job.rolled_back,
    }


def _result(tunnel, job) -> TunnelProvisionResult:  # noqa: ANN001
    return TunnelProvisionResult(
        tunnel_id=tunnel.id,
        job_id=job.id,
        status=job.status.value,
        step=job.step,
        progress=job.progress,
        log=job.log or "",
        error=job.error,
    )


@router.get("", response_model=list[TunnelOut], tags=["tunnels"])
def api_list_tunnels(request: Request, db: DbSession, user: ApiUser) -> list[TunnelOut]:
    _require_admin(user)
    require_scope(request, "nodes:read")
    return [_out(t) for t in tunnels_service.list_tunnels(db)]


@router.get("/backends", tags=["tunnels"])
def api_backends(user: ApiUser) -> list[dict]:
    _require_admin(user)
    return tunnel_backend.backend_metadata()


@router.get("/summary", tags=["tunnels"])
def api_tunnel_summary(db: DbSession, user: ApiUser) -> dict:
    _require_admin(user)
    return tunnels_service.summary(db)


@router.post("/fingerprint", response_model=TunnelFingerprintOut, tags=["tunnels"])
def api_fingerprint(host: str, user: ApiUser, port: int = 22) -> TunnelFingerprintOut:
    """Probe an entry host's SSH key without sending any credential."""

    _require_admin(user)
    try:
        fingerprint = tunnel_ssh.probe_fingerprint(host, port=port)
    except tunnel_ssh.SSHError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TunnelFingerprintOut(host=host, fingerprint=fingerprint)


@router.post("", response_model=TunnelProvisionResult, status_code=201, tags=["tunnels"])
def api_create_tunnel(
    request: Request, payload: TunnelCreate, db: DbSession, user: ApiUser
) -> TunnelProvisionResult:
    """Create a tunnel and (unless ``probe_only``) start provisioning it."""

    _require_admin(user)
    require_scope(request, "nodes:write")

    ip = request.client.host if request.client else None
    key = ip or "unknown"
    if tunnel_worker.provision_limiter().is_locked(key):
        retry = tunnel_worker.provision_limiter().retry_after(key)
        raise HTTPException(
            status_code=429, detail=f"too many provisioning requests; retry in {retry}s"
        )
    tunnel_worker.provision_limiter().register_failure(key)  # count each attempt

    control_port = payload.control_port or tunnels_service.allocate_control_port(db)
    spec = tunnels_service.TunnelSpec(
        name=payload.name,
        entry_host=payload.entry_host,
        control_port=control_port,
        token=tunnels_service.random_token(),
        backend=payload.backend,
        transport=payload.transport,
        entry_ssh_port=payload.entry_ssh_port,
        entry_username=payload.entry_username,
        auth_method=payload.auth_method,
        exit_host=payload.exit_host,
        public_ports=payload.public_ports,
        host_key_fingerprint=payload.host_key_fingerprint,
        node_id=payload.node_id,
        instance_name=payload.instance_name,
        mtu=payload.mtu,
        auto_heal=payload.auto_heal,
        owner_id=None,
    )
    try:
        tunnel = tunnels_service.create_tunnel(db, spec)
        tunnels_service.ensure_panel_key(tunnel)
    except tunnels_service.TunnelError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_service.record(
        db,
        action="tunnel.create",
        actor=user,
        target_type="tunnel",
        target_id=tunnel.id,
        ip_address=ip,
        detail=f"backend={payload.backend} transport={payload.transport}",
    )

    if payload.probe_only:
        db.commit()
        job = tunnels_service.create_job(db, tunnel_id=tunnel.id, kind="probe")
        job.log = "probe only: tunnel created, not provisioned\n"
        db.commit()
        return _result(tunnel, job)

    # Kick off provisioning in the background; secrets stay in memory only.
    db.commit()
    tunnel_worker.submit_provision(
        tunnel.id,
        {
            "password": payload.password,
            "private_key": payload.private_key,
            "disable_password_login": payload.disable_password_login,
        },
    )
    job = tunnels_service.latest_job(db, tunnel.id)
    db.commit()
    return _result(tunnel, job)


@router.get("/{tunnel_id}", response_model=TunnelDetail, tags=["tunnels"])
def api_get_tunnel(tunnel_id: int, db: DbSession, user: ApiUser) -> TunnelDetail:
    _require_admin(user)
    tunnel = tunnels_service.get(db, tunnel_id)
    if tunnel is None:
        raise HTTPException(status_code=404, detail="tunnel not found")
    detail = TunnelDetail.model_validate(tunnel)
    detail.public_ports = tunnel.port_list()
    detail.latest_job = _job_dict(tunnels_service.latest_job(db, tunnel.id))
    return detail


@router.patch("/{tunnel_id}", response_model=TunnelOut, tags=["tunnels"])
def api_update_tunnel(
    request: Request, tunnel_id: int, payload: TunnelUpdate, db: DbSession, user: ApiUser
) -> TunnelOut:
    _require_admin(user)
    require_scope(request, "nodes:write")
    tunnel = tunnels_service.get(db, tunnel_id)
    if tunnel is None:
        raise HTTPException(status_code=404, detail="tunnel not found")
    try:
        tunnels_service.update_tunnel(db, tunnel, **payload.model_dump(exclude_unset=True))
    except tunnels_service.TunnelError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_service.record(
        db,
        action="tunnel.update",
        actor=user,
        target_type="tunnel",
        target_id=tunnel.id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return _out(tunnel)


@router.delete("/{tunnel_id}", response_model=Message, tags=["tunnels"])
def api_delete_tunnel(request: Request, tunnel_id: int, db: DbSession, user: ApiUser) -> Message:
    _require_admin(user)
    require_scope(request, "nodes:write")
    tunnel = tunnels_service.get(db, tunnel_id)
    if tunnel is None:
        raise HTTPException(status_code=404, detail="tunnel not found")
    tunnels_service.delete_tunnel(db, tunnel)
    audit_service.record(
        db,
        action="tunnel.delete",
        actor=user,
        target_type="tunnel",
        target_id=tunnel_id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return Message(detail="deleted")


@router.post("/{tunnel_id}/rotate-token", response_model=TunnelOut, tags=["tunnels"])
def api_rotate_token(request: Request, tunnel_id: int, db: DbSession, user: ApiUser) -> TunnelOut:
    _require_admin(user)
    tunnel = tunnels_service.get(db, tunnel_id)
    if tunnel is None:
        raise HTTPException(status_code=404, detail="tunnel not found")
    tunnels_service.rotate_token(db, tunnel)
    audit_service.record(
        db,
        action="tunnel.rotate_token",
        actor=user,
        target_type="tunnel",
        target_id=tunnel.id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return _out(tunnel)


def _operation(
    request: Request,
    db,
    user,
    tunnel_id: int,
    action: str,
    password=None,
    private_key=None,
    offline_binary=None,
) -> TunnelProvisionResult:  # noqa: ANN001
    tunnel = tunnels_service.get(db, tunnel_id)
    if tunnel is None:
        raise HTTPException(status_code=404, detail="tunnel not found")
    job = tunnels_service.operate(
        db,
        tunnel,
        action,
        password=password,
        private_key=private_key,
        offline_binary=offline_binary,
    )
    audit_service.record(
        db,
        action=f"tunnel.{action}",
        actor=user,
        target_type="tunnel",
        target_id=tunnel.id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return _result(tunnel, job)


@router.post("/{tunnel_id}/start", response_model=TunnelProvisionResult, tags=["tunnels"])
def api_start_tunnel(request: Request, tunnel_id: int, db: DbSession, user: ApiUser):
    _require_admin(user)
    return _operation(request, db, user, tunnel_id, "start")


@router.post("/{tunnel_id}/stop", response_model=TunnelProvisionResult, tags=["tunnels"])
def api_stop_tunnel(request: Request, tunnel_id: int, db: DbSession, user: ApiUser):
    _require_admin(user)
    return _operation(request, db, user, tunnel_id, "stop")


@router.post("/{tunnel_id}/restart", response_model=TunnelProvisionResult, tags=["tunnels"])
def api_restart_tunnel(request: Request, tunnel_id: int, db: DbSession, user: ApiUser):
    _require_admin(user)
    return _operation(request, db, user, tunnel_id, "restart")


@router.post("/{tunnel_id}/update", response_model=TunnelProvisionResult, tags=["tunnels"])
def api_update_backend(request: Request, tunnel_id: int, db: DbSession, user: ApiUser):
    _require_admin(user)
    return _operation(request, db, user, tunnel_id, "update")


@router.post("/{tunnel_id}/retry", response_model=TunnelProvisionResult, tags=["tunnels"])
def api_retry_tunnel(request: Request, tunnel_id: int, db: DbSession, user: ApiUser):
    """Re-run provisioning using the panel's installed key."""

    _require_admin(user)
    tunnel = tunnels_service.get(db, tunnel_id)
    if tunnel is None:
        raise HTTPException(status_code=404, detail="tunnel not found")
    db.commit()
    tunnel_worker.submit_provision(tunnel.id, {})
    return _result(tunnel, tunnels_service.create_job(db, tunnel_id=tunnel.id, kind="retry"))


@router.post("/{tunnel_id}/rollback", response_model=TunnelProvisionResult, tags=["tunnels"])
def api_rollback_tunnel(request: Request, tunnel_id: int, db: DbSession, user: ApiUser):
    _require_admin(user)
    tunnel = tunnels_service.get(db, tunnel_id)
    if tunnel is None:
        raise HTTPException(status_code=404, detail="tunnel not found")
    job = tunnels_service.rollback(db, tunnel)
    audit_service.record(
        db,
        action="tunnel.rollback",
        actor=user,
        target_type="tunnel",
        target_id=tunnel.id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return _result(tunnel, job)


@router.get("/{tunnel_id}/jobs", tags=["tunnels"])
def api_tunnel_jobs(tunnel_id: int, db: DbSession, user: ApiUser) -> list[dict]:
    _require_admin(user)
    from sqlalchemy import select

    from app.models.tunnel import TunnelProvisionJob

    rows = list(
        db.scalars(
            select(TunnelProvisionJob)
            .where(TunnelProvisionJob.tunnel_id == tunnel_id)
            .order_by(TunnelProvisionJob.created_at.desc())
            .limit(50)
        )
    )
    return [_job_dict(j) for j in rows]


@router.get("/{tunnel_id}/logs", tags=["tunnels"])
def api_tunnel_logs(tunnel_id: int, db: DbSession, user: ApiUser, lines: int = 100) -> dict:
    _require_admin(user)
    tunnel = tunnels_service.get(db, tunnel_id)
    if tunnel is None:
        raise HTTPException(status_code=404, detail="tunnel not found")
    try:
        ssh = tunnels_service.connect(tunnel)
        try:
            kind = tunnel.backend.value if tunnel.backend.value != "auto" else "backhaul"
            backend = tunnel_backend.get_backend(kind, ssh)
            log = backend.logs(tunnel.name, lines=min(max(lines, 10), 1000))
        finally:
            ssh.close()
    except tunnel_ssh.SSHError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"tunnel_id": tunnel_id, "log": log}


@router.get("/{tunnel_id}/health", tags=["tunnels"])
def api_tunnel_health(tunnel_id: int, db: DbSession, user: ApiUser, limit: int = 96) -> dict:
    _require_admin(user)
    tunnel = tunnels_service.get(db, tunnel_id)
    if tunnel is None:
        raise HTTPException(status_code=404, detail="tunnel not found")
    samples = tunnels_service.recent_health(db, tunnel_id, limit=min(max(limit, 1), 500))
    return {
        "tunnel_id": tunnel_id,
        "status": tunnel.status.value,
        "consecutive_failures": tunnel.consecutive_failures,
        "samples": [
            {
                "ok": s.ok,
                "latency_ms": s.latency_ms,
                "packet_loss": s.packet_loss,
                "throughput_kbps": s.throughput_kbps,
                "recorded_at": s.recorded_at.isoformat(),
            }
            for s in samples
        ],
    }


@router.post("/{tunnel_id}/mtu", response_model=MtuOut, tags=["tunnels"])
def api_find_mtu(
    request: Request, tunnel_id: int, db: DbSession, user: ApiUser, apply: bool = False
) -> MtuOut:
    _require_admin(user)
    tunnel = tunnels_service.get(db, tunnel_id)
    if tunnel is None:
        raise HTTPException(status_code=404, detail="tunnel not found")
    from app.services import mtu_finder

    try:
        ssh = tunnels_service.connect(tunnel)
        try:
            result = mtu_finder.find_mtu(tunnel.entry_host, ssh=ssh)
        finally:
            ssh.close()
    except tunnel_ssh.SSHError:
        result = mtu_finder.find_mtu(tunnel.entry_host)
    if result.ok and apply and result.mtu:
        tunnel.mtu = result.mtu
        tunnel.mssfix = result.mssfix
        with contextlib.suppress(Exception):
            tunnels_service.apply_mtu_to_instance(tunnel)
    audit_service.record(
        db,
        action="tunnel.mtu",
        actor=user,
        target_type="tunnel",
        target_id=tunnel.id,
        detail=f"mtu={result.mtu}",
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return MtuOut(
        ok=result.ok,
        mtu=result.mtu,
        mssfix=result.mssfix,
        target=result.target,
        detail=result.detail,
    )


@router.post("/benchmark", response_model=TunnelBenchmarkOut, tags=["tunnels"])
def api_benchmark(
    request: Request, db: DbSession, user: ApiUser, host: str, candidates: str = ""
) -> TunnelBenchmarkOut:
    """Benchmark candidate backends/transports for a prospective entry host."""

    _require_admin(user)
    pairs: list[tuple[str, str]] = []
    for chunk in candidates.replace(",", " ").split():
        if ":" in chunk:
            backend, transport = chunk.split(":", 1)
            pairs.append((backend, transport))
    if not pairs:
        pairs = [
            ("backhaul", "tcp"),
            ("backhaul", "tcpmux"),
            ("backhaul", "udp"),
            ("wireguard", "wg"),
        ]
    report = tunnel_benchmark.benchmark_candidates(host, pairs)
    audit_service.record(
        db,
        action="tunnel.benchmark",
        actor=user,
        target_type="host",
        detail=host,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return TunnelBenchmarkOut(**report.as_dict())


@router.post("/{tunnel_id}/offline-install", response_model=TunnelProvisionResult, tags=["tunnels"])
def api_offline_install(
    request: Request,
    tunnel_id: int,
    file: UploadFile,
    db: DbSession,
    user: ApiUser,
) -> TunnelProvisionResult:
    """Install a backend binary uploaded from the panel (air-gapped hosts)."""

    _require_admin(user)
    tunnel = tunnels_service.get(db, tunnel_id)
    if tunnel is None:
        raise HTTPException(status_code=404, detail="tunnel not found")
    data = file.file.read()
    if not data or len(data) > 64 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="invalid or oversized binary")
    job = tunnels_service.operate(db, tunnel, "update", offline_binary=data)
    audit_service.record(
        db,
        action="tunnel.offline_install",
        actor=user,
        target_type="tunnel",
        target_id=tunnel.id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return _result(tunnel, job)
