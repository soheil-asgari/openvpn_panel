"""Tunnel Manager service: CRUD, provisioning runs, health and self-healing.

This module is the single orchestration point for tunnels. It:

* validates inputs and persists :class:`~app.models.tunnel.Tunnel` rows;
* builds the SSH transport (with the confirmed host-key fingerprint pinned);
* drives the provisioning steps, streaming a log into a
  :class:`~app.models.tunnel.TunnelProvisionJob` so the UI can follow along;
* records health samples and restarts failed tunnels (self-healing);
* exposes helpers the config/subscription layer uses to advertise
  ``entry_host:public_port`` instead of the exit host.

The public HTTP surface lives in :mod:`app.api.web` (HTML/HTMX) and
:mod:`app.api.api_v1` (JSON). Keeping the logic here means the CLI and the API
behave identically.
"""

from __future__ import annotations

import contextlib
import json
import logging
import secrets
import stat
import threading
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.security import generate_token
from app.core.validators import (
    ValidationError,
    validate_instance_name,
    validate_ip_or_host,
    validate_port,
    validate_username,
)
from app.models.base import utcnow
from app.models.node import Node
from app.models.tunnel import (
    Tunnel,
    TunnelAuthMethod,
    TunnelBackendKind,
    TunnelHealthSample,
    TunnelProvisionJob,
    TunnelProvisionJobStatus,
    TunnelStatus,
    TunnelTransport,
)
from app.services import tunnel_backend as backend_mod
from app.services import tunnel_benchmark, tunnel_ssh
from app.services.tunnel_ssh import SSHError, SSHTransport

logger = logging.getLogger(__name__)

#: How many consecutive failed probes trigger an automatic restart.
HEAL_THRESHOLD = 3
#: Default control port range the wizard allocates from.
CONTROL_PORT_BASE = 25000


class TunnelError(ValueError):
    """Domain error for tunnel operations."""


# --------------------------------------------------------------------------- #
# Plain-data payloads (decouple the service from pydantic for reuse in the CLI)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class TunnelSpec:
    name: str
    entry_host: str
    control_port: int
    token: str
    backend: str = "auto"
    transport: str = "auto"
    entry_ssh_port: int = 22
    entry_username: str = "root"
    auth_method: str = "password"
    exit_host: str | None = None
    public_ports: list[int] = field(default_factory=list)
    host_key_fingerprint: str | None = None
    node_id: int | None = None
    instance_name: str | None = None
    mtu: int | None = None
    auto_heal: bool = True
    owner_id: int | None = None


@dataclass(slots=True)
class ProvisionContext:
    """Everything a provisioning run needs, including the one-time password."""

    spec: TunnelSpec
    password: str | None = None
    private_key: str | None = None
    offline_binary: bytes | None = None
    disable_password_login: bool = False


#: Serialises provisioning runs across the process (SSH + systemd changes are
#: not safely concurrent against the same host).
_provision_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
def get(db: Session, tunnel_id: int) -> Tunnel | None:
    return db.get(Tunnel, tunnel_id)


def get_by_name(db: Session, name: str) -> Tunnel | None:
    return db.scalar(select(Tunnel).where(Tunnel.name == name))


def list_tunnels(
    db: Session, *, owner_id: int | None = None, active_only: bool = False
) -> list[Tunnel]:
    stmt = select(Tunnel).order_by(Tunnel.name)
    if owner_id is not None:
        stmt = stmt.where(Tunnel.owner_id == owner_id)
    if active_only:
        stmt = stmt.where(Tunnel.is_active.is_(True))
    return list(db.scalars(stmt))


def _coerce_backend(value: str) -> TunnelBackendKind:
    try:
        return TunnelBackendKind(value)
    except ValueError as exc:
        raise TunnelError(f"unknown backend: {value}") from exc


def _coerce_transport(value: str) -> TunnelTransport:
    try:
        return TunnelTransport(value)
    except ValueError as exc:
        raise TunnelError(f"unknown transport: {value}") from exc


