"""REST API (JSON) authenticated with per-user API keys.

Mounted at ``/api/v1``. All responses are JSON. Access is scoped: admins see
everything, resellers only see their own VPN users.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import select

from app.api.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    BulkResult,
    Message,
    NodeConfigIn,
    NodeConfigOut,
    NodeCreate,
    NodeCreated,
    NodeHealthOut,
    NodeInstallCommand,
    NodeMetricOut,
    NodeOut,
    NodeTaskOut,
    NodeUpdate,
    ReportSeries,
    ResellerCreate,
    ResellerOut,
    ResellerUpdate,
    ResellerUsage,
    StatsOut,
    SubscriptionInfo,
    SubscriptionToken,
    SubscriptionVisibility,
    TopUserOut,
    VersionInfo,
    VpnUserBulkCreate,
    VpnUserBulkExtend,
    VpnUserBulkIds,
    VpnUserCreate,
    VpnUserCredentials,
    VpnUserOut,
    VpnUserRenew,
    VpnUserUpdate,
)
from app.config import get_settings
from app.core import hmac_auth
from app.dependencies import ApiUser, DbSession, require_scope
from app.models.node import Node
from app.models.node_task import NodeTask
from app.models.user import Role
from app.models.vpn_user import VpnUserStatus
from app.services import api_keys as api_keys_service
from app.services import node_client, node_install
from app.services import nodes as nodes_service
from app.services import resellers as resellers_service
from app.services import subscriptions as subscriptions_service
from app.services import users as users_service
from app.services import vpn_sessions as vpn_sessions_service
from app.services import vpn_users as vpn_users_service

router = APIRouter()


def _scope_owner(user) -> int | None:  # noqa: ANN001
    return None if user.role == Role.ADMIN else user.id


# --------------------------------------------------------------------------- #
# VPN users
# --------------------------------------------------------------------------- #
@router.get("/users", response_model=list[VpnUserOut], tags=["users"])
def api_list_users(
    db: DbSession,
    user: ApiUser,
    search: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    node_id: int | None = None,
    sort_by: str = "username",
    sort_dir: str = Query(default="asc"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[VpnUserOut]:
    rows = vpn_users_service.list_vpn_users(
        db,
        owner_id=_scope_owner(user),
        node_id=node_id,
        search=search,
        status=status_filter,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )
    return [VpnUserOut.model_validate(r) for r in rows]


@router.get("/users/export", tags=["users"], response_class=Response)
def api_export_users(
    db: DbSession,
    user: ApiUser,
    search: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    node_id: int | None = None,
) -> Response:
    spec = vpn_users_service.FilterSpec(
        owner_id=_scope_owner(user),
        node_id=node_id,
        search=search,
        status=status_filter,
        limit=100000,
    )
    csv_text = vpn_users_service.export_csv(db, spec=spec)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=vpn_users.csv"},
    )


@router.get("/users/online", response_model=list[VpnUserOut], tags=["users"])
def api_online_users(db: DbSession, user: ApiUser) -> list[VpnUserOut]:
    rows = vpn_sessions_service.list_online_users(db, owner_id=_scope_owner(user))
    return [VpnUserOut.model_validate(r) for r in rows]


@router.post(
    "/users/bulk",
    response_model=list[VpnUserCredentials],
    status_code=status.HTTP_201_CREATED,
    tags=["users"],
)
def api_bulk_create_users(
    payload: VpnUserBulkCreate, db: DbSession, user: ApiUser
) -> list[VpnUserCredentials]:
    try:
        created = vpn_users_service.bulk_create(
            db,
            prefix=payload.prefix,
            count=payload.count,
            owner=user,
            node_id=payload.node_id,
            expires_at=payload.expires_at,
            expire_days=payload.expire_days,
            data_limit_bytes=payload.data_limit_bytes,
            max_connections=payload.max_connections,
            speed_limit_kbps=payload.speed_limit_kbps,
            password_length=payload.password_length,
        )
        db.commit()
    except vpn_users_service.VpnUserError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [
        VpnUserCredentials(id=item.user.id, username=item.user.username, password=item.password)
        for item in created
    ]


@router.post("/users/bulk/delete", response_model=BulkResult, tags=["users"])
def api_bulk_delete_users(payload: VpnUserBulkIds, db: DbSession, user: ApiUser) -> BulkResult:
    affected = vpn_users_service.bulk_delete(db, payload.ids, owner_id=_scope_owner(user))
    db.commit()
    return BulkResult(affected=affected)


@router.post("/users/bulk/enable", response_model=BulkResult, tags=["users"])
def api_bulk_enable_users(payload: VpnUserBulkIds, db: DbSession, user: ApiUser) -> BulkResult:
    rows = vpn_users_service.bulk_set_status(
        db, payload.ids, VpnUserStatus.ACTIVE, owner_id=_scope_owner(user)
    )
    db.commit()
    return BulkResult(affected=len(rows))


@router.post("/users/bulk/disable", response_model=BulkResult, tags=["users"])
def api_bulk_disable_users(payload: VpnUserBulkIds, db: DbSession, user: ApiUser) -> BulkResult:
    rows = vpn_users_service.bulk_set_status(
        db, payload.ids, VpnUserStatus.DISABLED, owner_id=_scope_owner(user)
    )
    for row in rows:
        vpn_sessions_service.terminate_active_sessions(db, row)
    db.commit()
    return BulkResult(affected=len(rows))


@router.post("/users/bulk/extend", response_model=BulkResult, tags=["users"])
def api_bulk_extend_users(payload: VpnUserBulkExtend, db: DbSession, user: ApiUser) -> BulkResult:
    try:
        rows = vpn_users_service.bulk_extend(
            db,
            payload.ids,
            days=payload.days,
            expires_at=payload.expires_at,
            owner_id=_scope_owner(user),
        )
        db.commit()
    except vpn_users_service.VpnUserError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BulkResult(affected=len(rows))


@router.post("/users/bulk/reset-traffic", response_model=BulkResult, tags=["users"])
def api_bulk_reset_traffic(payload: VpnUserBulkIds, db: DbSession, user: ApiUser) -> BulkResult:
    affected = vpn_users_service.bulk_reset_traffic(db, payload.ids, owner_id=_scope_owner(user))
    db.commit()
    return BulkResult(affected=affected)


@router.post(
    "/users", response_model=VpnUserOut, status_code=status.HTTP_201_CREATED, tags=["users"]
)
def api_create_user(payload: VpnUserCreate, db: DbSession, user: ApiUser) -> VpnUserOut:
    try:
        vpn_user = vpn_users_service.create_vpn_user(
            db,
            username=payload.username,
            password=payload.password,
            owner=user,
            node_id=payload.node_id,
            expires_at=payload.expires_at,
            expire_days=payload.expire_days,
            data_limit_bytes=payload.data_limit_bytes,
            max_connections=payload.max_connections,
            speed_limit_kbps=payload.speed_limit_kbps,
            common_name=payload.common_name,
        )
        db.commit()
    except vpn_users_service.VpnUserError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return VpnUserOut.model_validate(vpn_user)


def _get_scoped_vpn_user(db, user, vpn_user_id: int):  # noqa: ANN001
    vpn_user = vpn_users_service.get(db, vpn_user_id)
    if vpn_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    if user.role != Role.ADMIN and vpn_user.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    return vpn_user


@router.get("/users/{vpn_user_id}", response_model=VpnUserOut, tags=["users"])
def api_get_user(vpn_user_id: int, db: DbSession, user: ApiUser) -> VpnUserOut:
    return VpnUserOut.model_validate(_get_scoped_vpn_user(db, user, vpn_user_id))


@router.patch("/users/{vpn_user_id}", response_model=VpnUserOut, tags=["users"])
def api_update_user(
    vpn_user_id: int, payload: VpnUserUpdate, db: DbSession, user: ApiUser
) -> VpnUserOut:
    vpn_user = _get_scoped_vpn_user(db, user, vpn_user_id)
    if payload.password:
        vpn_users_service.set_password(db, vpn_user, payload.password)
    if payload.node_id is not None:
        try:
            resellers_service.enforce_node_allowed(db, vpn_user.owner, payload.node_id)
        except resellers_service.ResellerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        vpn_user.node_id = payload.node_id
    if payload.expires_at is not None:
        vpn_user.expires_at = payload.expires_at
        vpn_user.expire_days = None
    if payload.expire_days is not None:
        vpn_user.expire_days = payload.expire_days
    if payload.data_limit_bytes is not None:
        vpn_user.data_limit_bytes = payload.data_limit_bytes
    if payload.max_connections is not None:
        vpn_user.max_connections = payload.max_connections
    if payload.speed_limit_kbps is not None:
        vpn_user.speed_limit_kbps = payload.speed_limit_kbps
    if payload.status:
        try:
            status_value = VpnUserStatus(payload.status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid status") from exc
        vpn_users_service.set_status(db, vpn_user, status_value)
        if status_value == VpnUserStatus.DISABLED:
            vpn_sessions_service.terminate_active_sessions(db, vpn_user)
    db.commit()
    return VpnUserOut.model_validate(vpn_user)


@router.post("/users/{vpn_user_id}/reset-password", tags=["users"])
def api_reset_password(vpn_user_id: int, db: DbSession, user: ApiUser) -> VpnUserCredentials:
    vpn_user = _get_scoped_vpn_user(db, user, vpn_user_id)
    password = vpn_users_service.reset_password(db, vpn_user)
    db.commit()
    return VpnUserCredentials(id=vpn_user.id, username=vpn_user.username, password=password)


@router.post("/users/{vpn_user_id}/reset-traffic", response_model=VpnUserOut, tags=["users"])
def api_reset_traffic(vpn_user_id: int, db: DbSession, user: ApiUser) -> VpnUserOut:
    vpn_user = _get_scoped_vpn_user(db, user, vpn_user_id)
    vpn_users_service.reset_usage(db, vpn_user)
    db.commit()
    return VpnUserOut.model_validate(vpn_user)


@router.post("/users/{vpn_user_id}/renew", response_model=VpnUserOut, tags=["users"])
def api_renew_user(
    vpn_user_id: int, payload: VpnUserRenew, db: DbSession, user: ApiUser
) -> VpnUserOut:
    """Extend a user's expiry by ``days`` or to an absolute date."""

    vpn_user = _get_scoped_vpn_user(db, user, vpn_user_id)
    try:
        rows = vpn_users_service.bulk_extend(
            db,
            [vpn_user.id],
            days=payload.days,
            expires_at=payload.expires_at,
            owner_id=_scope_owner(user),
        )
    except vpn_users_service.VpnUserError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.reset_traffic:
        vpn_users_service.reset_usage(db, vpn_user)
    db.commit()
    return VpnUserOut.model_validate(rows[0] if rows else vpn_user)


