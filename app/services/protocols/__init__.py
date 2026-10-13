"""Protocol backend registry (Phase 12).

Maps a persisted ``protocol_instances.kind`` to its :class:`ProtocolBackend`
implementation and exposes the catalogue used by the UI/API.
"""

from __future__ import annotations

from app.services.protocols.base import (
    ExecResult,
    Executor,
    HealthResult,
    InstallResult,
    OnlineSession,
    ProtocolBackend,
    ProtocolError,
    Usage,
    UserConfig,
    UserSpec,
)
from app.services.protocols.l2tp import L2tpBackend
from app.services.protocols.ocserv import OcservBackend
from app.services.protocols.openvpn import OpenVpnBackend
from app.services.protocols.singbox import SingBoxBackend
from app.services.protocols.wireguard import WireGuardBackend

BACKENDS: dict[str, type[ProtocolBackend]] = {
    OpenVpnBackend.kind: OpenVpnBackend,
    WireGuardBackend.kind: WireGuardBackend,
    OcservBackend.kind: OcservBackend,
    L2tpBackend.kind: L2tpBackend,
    SingBoxBackend.kind: SingBoxBackend,
}


def get_backend_class(kind: str) -> type[ProtocolBackend]:
    cls = BACKENDS.get((kind or "").lower())
    if cls is None:
        raise ProtocolError(f"unknown protocol backend: {kind}")
    return cls


def get_backend(instance, executor: Executor) -> ProtocolBackend:  # noqa: ANN001
    """Instantiate the backend for a persisted protocol instance."""

    kind = getattr(instance, "kind", "") or ""
    return get_backend_class(kind)(executor, instance)


def backend_metadata() -> list[dict[str, object]]:
    """Catalogue for the UI (kind, capabilities, upstream, license, install)."""

    return [
        {
            "kind": cls.kind,
            "display_name": cls.display_name,
            "capabilities": list(cls.capabilities),
            "upstream": cls.upstream,
            "license": cls.license,
            "supports_install": cls.supports_install,
            "supports_password": cls.supports_password,
            "supports_traffic": cls.supports_traffic,
        }
        for cls in BACKENDS.values()
    ]


__all__ = [
    "BACKENDS",
    "ExecResult",
    "Executor",
    "HealthResult",
    "InstallResult",
    "OnlineSession",
    "ProtocolBackend",
    "ProtocolError",
    "Usage",
    "UserConfig",
    "UserSpec",
    "backend_metadata",
    "get_backend",
    "get_backend_class",
]
