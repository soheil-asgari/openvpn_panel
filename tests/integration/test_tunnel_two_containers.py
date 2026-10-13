"""Two-container integration test for the Tunnel Manager.

This test provisions a real Backhaul tunnel between two throwaway containers:

* **exit** container — runs a tiny TCP echo server that stands in for the
  OpenVPN instance.
* **entry** container — receives the panel's provisioning over SSH and forwards
  a public port through the tunnel to the exit.

It is skipped automatically unless Docker and the ``integration`` marker are
enabled, so it never slows down the hermetic unit suite:

    OVPN_TUNNEL_INTEGRATION=1 python -m pytest tests/integration -m integration

Bring the topology up with :file:`docker-compose.yml` in this directory first
(``docker compose -f tests/integration/docker-compose.yml up -d``), or pass
``--build-topology`` to let the test do it.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ENTRY_HOST = os.environ.get("OVPN_TUNNEL_ENTRY_HOST", "127.0.0.1")
ENTRY_SSH_PORT = int(os.environ.get("OVPN_TUNNEL_ENTRY_SSH_PORT", "2222"))
ENTRY_SSH_USER = os.environ.get("OVPN_TUNNEL_ENTRY_SSH_USER", "root")
ENTRY_SSH_PASSWORD = os.environ.get("OVPN_TUNNEL_ENTRY_SSH_PASSWORD", "root")
PUBLIC_PORT = int(os.environ.get("OVPN_TUNNEL_PUBLIC_PORT", "8443"))
EXIT_PORT = int(os.environ.get("OVPN_TUNNEL_EXIT_PORT", "9443"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("OVPN_TUNNEL_INTEGRATION") != "1",
        reason="set OVPN_TUNNEL_INTEGRATION=1 to run the Docker integration test",
    ),
]


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    with contextlib.suppress(Exception):
        return (
            subprocess.run(  # noqa: S603
                ["docker", "info"], capture_output=True, timeout=15, check=False
            ).returncode
            == 0
        )
    return False


requires_docker = pytest.mark.skipif(not _docker_available(), reason="docker is not available")


@pytest.fixture(scope="module")
def topology() -> None:  # noqa: ANN001
    """Bring the two-container topology up for the module, then tear it down."""

    compose = HERE / "docker-compose.yml"
    subprocess.run(  # noqa: S603
        ["docker", "compose", "-f", str(compose), "up", "-d", "--build"],
        check=True,
        timeout=600,
    )
    # Wait for the entry container's SSH to accept connections.
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            with socket.create_connection((ENTRY_HOST, ENTRY_SSH_PORT), timeout=2):
                break
        except OSError:
            time.sleep(2)
    else:  # pragma: no cover - environment dependent
        subprocess.run(  # noqa: S603
            ["docker", "compose", "-f", str(compose), "down", "-v"], check=False
        )
        pytest.fail("entry container SSH did not come up")
    yield
    subprocess.run(  # noqa: S603
        ["docker", "compose", "-f", str(compose), "down", "-v"], check=False
    )


@requires_docker
def test_end_to_end_tunnel_provisioning(topology: None) -> None:  # noqa: ANN001, ARG001
    """Provision a Backhaul tunnel and verify the forwarded port is reachable."""

    os.environ["OVPN_DISABLE_SCHEDULER"] = "true"
    from app.config import reset_settings_cache
    from app.db import get_session_factory, reset_engine, session_scope
    from app.models import Base
    from app.services import tunnel_ssh
    from app.services import tunnels as tunnels_service

    reset_settings_cache()
    reset_engine()
    Base.metadata.create_all(bind=__import__("app.db", fromlist=["get_engine"]).get_engine())

    fingerprint = tunnel_ssh.probe_fingerprint(ENTRY_HOST, port=ENTRY_SSH_PORT)

    with session_scope() as db:
        spec = tunnels_service.TunnelSpec(
            name="itest",
            entry_host=ENTRY_HOST,
            entry_ssh_port=ENTRY_SSH_PORT,
            entry_username=ENTRY_SSH_USER,
            control_port=25000,
            token=tunnels_service.random_token(),
            backend="backhaul",
            transport="tcp",
            exit_host=os.environ.get("OVPN_TUNNEL_EXIT_HOST", "10.99.0.3"),
            public_ports=[PUBLIC_PORT],
            host_key_fingerprint=fingerprint,
        )
        tunnel = tunnels_service.create_tunnel(db, spec)
        tunnels_service.ensure_panel_key(tunnel)
        ctx = tunnels_service.ProvisionContext(spec=spec, password=ENTRY_SSH_PASSWORD)
        job = tunnels_service.provision(db, tunnel, ctx)

    assert job.status.value == "done", job.log

    # The forwarded port on the entry host should now reach the exit echo server.
    deadline = time.time() + 60
    connected = False
    while time.time() < deadline:
        try:
            with socket.create_connection((ENTRY_HOST, PUBLIC_PORT), timeout=3) as sock:
                sock.sendall(b"ping")
                connected = sock.recv(16) == b"ping"
                if connected:
                    break
        except OSError:
            time.sleep(2)

    # Clean up regardless of the assertion outcome.
    with session_scope() as db:
        tunnel = tunnels_service.get_by_name(db, "itest")
        if tunnel is not None:
            with contextlib.suppress(Exception):
                tunnels_service.rollback(db, tunnel)

    _ = get_session_factory
    assert connected, f"tunnel port {PUBLIC_PORT} did not forward to the exit host"
