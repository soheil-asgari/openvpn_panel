"""Subscription page logic: tokens, per-user node visibility and config builds.

The public subscription URL (``/sub/<token>``) is unauthenticated but keyed by a
long random token, so the service here is the single place that resolves a token
to a VPN user and decides which nodes/configs may be exposed.
"""

from __future__ import annotations

import contextlib
import secrets
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.node import Node
from app.models.subscription import VpnUserHiddenNode
from app.models.vpn_user import VpnUser
from app.services import config_builder, helper_client

TOKEN_BYTES = 24  # -> 32 URL-safe characters


@dataclass(slots=True)
class SubscriptionNode:
    """A node exposed to a subscriber together with a rendered ``.ovpn``."""

    node: Node
    config_text: str
    remote: str
    user: VpnUser


@dataclass(slots=True)
class ProtocolLink:
    """A per-protocol client config / share link shown on the subscription."""

    kind: str
    text: str = ""
    uri: str = ""
    qr_payload: str = ""
    filename: str = ""


@dataclass(slots=True)
class SubscriptionView:
    """Everything the subscription template needs, already resolved."""

    vpn_user: VpnUser
    nodes: list[SubscriptionNode] = field(default_factory=list)
    branding: dict[str, str] = field(default_factory=dict)
    protocols: list[ProtocolLink] = field(default_factory=list)

    @property
    def aggregated_link(self) -> str:
        return "\n".join(p.uri for p in self.protocols if p.uri)

    @property
    def has_protocol_links(self) -> bool:
        return bool(self.aggregated_link)

    @property
    def is_usable(self) -> bool:
        from app.services import vpn_users as vpn_users_service

        return vpn_users_service.is_connectable(self.vpn_user)

    @property
    def usage_percent(self) -> float:
        limit = self.vpn_user.data_limit_bytes
        if not limit:
            return 0.0
        return min(100.0, round(self.vpn_user.used_bytes / limit * 100, 1))


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def ensure_token(db: Session, vpn_user: VpnUser) -> str:
    """Return the user's subscription token, creating one on first use."""

    if not vpn_user.subscription_token:
        vpn_user.subscription_token = generate_token()
        db.flush()
    return vpn_user.subscription_token


def regenerate_token(db: Session, vpn_user: VpnUser) -> str:
    """Replace the subscription token (invalidates the previous URL)."""

    vpn_user.subscription_token = generate_token()
    db.flush()
    return vpn_user.subscription_token


def get_by_token(db: Session, token: str) -> VpnUser | None:
    if not token or len(token) > 64:
        return None
    return db.scalar(select(VpnUser).where(VpnUser.subscription_token == token))


# --------------------------------------------------------------------------- #
# Per-user node visibility
# --------------------------------------------------------------------------- #
def hidden_node_ids(db: Session, vpn_user_id: int) -> set[int]:
    stmt = select(VpnUserHiddenNode.node_id).where(VpnUserHiddenNode.vpn_user_id == vpn_user_id)
    return set(db.scalars(stmt))


def set_hidden_nodes(db: Session, vpn_user: VpnUser, node_ids: set[int]) -> None:
    """Replace the hidden-node override set for a user."""

    existing = list(
        db.scalars(select(VpnUserHiddenNode).where(VpnUserHiddenNode.vpn_user_id == vpn_user.id))
    )
    for row in existing:
        if row.node_id not in node_ids:
            db.delete(row)
    already = {row.node_id for row in existing}
    for node_id in node_ids - already:
        db.add(VpnUserHiddenNode(vpn_user_id=vpn_user.id, node_id=node_id))
    db.flush()


def visible_nodes(db: Session, vpn_user: VpnUser) -> list[Node]:
    """Active nodes visible to the user (honouring hide overrides)."""

    hidden = hidden_node_ids(db, vpn_user.id)
    stmt = select(Node).where(Node.is_active.is_(True)).order_by(Node.name)
    if hidden:
        stmt = stmt.where(Node.id.notin_(hidden))
    return list(db.scalars(stmt))


def resolve_endpoint(db: Session, vpn_user: VpnUser, node: Node) -> tuple[str, int, object | None]:
    """Return ``(host, port, tunnel)`` used to advertise ``node``'s config.

    When the user opted into the tunnel (``use_tunnel``) and the node has an
    active tunnel attached, the entry host and its first public port are used;
    otherwise the node's own exit host/port are returned with ``tunnel=None``.
    """

    if not vpn_user.use_tunnel:
        return node.host, node.port, None
    from app.services import tunnels as tunnels_service

    tunnel = tunnels_service.active_tunnel_for_node(db, node.id)
    if tunnel is None:
        return node.host, node.port, None
    host, port = tunnels_service.effective_endpoint(tunnel)
    return host, port, tunnel


def build_view(
    db: Session, vpn_user: VpnUser, *, branding: dict[str, str] | None = None
) -> SubscriptionView:
    """Assemble the subscription view (nodes + rendered configs)."""

    from app.services import settings as settings_service

    nodes = visible_nodes(db, vpn_user)
    entries: list[SubscriptionNode] = []
    for node in nodes:
        host, port, tunnel = resolve_endpoint(db, vpn_user, node)
        config_text = build_config_for(vpn_user, node, host=host, port=port)
        remote = config_builder.remote_line(host, port, node.protocol)
        entry = SubscriptionNode(node=node, config_text=config_text, remote=remote, user=vpn_user)
        # Surface the tunnel (if any) for the UI badge; attribute is optional.
        with contextlib.suppress(Exception):
            entry.tunnel = tunnel  # type: ignore[attr-defined]
        entries.append(entry)
    return SubscriptionView(
        vpn_user=vpn_user,
        nodes=entries,
        branding=branding if branding is not None else settings_service.get_branding(db),
        protocols=protocol_links(db, vpn_user),
    )


def protocol_links(db: Session, vpn_user: VpnUser) -> list[ProtocolLink]:
    """Render per-protocol client configs / share links for a subscriber."""

    from app.services import protocols_service

    node = vpn_user.node
    try:
        configs = protocols_service.configs_for_user(db, vpn_user, node)
    except Exception:  # noqa: BLE001 - a broken backend must not break the page
        return []
    return [
        ProtocolLink(
            kind=c.kind,
            text=c.text,
            uri=c.uri,
            qr_payload=c.qr_payload,
            filename=c.filename,
        )
        for c in configs
    ]


def build_config_for(
    vpn_user: VpnUser,
    node: Node,
    *,
    host: str | None = None,
    port: int | None = None,
) -> str:
    """Render an ``.ovpn`` for a user/node pair.

    Tries the privileged helper first (which embeds the CA/tls-crypt material)
    and falls back to a syntactically valid config without certificates. When
    ``host``/``port`` are supplied (tunnel endpoint) the fallback config points
    at them.
    """

    common_name = vpn_user.common_name or vpn_user.username
    try:
        result = helper_client.generate_client_config(common_name=common_name)
        if result.ok and result.data and result.data.get("config"):
            return str(result.data["config"])
    except helper_client.HelperError:
        pass
    return config_builder.build_client_config(
        host=host or node.host,
        port=port or node.port,
        protocol=node.protocol,
        cipher=node.cipher,
    )


def config_filename(vpn_user: VpnUser, node: Node) -> str:
    """Safe ``.ovpn`` filename derived from the user and node names."""

    def clean(value: str) -> str:
        return "".join(ch for ch in value if ch.isalnum() or ch in "._-") or "config"

    return f"{clean(vpn_user.username)}-{clean(node.name)}.ovpn"