def allocate_control_port(db: Session) -> int:
    """Return the next free control port (skips ports already assigned)."""

    used = {int(p) for p in db.scalars(select(Tunnel.control_port)) if p is not None}
    port = CONTROL_PORT_BASE
    while port in used and port < 65000:
        port += 1
    return port


def create_tunnel(db: Session, spec: TunnelSpec) -> Tunnel:
    """Validate and persist a new tunnel (status ``pending``)."""

    try:
        from app.core.validators import validate_node_name

        validate_node_name(spec.name)
        validate_ip_or_host(spec.entry_host)
        validate_port(spec.control_port)
        validate_username(spec.entry_username)
        if spec.exit_host:
            validate_ip_or_host(spec.exit_host)
        if spec.instance_name:
            validate_instance_name(spec.instance_name)
        for port in spec.public_ports:
            validate_port(port)
    except ValidationError as exc:
        raise TunnelError(str(exc)) from exc

    if get_by_name(db, spec.name) is not None:
        raise TunnelError("tunnel name already exists")

    tunnel = Tunnel(
        name=spec.name,
        backend=_coerce_backend(spec.backend),
        transport=_coerce_transport(spec.transport),
        entry_host=spec.entry_host,
        entry_ssh_port=spec.entry_ssh_port,
        entry_username=spec.entry_username,
        entry_auth_method=TunnelAuthMethod(spec.auth_method),
        host_key_fingerprint=spec.host_key_fingerprint,
        exit_host=spec.exit_host,
        token=spec.token or generate_token(24),
        control_port=spec.control_port,
        public_ports=",".join(str(p) for p in spec.public_ports),
        mtu=spec.mtu,
        mssfix=max(0, spec.mtu - 40) if spec.mtu else None,
        node_id=spec.node_id,
        instance_name=spec.instance_name,
        auto_heal=spec.auto_heal,
        owner_id=spec.owner_id,
        status=TunnelStatus.PENDING,
    )
    db.add(tunnel)
    db.flush()
    return tunnel


def update_tunnel(db: Session, tunnel: Tunnel, **fields: object) -> Tunnel:
    if "transport" in fields and fields["transport"]:
        tunnel.transport = _coerce_transport(str(fields["transport"]))
    if "backend" in fields and fields["backend"]:
        tunnel.backend = _coerce_backend(str(fields["backend"]))
    if "control_port" in fields and fields["control_port"] is not None:
        tunnel.control_port = validate_port(fields["control_port"])  # type: ignore[arg-type]
    if "public_ports" in fields and fields["public_ports"] is not None:
        raw = fields["public_ports"]
        ports = raw if isinstance(raw, list) else str(raw).replace(",", " ").split()
        tunnel.public_ports = ",".join(str(validate_port(p)) for p in ports)
    if "token" in fields and fields["token"]:
        tunnel.token = str(fields["token"])
    if "mtu" in fields and fields["mtu"] is not None:
        tunnel.mtu = int(fields["mtu"])  # type: ignore[arg-type]
        tunnel.mssfix = max(0, tunnel.mtu - 40)
    if "instance_name" in fields and fields["instance_name"]:
        tunnel.instance_name = validate_instance_name(str(fields["instance_name"]))
    if "node_id" in fields:
        tunnel.node_id = fields["node_id"]  # type: ignore[assignment]
    if "auto_heal" in fields and fields["auto_heal"] is not None:
        tunnel.auto_heal = bool(fields["auto_heal"])
    if "is_active" in fields and fields["is_active"] is not None:
        tunnel.is_active = bool(fields["is_active"])
    db.flush()
    return tunnel


def rotate_token(db: Session, tunnel: Tunnel) -> str:
    tunnel.token = generate_token(24)
    db.flush()
    return tunnel.token