@router.get("/users/{vpn_user_id}/usage", response_model=VpnUserOut, tags=["users"])
def api_user_usage(vpn_user_id: int, db: DbSession, user: ApiUser) -> VpnUserOut:
    vpn_user = _get_scoped_vpn_user(db, user, vpn_user_id)
    return VpnUserOut.model_validate(vpn_user)


@router.post("/users/{vpn_user_id}/kick", response_model=Message, tags=["users"])
def api_kick_user(vpn_user_id: int, db: DbSession, user: ApiUser) -> Message:
    vpn_user = _get_scoped_vpn_user(db, user, vpn_user_id)
    closed = vpn_sessions_service.terminate_active_sessions(db, vpn_user)
    db.commit()
    return Message(detail=f"closed {closed} session(s)")


@router.delete("/users/{vpn_user_id}", response_model=Message, tags=["users"])
def api_delete_user(vpn_user_id: int, db: DbSession, user: ApiUser) -> Message:
    vpn_user = _get_scoped_vpn_user(db, user, vpn_user_id)
    vpn_users_service.delete_vpn_user(db, vpn_user)
    db.commit()
    return Message(detail="deleted")


def _sub_url(request: Request, token: str) -> str:
    return f"{str(request.base_url).rstrip('/')}/sub/{token}"


