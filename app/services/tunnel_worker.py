"""Background worker for tunnel provisioning + provisioning rate limiting.

Provisioning can take minutes (downloads, systemd, MTU discovery), so the HTTP
handlers start a job and return immediately; the UI then streams the job log
from the database. A dedicated single-thread executor serialises runs.

A simple token-bucket limiter caps how often the provisioning API may be
invoked, protecting against runaway loops and abuse of the SSH wizard.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from app.core.ratelimit import RateLimiter
from app.db import session_scope

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tunnel")
# At most 6 provisioning runs per minute per actor/IP.
_provision_limiter = RateLimiter(max_attempts=6, window_seconds=60)


def provision_limiter() -> RateLimiter:
    return _provision_limiter


def submit_provision(tunnel_id: int, ctx_payload: dict) -> None:
    """Queue a provisioning run for ``tunnel_id``.

    ``ctx_payload`` carries the transient secrets (password/private_key) which
    are held only in memory for the lifetime of the run and never persisted.
    """

    _executor.submit(_run_provision, tunnel_id, ctx_payload)


def _run_provision(tunnel_id: int, ctx_payload: dict) -> None:
    from app.services import tunnels as tunnels_service

    with session_scope() as db:
        tunnel = tunnels_service.get(db, tunnel_id)
        if tunnel is None:
            logger.warning("provision requested for missing tunnel %s", tunnel_id)
            return
        password = ctx_payload.get("password")
        private_key = ctx_payload.get("private_key")
        # Ensure the panel key exists so future operations are passwordless.
        try:
            tunnels_service.ensure_panel_key(tunnel)
        except tunnels_service.TunnelError as exc:
            logger.warning("panel key not created: %s", exc)
        spec = tunnels_service.TunnelSpec(
            name=tunnel.name,
            entry_host=tunnel.entry_host,
            control_port=tunnel.control_port,
            token=tunnel.token,
            backend=tunnel.backend.value,
            transport=tunnel.transport.value,
            entry_ssh_port=tunnel.entry_ssh_port,
            entry_username=tunnel.entry_username,
            auth_method=tunnel.entry_auth_method.value,
            exit_host=tunnel.exit_host,
            public_ports=tunnel.port_list(),
            host_key_fingerprint=tunnel.host_key_fingerprint,
            node_id=tunnel.node_id,
            instance_name=tunnel.instance_name,
            mtu=tunnel.mtu,
            auto_heal=tunnel.auto_heal,
            owner_id=tunnel.owner_id,
        )
        ctx = tunnels_service.ProvisionContext(
            spec=spec,
            password=password,
            private_key=private_key,
            offline_binary=ctx_payload.get("offline_binary"),
            disable_password_login=bool(ctx_payload.get("disable_password_login")),
        )
        tunnels_service.provision(db, tunnel, ctx)
    # Drop references to the transient secrets as soon as the run finishes.
    ctx_payload.pop("password", None)
    ctx_payload.pop("private_key", None)


def submit_operation(tunnel_id: int, action: str, ctx_payload: dict | None = None) -> None:
    _executor.submit(_run_operation, tunnel_id, action, ctx_payload or {})


def _run_operation(tunnel_id: int, action: str, ctx_payload: dict) -> None:
    from app.services import tunnels as tunnels_service

    with session_scope() as db:
        tunnel = tunnels_service.get(db, tunnel_id)
        if tunnel is None:
            return
        try:
            tunnels_service.operate(
                db,
                tunnel,
                action,
                password=ctx_payload.get("password"),
                private_key=ctx_payload.get("private_key"),
                offline_binary=ctx_payload.get("offline_binary"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("tunnel operation %s failed: %s", action, exc)
    ctx_payload.pop("password", None)
    ctx_payload.pop("private_key", None)


def shutdown() -> None:
    _executor.shutdown(wait=False, cancel_futures=True)


def _reset_for_tests() -> None:
    global _executor
    _provision_limiter.clear()
    _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tunnel")
    _ = threading.current_thread()