def delete_tunnel(db: Session, tunnel: Tunnel) -> None:
    db.delete(tunnel)
    db.flush()


# --------------------------------------------------------------------------- #
# Panel-owned SSH key
# --------------------------------------------------------------------------- #
def panel_key_path(tunnel_name: str) -> Path:
    return get_settings().tunnels_dir / f"{tunnel_name}.ed25519"


def ensure_panel_key(tunnel: Tunnel, key_text: str | None = None) -> str:
    """Return the panel's private key path, generating one if needed (mode 600).

    The generated key lets the panel reconnect passwordlessly after the one-time
    password has been used and discarded.
    """

    path = panel_key_path(tunnel.name)
    if key_text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key_text, encoding="utf-8")
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        tunnel.panel_key_path = str(path)
        return str(path)
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError:  # pragma: no cover - optional dependency
        raise TunnelError("cryptography is required to generate a panel key") from None

    if path.exists():
        key = serialization.load_ssh_private_key(path.read_bytes(), password=None)
    else:
        key = ed25519.Ed25519PrivateKey.generate()
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pem, encoding="utf-8")
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    public = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        )
        .decode("utf-8")
    )
    tunnel.panel_key_path = str(path)
    # Preserve any other config keys while recording the public key.
    config: dict = {}
    if tunnel.config:
        with contextlib.suppress(ValueError):
            config = json.loads(tunnel.config)
    config["panel_public_key"] = public
    tunnel.config = json.dumps(config)
    return str(path)


def connect(
    tunnel: Tunnel,
    *,
    password: str | None = None,
    private_key: str | None = None,
    transport_factory=None,  # noqa: ANN001 - injectable for tests
) -> SSHTransport:
    """Open an SSH transport to the tunnel's entry host.

    When ``password`` is supplied it is used **once** for this connection only;
    it is never stored. Otherwise the panel's own key is used.
    """

    key_material = private_key
    if password is None and key_material is None and tunnel.panel_key_path:
        with contextlib.suppress(OSError):
            key_material = Path(tunnel.panel_key_path).read_text(encoding="utf-8")
    factory = transport_factory or tunnel_ssh.ParamikoSSH
    return factory(
        tunnel.entry_host,
        port=tunnel.entry_ssh_port,
        username=tunnel.entry_username,
        password=password,
        private_key=key_material,
        host_key_fingerprint=tunnel.host_key_fingerprint,
    )


# --------------------------------------------------------------------------- #
# Provisioning jobs
# --------------------------------------------------------------------------- #
def create_job(
    db: Session, *, tunnel_id: int | None, kind: str = "provision"
) -> TunnelProvisionJob:
    job = TunnelProvisionJob(
        tunnel_id=tunnel_id,
        kind=kind,
        status=TunnelProvisionJobStatus.PENDING,
        log="",
    )
    db.add(job)
    db.flush()
    return job


def latest_job(db: Session, tunnel_id: int) -> TunnelProvisionJob | None:
    return db.scalar(
        select(TunnelProvisionJob)
        .where(TunnelProvisionJob.tunnel_id == tunnel_id)
        .order_by(TunnelProvisionJob.created_at.desc(), TunnelProvisionJob.id.desc())
    )


def append_log(
    job: TunnelProvisionJob,
    message: str,
    *,
    step: str | None = None,
    progress: int | None = None,
) -> None:
    """Append a timestamped line to the job log (bounded to ~200 KB)."""

    stamp = utcnow().strftime("%H:%M:%S")
    job.log = (job.log or "") + f"[{stamp}] {message}\n"
    if len(job.log) > 200_000:
        job.log = job.log[-200_000:]
    if step is not None:
        job.step = step
    if progress is not None:
        job.progress = max(0, min(100, progress))
    logger.info("tunnel job %s: %s", job.id, message)