@router.get(
    "/users/{vpn_user_id}/subscription",
    response_model=SubscriptionInfo,
    tags=["subscriptions"],
)
def api_get_subscription(
    vpn_user_id: int, request: Request, db: DbSession, user: ApiUser
) -> SubscriptionInfo:
    vpn_user = _get_scoped_vpn_user(db, user, vpn_user_id)
    token = subscriptions_service.ensure_token(db, vpn_user)
    hidden = subscriptions_service.hidden_node_ids(db, vpn_user.id)
    visible = [n.id for n in subscriptions_service.visible_nodes(db, vpn_user)]
    db.commit()
    return SubscriptionInfo(
        vpn_user_id=vpn_user.id,
        username=vpn_user.username,
        token=token,
        url=_sub_url(request, token),
        visible_node_ids=visible,
        hidden_node_ids=sorted(hidden),
    )


@router.post(
    "/users/{vpn_user_id}/subscription/token",
    response_model=SubscriptionToken,
    tags=["subscriptions"],
)
def api_regenerate_subscription_token(
    vpn_user_id: int, request: Request, db: DbSession, user: ApiUser
) -> SubscriptionToken:
    vpn_user = _get_scoped_vpn_user(db, user, vpn_user_id)
    token = subscriptions_service.regenerate_token(db, vpn_user)
    db.commit()
    return SubscriptionToken(token=token, url=_sub_url(request, token))


@router.put(
    "/users/{vpn_user_id}/subscription/visibility",
    response_model=SubscriptionInfo,
    tags=["subscriptions"],
)
def api_set_subscription_visibility(
    vpn_user_id: int,
    payload: SubscriptionVisibility,
    request: Request,
    db: DbSession,
    user: ApiUser,
) -> SubscriptionInfo:
    vpn_user = _get_scoped_vpn_user(db, user, vpn_user_id)
    all_nodes = {n.id for n in db.scalars(select(Node))}
    requested = set(payload.node_ids)
    unknown = requested - all_nodes
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown node ids: {sorted(unknown)}")
    subscriptions_service.set_hidden_nodes(db, vpn_user, all_nodes - requested)
    token = subscriptions_service.ensure_token(db, vpn_user)
    db.commit()
    visible = [n.id for n in subscriptions_service.visible_nodes(db, vpn_user)]
    return SubscriptionInfo(
        vpn_user_id=vpn_user.id,
        username=vpn_user.username,
        token=token,
        url=_sub_url(request, token),
        visible_node_ids=visible,
        hidden_node_ids=sorted(all_nodes - requested),
    )


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #
@router.get("/nodes", response_model=list[NodeOut], tags=["nodes"])
def api_list_nodes(db: DbSession, user: ApiUser) -> list[NodeOut]:
    rows = nodes_service.list_nodes(db)
    return [NodeOut.model_validate(r) for r in rows]


@router.post("/nodes", response_model=NodeCreated, status_code=201, tags=["nodes"])
def api_create_node(payload: NodeCreate, db: DbSession, user: ApiUser) -> NodeCreated:
    if user.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")
    try:
        node, agent_token = nodes_service.create_node(db, **payload.model_dump())
        db.commit()
    except nodes_service.NodeError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    out = NodeCreated.model_validate(node)
    out.agent_token = agent_token
    return out