# --------------------------------------------------------------------------- #
# Provisioning steps
# --------------------------------------------------------------------------- #
#: Ordered steps shown in the wizard's progress panel.
PROVISION_STEPS = (
    ("verify_fingerprint", "Verify host key fingerprint"),
    ("connect", "Connect over SSH"),
    ("install_key", "Install panel key, disable password login"),
    ("install_backend", "Install tunnel backend"),
    ("configure", "Write configuration"),
    ("mtu", "Discover path MTU"),
    ("start", "Start the tunnel"),
    ("healthcheck", "End-to-end health check"),
)


def _resolve_backend_kind(tunnel: Tunnel) -> str:
    if tunnel.backend != TunnelBackendKind.AUTO:
        return tunnel.backend.value
    # Auto: prefer Backhaul (multiplexing, UDP), fall back to WireGuard.
    preferred = "backhaul"
    if tunnel.transport == TunnelTransport.WG:
        preferred = "wireguard"
    return preferred


def _resolve_transport(tunnel: Tunnel, backend_kind: str) -> str:
    if tunnel.transport not in (TunnelTransport.AUTO,):
        return tunnel.transport.value
    if backend_kind == "wireguard":
        return "wg"
    return "tcpmux"


def provision(
    db: Session,
    tunnel: Tunnel,
    ctx: ProvisionContext,
    *,
    transport_factory=None,  # noqa: ANN001
) -> TunnelProvisionJob:
    """Run the full provisioning sequence, streaming progress into a job.

    On failure the tunnel is left stopped and the job is marked failed; the
    caller may re-run (retry) or trigger :func:`rollback`.
    """

    job = create_job(db, tunnel_id=tunnel.id, kind="provision")
    job.status = TunnelProvisionJobStatus.RUNNING
    db.flush()
    tunnel.status = TunnelStatus.PROVISIONING
    db.flush()

    try:
        with _provision_lock:
            _run_provision_steps(db, tunnel, ctx, job, transport_factory=transport_factory)
    except (TunnelError, SSHError) as exc:
        append_log(job, f"FAILED: {exc}", step="failed", progress=100)
        job.status = TunnelProvisionJobStatus.FAILED
        job.error = str(exc)
        job.completed_at = utcnow()
        tunnel.status = TunnelStatus.ERROR
        tunnel.last_error = str(exc)
        db.flush()
        return job
    except Exception as exc:  # noqa: BLE001 - never leak a raw traceback to the UI
        logger.exception("unexpected provisioning error")
        append_log(job, f"FAILED (unexpected): {exc}", step="failed", progress=100)
        job.status = TunnelProvisionJobStatus.FAILED
        job.error = str(exc)
        job.completed_at = utcnow()
        tunnel.status = TunnelStatus.ERROR
        tunnel.last_error = str(exc)
        db.flush()
        return job

    job.status = TunnelProvisionJobStatus.DONE
    job.progress = 100
    job.completed_at = utcnow()
    tunnel.status = TunnelStatus.ACTIVE
    tunnel.last_error = None
    tunnel.last_seen_at = utcnow()
    db.flush()
    return job


def _run_provision_steps(
    db: Session,
    tunnel: Tunnel,
    ctx: ProvisionContext,
    job: TunnelProvisionJob,
    *,
    transport_factory=None,  # noqa: ANN001
) -> None:
    ssh: SSHTransport | None = None
    try:
        # 1. Fingerprint ---------------------------------------------------- #
        append_log(
            job,
            "Verifying entry host key fingerprint",
            step="verify_fingerprint",
            progress=5,
        )
        if not tunnel.host_key_fingerprint:
            # First run: discover and pin it (the UI requires the admin to
            # confirm a previously probed value; we re-verify here).
            try:
                found = tunnel_ssh.probe_fingerprint(tunnel.entry_host, port=tunnel.entry_ssh_port)
            except SSHError as exc:
                raise TunnelError(f"could not read host key: {exc}") from exc
            tunnel.host_key_fingerprint = found
            append_log(job, f"Pinned host key {found}")
        else:
            append_log(job, f"Using confirmed fingerprint {tunnel.host_key_fingerprint}")

        # 2. Connect -------------------------------------------------------- #
        append_log(job, "Connecting over SSH", step="connect", progress=15)
        ssh = connect(
            tunnel,
            password=ctx.password,
            private_key=ctx.private_key,
            transport_factory=transport_factory,
        )
        # 3. Install key ---------------------------------------------------- #
        append_log(job, "Installing panel SSH key", step="install_key", progress=25)
        _install_panel_key(ssh, tunnel, disable_password=ctx.disable_password_login)

        # 4. Install backend ------------------------------------------------ #
        backend_kind = _resolve_backend_kind(tunnel)
        backend = backend_mod.get_backend(backend_kind, ssh)
        append_log(
            job,
            f"Installing backend '{backend_kind}' ({backend.license})",
            step="install_backend",
            progress=40,
        )
        install = backend.install(offline_binary=ctx.offline_binary)
        for line in install.log:
            append_log(job, f"  {line}")
        if not install.ok:
            raise TunnelError(install.error or "backend install failed")
        tunnel.backend_version = install.version

        # 5. Configure ------------------------------------------------------ #
        append_log(job, "Writing tunnel configuration", step="configure", progress=55)
        transport = _resolve_transport(tunnel, backend_kind)
        exit_host = tunnel.exit_host or tunnel.entry_host
        configured = backend.configure(
            name=tunnel.name,
            token=tunnel.token,
            transport=transport,
            control_port=tunnel.control_port,
            public_ports=tunnel.port_list(),
            exit_host=exit_host,
            mtu=tunnel.mtu,
        )
        if not configured.ok:
            raise TunnelError(configured.error or "configuration failed")

        # 6. MTU ------------------------------------------------------------ #
        append_log(job, "Discovering path MTU (DF-bit ping)", step="mtu", progress=70)
        if ctx.spec.mtu is None:
            mtu_result = _discover_mtu(ssh, tunnel.entry_host)
            if mtu_result.ok and mtu_result.mtu:
                tunnel.mtu = mtu_result.mtu
                tunnel.mssfix = mtu_result.mssfix
                append_log(job, f"Path MTU {mtu_result.mtu}, mssfix {mtu_result.mssfix}")
        else:
            append_log(job, f"Using configured MTU {tunnel.mtu}")

        # 7. Start ---------------------------------------------------------- #
        append_log(job, "Starting the tunnel", step="start", progress=85)
        started = backend.start(tunnel.name)
        if not started.ok:
            raise TunnelError(started.stderr or "failed to start the tunnel")

        # 8. Healthcheck ---------------------------------------------------- #
        append_log(job, "Running end-to-end health check", step="healthcheck", progress=95)
        health = backend.healthcheck(host=tunnel.entry_host, port=tunnel.control_port)
        if not health.running:
            append_log(job, f"warning: control port not yet reachable ({health.detail})")
        tunnel.status = TunnelStatus.ACTIVE
        tunnel.last_seen_at = utcnow()
    finally:
        if ssh is not None:
            with contextlib.suppress(Exception):
                ssh.close()
        db.flush()


def _install_panel_key(
    ssh: SSHTransport, tunnel: Tunnel, *, disable_password: bool = False
) -> None:
    """Append the panel's public key to ``authorized_keys`` (mode 600)."""

    public = ""
    if tunnel.config:
        with contextlib.suppress(ValueError):
            public = json.loads(tunnel.config).get("panel_public_key", "")
    if not public:
        return
    line = f"{public} ovpn-panel:{tunnel.name}"
    script = (
        "set -e; install -d -m 0700 ~/.ssh; touch ~/.ssh/authorized_keys; "
        "chmod 600 ~/.ssh/authorized_keys; "
        f"grep -qxF {tunnel_ssh.quote(line)} ~/.ssh/authorized_keys || "
        f"printf '%s\\n' {tunnel_ssh.quote(line)} >> ~/.ssh/authorized_keys"
    )
    result = ssh.run(script, timeout=30)
    if not result.ok:
        raise SSHError(result.stderr or "failed to install panel key")
    if disable_password:
        ssh.run(
            "sed -i 's/^#\\?PasswordAuthentication .*/PasswordAuthentication no/' "
            "/etc/ssh/sshd_config && (systemctl reload sshd || systemctl reload ssh) || true",
            timeout=30,
        )