@router.patch("/nodes/{node_id}", response_model=NodeOut, tags=["nodes"])
def api_update_node(node_id: int, payload: NodeUpdate, db: DbSession, user: ApiUser) -> NodeOut:
    if user.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")
    node = nodes_service.get(db, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    nodes_service.update_node(db, node, **payload.model_dump(exclude_unset=True))
    db.commit()
    return NodeOut.model_validate(node)


@router.delete("/nodes/{node_id}", response_model=Message, tags=["nodes"])
def api_delete_node(node_id: int, db: DbSession, user: ApiUser) -> Message:
    _require_admin(user)
    node = nodes_service.get(db, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    nodes_service.delete_node(db, node)
    db.commit()
    return Message(detail="deleted")


def _node_or_404(db, node_id: int):  # noqa: ANN001
    node = nodes_service.get(db, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    return node


@router.get("/nodes/{node_id}/install-command", response_model=NodeInstallCommand, tags=["nodes"])
def api_node_install_command(
    node_id: int, request: Request, db: DbSession, user: ApiUser
) -> NodeInstallCommand:
    _require_admin(user)
    node = _node_or_404(db, node_id)
    if not node.agent_secret:
        node.agent_secret = hmac_auth.generate_secret()
        db.commit()
    settings = get_settings()
    base = str(request.base_url).rstrip("/") + settings.admin_prefix
    return NodeInstallCommand(
        node_id=node.id,
        command=node_install.build_install_command(node, panel_url=base),
        install_script_url=f"{base}/nodes/agent/install.sh",
    )


@router.get("/nodes/{node_id}/health", response_model=NodeHealthOut, tags=["nodes"])
def api_node_health(node_id: int, db: DbSession, user: ApiUser) -> NodeHealthOut:
    _require_admin(user)
    node = _node_or_404(db, node_id)
    from app.services import node_sync

    reachable = node_sync.collect_metrics(db, node)
    db.commit()
    metric = node_sync.latest_metric(db, node.id)
    pending = node_sync.pending_tasks(db, node_id=node.id, limit=1000)
    return NodeHealthOut(
        node_id=node.id,
        name=node.name,
        status=node.status.value,
        last_seen_at=node.last_seen_at,
        metric=NodeMetricOut.model_validate(metric) if metric else None,
        pending_tasks=len(pending),
        reachable=reachable,
    )


@router.post("/nodes/{node_id}/reconcile", response_model=Message, tags=["nodes"])
def api_node_reconcile(node_id: int, db: DbSession, user: ApiUser) -> Message:
    _require_admin(user)
    node = _node_or_404(db, node_id)
    from app.services import node_sync

    synced, queued = node_sync.reconcile_node(db, node)
    db.commit()
    return Message(detail=f"synced {synced}, queued {queued}")


@router.get("/nodes/{node_id}/config", response_model=NodeConfigOut, tags=["nodes"])
def api_node_config(node_id: int, db: DbSession, user: ApiUser) -> NodeConfigOut:
    _require_admin(user)
    node = _node_or_404(db, node_id)
    if node.agent_url:
        try:
            result = node_client.NodeAgentClient(node).read_config()
        except node_client.NodeClientError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if result.ok:
            return NodeConfigOut(
                node_id=node.id,
                path=result.data.get("path"),
                content=result.data.get("content", ""),
            )
    return NodeConfigOut(node_id=node.id, path=None, content=node.config or "")


@router.put("/nodes/{node_id}/config", response_model=NodeConfigOut, tags=["nodes"])
def api_write_node_config(
    node_id: int, payload: NodeConfigIn, db: DbSession, user: ApiUser
) -> NodeConfigOut:
    _require_admin(user)
    node = _node_or_404(db, node_id)
    node.config = payload.content
    path = None
    if node.agent_url:
        try:
            result = node_client.NodeAgentClient(node).write_config(payload.content)
        except node_client.NodeClientError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.error or "agent rejected config")
        path = result.data.get("path")
    db.commit()
    return NodeConfigOut(node_id=node.id, path=path, content=payload.content)


@router.get("/nodes/{node_id}/tasks", response_model=list[NodeTaskOut], tags=["nodes"])
def api_node_tasks(node_id: int, db: DbSession, user: ApiUser) -> list[NodeTaskOut]:
    _require_admin(user)
    _node_or_404(db, node_id)
    stmt = (
        select(NodeTask)
        .where(NodeTask.node_id == node_id)
        .order_by(NodeTask.created_at.desc())
        .limit(200)
    )
    return [NodeTaskOut.model_validate(t) for t in db.scalars(stmt)]


@router.post("/nodes/{node_id}/tasks/retry", response_model=Message, tags=["nodes"])
def api_retry_node_tasks(node_id: int, db: DbSession, user: ApiUser) -> Message:
    _require_admin(user)
    _node_or_404(db, node_id)
    from app.services import node_sync

    for task in node_sync.pending_tasks(db, node_id=node_id, limit=500):
        task.run_after = datetime.now(timezone.utc)
    db.commit()
    return Message(detail="retry scheduled")


# --------------------------------------------------------------------------- #
# Resellers
# --------------------------------------------------------------------------- #
def _require_admin(user) -> None:  # noqa: ANN001
    if user.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")


def _reseller_usage_payload(db, reseller) -> ResellerUsage:  # noqa: ANN001
    summary = resellers_service.usage(db, reseller)
    allowed = sorted(resellers_service.allowed_node_ids(db, reseller))
    return ResellerUsage(
        reseller_id=reseller.id,
        username=reseller.username,
        user_count=summary.user_count,
        data_bytes=summary.data_bytes,
        quota_users=summary.quota_users,
        max_data_bytes=summary.max_data_bytes,
        users_remaining=summary.users_remaining,
        data_remaining=summary.data_remaining,
        allowed_node_ids=allowed,
        restricted=reseller.restrict_nodes,
        expired=resellers_service.is_expired(reseller),
    )


@router.get("/resellers", response_model=list[ResellerOut], tags=["resellers"])
def api_list_resellers(db: DbSession, user: ApiUser) -> list[ResellerOut]:
    _require_admin(user)
    rows = resellers_service.list_resellers(db)
    return [ResellerOut.model_validate(r) for r in rows]


@router.post("/resellers", response_model=ResellerOut, status_code=201, tags=["resellers"])
def api_create_reseller(payload: ResellerCreate, db: DbSession, user: ApiUser) -> ResellerOut:
    _require_admin(user)
    try:
        created = resellers_service.create_reseller(
            db,
            username=payload.username,
            password=payload.password,
            email=payload.email,
            parent_id=payload.parent_id,
            quota_users=payload.quota_users,
            max_data_bytes=payload.max_data_bytes,
            credit=payload.credit,
            expires_at=payload.expires_at,
            node_ids=set(payload.node_ids),
        )
        db.commit()
    except (users_service.UserError, resellers_service.ResellerError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResellerOut.model_validate(created)


@router.get("/resellers/{reseller_id}", response_model=ResellerOut, tags=["resellers"])
def api_get_reseller(reseller_id: int, db: DbSession, user: ApiUser) -> ResellerOut:
    _require_admin(user)
    reseller = resellers_service.get(db, reseller_id)
    if reseller is None:
        raise HTTPException(status_code=404, detail="reseller not found")
    return ResellerOut.model_validate(reseller)


@router.patch("/resellers/{reseller_id}", response_model=ResellerOut, tags=["resellers"])
def api_update_reseller(
    reseller_id: int, payload: ResellerUpdate, db: DbSession, user: ApiUser
) -> ResellerOut:
    _require_admin(user)
    reseller = resellers_service.get(db, reseller_id)
    if reseller is None:
        raise HTTPException(status_code=404, detail="reseller not found")
    try:
        resellers_service.update_reseller(
            db,
            reseller,
            password=payload.password,
            email=payload.email,
            quota_users=payload.quota_users,
            max_data_bytes=payload.max_data_bytes,
            credit=payload.credit,
            expires_at=payload.expires_at,
            is_active=payload.is_active,
            node_ids=set(payload.node_ids) if payload.node_ids is not None else None,
        )
        db.commit()
    except (users_service.UserError, resellers_service.ResellerError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResellerOut.model_validate(reseller)


@router.delete("/resellers/{reseller_id}", response_model=Message, tags=["resellers"])
def api_delete_reseller(reseller_id: int, db: DbSession, user: ApiUser) -> Message:
    _require_admin(user)
    reseller = resellers_service.get(db, reseller_id)
    if reseller is None:
        raise HTTPException(status_code=404, detail="reseller not found")
    resellers_service.delete_reseller(db, reseller)
    db.commit()
    return Message(detail="deleted")


@router.get("/resellers/{reseller_id}/usage", response_model=ResellerUsage, tags=["resellers"])
def api_reseller_usage(reseller_id: int, db: DbSession, user: ApiUser) -> ResellerUsage:
    _require_admin(user)
    reseller = resellers_service.get(db, reseller_id)
    if reseller is None:
        raise HTTPException(status_code=404, detail="reseller not found")
    return _reseller_usage_payload(db, reseller)


@router.get("/reports", response_model=ReportSeries, tags=["reports"])
def api_report(
    db: DbSession,
    user: ApiUser,
    preset: str = "daily",
    start: str | None = None,
    end: str | None = None,
    owner_id: int | None = None,
) -> ReportSeries:
    """Usage series for the caller (admin: optional ``owner_id``; reseller: self)."""

    from datetime import date as date_type

    from app.services import reports as reports_service

    scope = _scope_owner(user)
    if scope is None and owner_id:
        scope = owner_id

    def _parse(value: str | None) -> date_type | None:
        try:
            return date_type.fromisoformat(value) if value else None
        except ValueError:
            return None

    try:
        report = reports_service.build_report(
            db, owner_id=scope, preset=preset, start=_parse(start), end=_parse(end)
        )
    except reports_service.ReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ReportSeries(
        preset=report.preset,
        labels=report.labels,
        bytes_in=report.series_in,
        bytes_out=report.series_out,
        total_bytes_in=report.totals.bytes_in,
        total_bytes_out=report.totals.bytes_out,
        users=report.totals.users,
    )


# --------------------------------------------------------------------------- #
# API keys (self service)
# --------------------------------------------------------------------------- #
def _key_out(key) -> ApiKeyOut:  # noqa: ANN001
    return ApiKeyOut(
        id=key.id,
        name=key.name,
        prefix=key.prefix,
        is_active=key.is_active,
        scopes=sorted(key.scope_set()),
        expires_at=key.expires_at,
    )


def _created_out(key, raw: str) -> ApiKeyCreated:  # noqa: ANN001
    return ApiKeyCreated(
        id=key.id,
        name=key.name,
        prefix=key.prefix,
        is_active=key.is_active,
        scopes=sorted(key.scope_set()),
        expires_at=key.expires_at,
        key=raw,
    )


@router.get("/keys", response_model=list[ApiKeyOut], tags=["keys"])
def api_list_keys(db: DbSession, user: ApiUser) -> list[ApiKeyOut]:
    return [_key_out(k) for k in api_keys_service.list_keys(db, user.id)]


@router.post("/keys", response_model=ApiKeyCreated, status_code=201, tags=["keys"])
def api_create_key(
    db: DbSession,
    user: ApiUser,
    payload: ApiKeyCreate | None = None,
    name: str | None = None,
) -> ApiKeyCreated:
    """Create a key. ``name`` may be given as a query param or in the JSON body."""

    payload = payload or ApiKeyCreate()
    try:
        key, raw = api_keys_service.create_key(
            db,
            user,
            name=name if name is not None else payload.name,
            scopes=payload.scopes,
            expires_at=payload.expires_at,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return _created_out(key, raw)


@router.post("/keys/{key_id}/revoke", response_model=Message, tags=["keys"])
def api_revoke_key(key_id: int, db: DbSession, user: ApiUser) -> Message:
    if not api_keys_service.revoke_key(db, user.id, key_id):
        raise HTTPException(status_code=404, detail="key not found")
    db.commit()
    return Message(detail="revoked")


@router.post("/keys/{key_id}/rotate", response_model=ApiKeyCreated, tags=["keys"])
def api_rotate_key(key_id: int, db: DbSession, user: ApiUser) -> ApiKeyCreated:
    rotated = api_keys_service.rotate_key(db, user.id, key_id)
    if rotated is None:
        raise HTTPException(status_code=404, detail="key not found")
    key, raw = rotated
    db.commit()
    return _created_out(key, raw)


@router.get("/stats", response_model=StatsOut, tags=["stats"])
def api_stats(request: Request, db: DbSession, user: ApiUser) -> StatsOut:
    """Aggregate dashboard statistics (scoped to the caller)."""

    require_scope(request, "reports:read")
    from app.services import dashboard as dashboard_service

    data = dashboard_service.build(db, owner_id=_scope_owner(user))
    return StatsOut(
        total_users=data.total_users,
        online_users=data.online_users,
        total_nodes=data.total_nodes,
        total_bytes=data.total_bytes,
        cpu_percent=data.metrics.cpu_percent,
        mem_total=data.metrics.mem_total,
        mem_used=data.metrics.mem_used,
        disk_total=data.metrics.disk_total,
        disk_used=data.metrics.disk_used,
        net_rx=data.metrics.net_rx,
        net_tx=data.metrics.net_tx,
        top_users=[
            TopUserOut(username=u.username, used_bytes=u.used_bytes, is_online=u.is_online)
            for u in data.top_users
        ],
    )


@router.get("/version", response_model=VersionInfo, tags=["meta"])
def api_version(request: Request, db: DbSession, user: ApiUser) -> VersionInfo:
    """Return the panel version and, only if enabled, an opt-in update status.

    The update check is OFF by default (``update.enabled`` setting) and sends no
    telemetry — it merely reads the project's public releases when the operator
    turns it on.
    """

    from app import __version__
    from app.services import settings as settings_service
    from app.services import update_check

    enabled = settings_service.get(db, "update.enabled", "false") == "true"
    repo = settings_service.get(db, "update.repo", "") or ""
    status = update_check.check_for_update(enabled=enabled, repo=repo)
    return VersionInfo(
        version=__version__,
        update_check_enabled=enabled,
        latest=status.latest,
        update_available=status.update_available,
        url=status.url,
        error=status.error,
    )