def _discover_mtu(ssh: SSHTransport, target: str):  # noqa: ANN201
    from app.services import mtu_finder

    return mtu_finder.find_mtu(target, ssh=ssh)


# --------------------------------------------------------------------------- #
# Operations (start/stop/restart/update/uninstall)
# --------------------------------------------------------------------------- #
def operate(
    db: Session,
    tunnel: Tunnel,
    action: str,
    *,
    password: str | None = None,
    private_key: str | None = None,
    offline_binary: bytes | None = None,
    transport_factory=None,  # noqa: ANN001
) -> TunnelProvisionJob:
    """Run an operation (start/stop/restart/update/uninstall) with a live job."""

    job = create_job(db, tunnel_id=tunnel.id, kind=action)
    job.status = TunnelProvisionJobStatus.RUNNING
    db.flush()
    ssh: SSHTransport | None = None
    try:
        ssh = connect(
            tunnel,
            password=password,
            private_key=private_key,
            transport_factory=transport_factory,
        )
        backend_kind = _resolve_backend_kind(tunnel)
        backend = backend_mod.get_backend(backend_kind, ssh)
        append_log(job, f"{action}: backend {backend_kind}", step=action, progress=20)

        if action == "start":
            result = backend.start(tunnel.name)
            tunnel.status = TunnelStatus.ACTIVE if result.ok else TunnelStatus.ERROR
        elif action == "stop":
            result = backend.stop(tunnel.name)
            tunnel.status = TunnelStatus.STOPPED if result.ok else TunnelStatus.ERROR
        elif action == "restart":
            result = backend.restart(tunnel.name)
            tunnel.status = TunnelStatus.ACTIVE if result.ok else TunnelStatus.ERROR
        elif action == "update":
            update = backend.update(offline_binary=offline_binary)
            result = update and _as_cmd(update.ok, update.error)
            tunnel.backend_version = update.version or tunnel.backend_version
        elif action == "uninstall":
            result = backend.uninstall()
            tunnel.status = TunnelStatus.UNINSTALLED
        else:
            raise TunnelError(f"unknown action: {action}")

        if result.stdout:
            append_log(job, result.stdout.strip())
        if not result.ok:
            raise TunnelError(result.stderr or f"{action} failed")
        append_log(job, f"{action} completed", step="done", progress=100)
        job.status = TunnelProvisionJobStatus.DONE
        job.completed_at = utcnow()
        tunnel.last_error = None
        tunnel.last_seen_at = utcnow()
    except (TunnelError, SSHError) as exc:
        append_log(job, f"FAILED: {exc}", step="failed", progress=100)
        job.status = TunnelProvisionJobStatus.FAILED
        job.error = str(exc)
        job.completed_at = utcnow()
        tunnel.status = TunnelStatus.ERROR
        tunnel.last_error = str(exc)
    finally:
        if ssh is not None:
            with contextlib.suppress(Exception):
                ssh.close()
        db.flush()
    return job


def _as_cmd(ok: bool, error: str | None) -> tunnel_ssh.CommandResult:
    return tunnel_ssh.CommandResult(ok=ok, exit_code=0 if ok else 1, stdout="", stderr=error or "")


def rollback(
    db: Session,
    tunnel: Tunnel,
    *,
    transport_factory=None,  # noqa: ANN001
) -> TunnelProvisionJob:
    """Best-effort uninstall used by the wizard's rollback button."""

    job = operate(db, tunnel, "uninstall", transport_factory=transport_factory)
    if job.status == TunnelProvisionJobStatus.DONE:
        job.status = TunnelProvisionJobStatus.ROLLED_BACK
        job.rolled_back = True
        tunnel.status = TunnelStatus.UNINSTALLED
    db.flush()
    return job


# --------------------------------------------------------------------------- #
# Health & self-healing
# --------------------------------------------------------------------------- #
def record_health(
    db: Session,
    tunnel: Tunnel,
    *,
    ok: bool,
    latency_ms: float = 0.0,
    packet_loss: float = 0.0,
    throughput_kbps: float = 0.0,
    detail: str = "",
) -> TunnelHealthSample:
    sample = TunnelHealthSample(
        tunnel_id=tunnel.id,
        ok=ok,
        latency_ms=latency_ms,
        packet_loss=packet_loss,
        throughput_kbps=throughput_kbps,
        uptime_seconds=tunnel.uptime_seconds,
        detail=detail[:500] if detail else None,
    )
    db.add(sample)
    db.flush()
    return sample


def latest_sample(db: Session, tunnel_id: int) -> TunnelHealthSample | None:
    return db.scalar(
        select(TunnelHealthSample)
        .where(TunnelHealthSample.tunnel_id == tunnel_id)
        .order_by(TunnelHealthSample.recorded_at.desc(), TunnelHealthSample.id.desc())
    )


def health_history(db: Session, tunnel_id: int, *, limit: int = 100) -> list[TunnelHealthSample]:
    return list(
        db.scalars(
            select(TunnelHealthSample)
            .where(TunnelHealthSample.tunnel_id == tunnel_id)
            .order_by(TunnelHealthSample.recorded_at.desc())
            .limit(limit)
        )
    )


def probe_tunnel(
    db: Session,
    tunnel: Tunnel,
    *,
    ssh: SSHTransport | None = None,
    transport_factory=None,  # noqa: ANN001
) -> TunnelHealthSample:
    """Probe a tunnel end-to-end and record the sample."""

    latency, loss, reachable = tunnel_benchmark.probe_icmp(tunnel.entry_host, ssh=ssh)
    ok = reachable and loss < 100.0
    sample = record_health(
        db,
        tunnel,
        ok=ok,
        latency_ms=latency,
        packet_loss=loss,
        detail="reachable" if ok else "unreachable",
    )
    tunnel.last_seen_at = utcnow() if ok else tunnel.last_seen_at
    if ok:
        tunnel.consecutive_failures = 0
        if tunnel.status in (TunnelStatus.DEGRADED, TunnelStatus.ERROR):
            tunnel.status = TunnelStatus.ACTIVE
    else:
        tunnel.consecutive_failures += 1
        if tunnel.consecutive_failures >= HEAL_THRESHOLD:
            tunnel.status = TunnelStatus.DEGRADED
    db.flush()
    return sample


def maybe_self_heal(
    db: Session,
    tunnel: Tunnel,
    *,
    transport_factory=None,  # noqa: ANN001
) -> bool:
    """Restart a tunnel after ``HEAL_THRESHOLD`` consecutive probe failures."""

    if not tunnel.auto_heal:
        return False
    if tunnel.consecutive_failures < HEAL_THRESHOLD:
        return False
    logger.warning(
        "self-healing tunnel %s after %s failures", tunnel.name, tunnel.consecutive_failures
    )
    _notify(db, tunnel, "restarting after repeated failures", event="tunnel_self_heal")
    job = operate(db, tunnel, "restart", transport_factory=transport_factory)
    if job.status == TunnelProvisionJobStatus.DONE:
        tunnel.consecutive_failures = 0
        db.flush()
        _notify(db, tunnel, "recovered after automatic restart", event="tunnel_recovered")
        return True
    _notify(db, tunnel, "automatic restart failed", event="tunnel_heal_failed")
    return False


def _notify(db: Session, tunnel: Tunnel, message: str, *, event: str) -> None:
    """Best-effort tunnel alert (never breaks the caller)."""

    try:
        from app.services import notifications

        notifications.notify_tunnel(db, tunnel.name, message, event=event)
    except Exception as exc:  # noqa: BLE001
        logger.debug("tunnel notification skipped: %s", exc)


# --------------------------------------------------------------------------- #
# Config delivery helpers
# --------------------------------------------------------------------------- #
def effective_endpoint(tunnel: Tunnel) -> tuple[str, int]:
    """Return ``(host, port)`` clients should connect to for this tunnel.

    Uses the entry host and the *first* exposed public port, falling back to the
    control port when no ports are configured.
    """

    ports = tunnel.port_list()
    return tunnel.entry_host, (ports[0] if ports else tunnel.control_port)


def active_tunnel_for_node(db: Session, node_id: int) -> Tunnel | None:
    """The active tunnel attached to a node (if any), preferring ``active``."""

    stmt = (
        select(Tunnel)
        .where(
            Tunnel.node_id == node_id,
            Tunnel.is_active.is_(True),
            Tunnel.status == TunnelStatus.ACTIVE,
        )
        .order_by(Tunnel.id)
    )
    return db.scalar(stmt)


def tunnels_for_node(db: Session, node_id: int) -> list[Tunnel]:
    return list(
        db.scalars(
            select(Tunnel)
            .where(Tunnel.node_id == node_id, Tunnel.is_active.is_(True))
            .order_by(Tunnel.name)
        )
    )


def attached_instance_names(db: Session, tunnel_id: int) -> list[str]:
    tunnel = get(db, tunnel_id)
    if tunnel is None or not tunnel.instance_name:
        return []
    return [tunnel.instance_name]


def apply_mtu_to_instance(tunnel: Tunnel, *, instance: str | None = None) -> tuple[bool, str]:
    """Push ``tun-mtu``/``mssfix`` into the attached OpenVPN instance config.

    Uses the same validate-write-restart-rollback path as the server-config
    editor (:mod:`app.services.server_config`) so a bad value can never break
    the running instance.
    """

    from app.services import server_config

    name = instance or tunnel.instance_name
    if not name:
        return False, "tunnel is not attached to an instance"
    path = f"/etc/openvpn/instances/{name}.conf"
    content = server_config.read_config(path)
    if not content:
        return False, "instance config not readable"
    lines = [
        line for line in content.splitlines() if not line.strip().startswith(("tun-mtu", "mssfix"))
    ]
    if tunnel.mtu:
        lines.append(f"tun-mtu {tunnel.mtu}")
    if tunnel.mssfix:
        lines.append(f"mssfix {tunnel.mssfix}")
    result = server_config.apply("\n".join(lines) + "\n", path=path, instance=name)
    return result.ok, result.message


# --------------------------------------------------------------------------- #
# Summary helpers (dashboard)
# --------------------------------------------------------------------------- #
def summary(db: Session, *, owner_id: int | None = None) -> dict[str, int]:
    stmt = select(Tunnel.status, func.count(Tunnel.id)).group_by(Tunnel.status)
    if owner_id is not None:
        stmt = stmt.where(Tunnel.owner_id == owner_id)
    counts = {status.value: 0 for status in TunnelStatus}
    for status, count in db.execute(stmt):
        counts[status.value] = int(count)
    counts["total"] = sum(v for k, v in counts.items() if k != "total")
    return counts


def recent_health(db: Session, tunnel_id: int, *, limit: int = 48) -> list[TunnelHealthSample]:
    return list(reversed(health_history(db, tunnel_id, limit=limit)))


def random_token() -> str:
    return generate_token(24)


def random_password(length: int = 20) -> str:
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def node_options(db: Session) -> list[Node]:
    return list(db.scalars(select(Node).order_by(Node.name)))
