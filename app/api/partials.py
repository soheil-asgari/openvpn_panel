"""HTMX partial endpoints returning HTML fragments."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.api.web import _context
from app.core.units import format_size
from app.dependencies import AdminUser, CurrentUser, DbSession
from app.models.node import Node
from app.models.user import Role
from app.models.vpn_user import VpnUserStatus
from app.services import api_keys as api_keys_service
from app.services import config_builder, helper_client
from app.services import subscriptions as subscriptions_service
from app.services import users as users_service
from app.services import vpn_sessions as vpn_sessions_service
from app.services import vpn_users as vpn_users_service

router = APIRouter(prefix="/partials")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
templates.env.filters["size"] = format_size


def _render(request: Request, template: str, user, **extra: object) -> HTMLResponse:  # noqa: ANN001
    return templates.TemplateResponse(request, template, _context(request, user, **extra))


def _scoped(db, user, vpn_user_id: int):  # noqa: ANN001
    vpn_user = vpn_users_service.get(db, vpn_user_id)
    if vpn_user is None:
        raise HTTPException(status_code=404, detail="not found")
    if user.role != Role.ADMIN and vpn_user.owner_id != user.id:
        raise HTTPException(status_code=404, detail="not found")
    return vpn_user


@router.put("/nodes/{node_id}/config", response_class=HTMLResponse)
def save_node_config(
    request: Request,
    node_id: int,
    db: DbSession,
    user: AdminUser,
    content: Annotated[str, Form()] = "",
) -> HTMLResponse:
    from app.services import audit as audit_service
    from app.services import node_client

    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="not found")
    node.config = content
    if node.agent_url:
        try:
            result = node_client.NodeAgentClient(node).write_config(content)
        except node_client.NodeClientError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.error or "agent rejected config")
    audit_service.record(
        db,
        action="node.config_update",
        actor=user,
        target_type="node",
        target_id=node.id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return HTMLResponse("")


def _split_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for chunk in (raw or "").replace(",", " ").split():
        if chunk.isdigit():
            ids.append(int(chunk))
    return ids


def _owner_id(user) -> int | None:  # noqa: ANN001
    return None if user.role == Role.ADMIN else user.id


def _push_user(db, vpn_user) -> None:  # noqa: ANN001
    """Best-effort user sync to the assigned node (queued on failure)."""

    from app.services import node_sync

    try:
        node_sync.push_user(db, vpn_user)
        db.commit()
    except Exception:  # noqa: BLE001 - never fail the request on a sync error
        db.rollback()


def _audit(request: Request, db, user, action: str, **kwargs) -> None:  # noqa: ANN001
    from app.services import audit as audit_service

    audit_service.record(
        db,
        action=action,
        actor=user,
        ip_address=request.client.host if request.client else None,
        **kwargs,
    )


@router.get("/users/new", response_class=HTMLResponse)
def new_user_form(request: Request, user: CurrentUser, db: DbSession) -> HTMLResponse:
    nodes = list(db.scalars(select(Node).order_by(Node.name)))
    return _render(request, "partials/user_form.html", user, nodes=nodes)


@router.get("/users/bulk/new", response_class=HTMLResponse)
def new_bulk_form(request: Request, user: CurrentUser, db: DbSession) -> HTMLResponse:
    nodes = list(db.scalars(select(Node).order_by(Node.name)))
    return _render(request, "partials/bulk_form.html", user, nodes=nodes)


@router.post("/users", response_class=HTMLResponse)
def create_user_row(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    node_id: Annotated[int, Form()] = 0,
    data_limit_mb: Annotated[int, Form()] = 0,
    expire_days: Annotated[int, Form()] = 0,
    max_connections: Annotated[int, Form()] = 0,
    speed_limit_kbps: Annotated[int, Form()] = 0,
) -> HTMLResponse:
    try:
        vpn_user = vpn_users_service.create_vpn_user(
            db,
            username=username,
            password=password,
            owner=user,
            node_id=node_id or None,
            data_limit_bytes=(data_limit_mb * 1024 * 1024) if data_limit_mb > 0 else None,
            expire_days=expire_days or None,
            max_connections=max_connections or None,
            speed_limit_kbps=speed_limit_kbps or None,
        )
        _audit(
            request,
            db,
            user,
            "user.create",
            target_type="vpn_user",
            target_id=vpn_user.id,
            detail=vpn_user.username,
        )
        db.commit()
    except vpn_users_service.VpnUserError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _push_user(db, vpn_user)
    return _render(request, "partials/user_row.html", user, u=vpn_user)


@router.post("/users/bulk", response_class=HTMLResponse)
def bulk_create_rows(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    prefix: Annotated[str, Form()],
    count: Annotated[int, Form()],
    node_id: Annotated[int, Form()] = 0,
    data_limit_mb: Annotated[int, Form()] = 0,
    expire_days: Annotated[int, Form()] = 0,
    max_connections: Annotated[int, Form()] = 0,
    speed_limit_kbps: Annotated[int, Form()] = 0,
) -> HTMLResponse:
    try:
        created = vpn_users_service.bulk_create(
            db,
            prefix=prefix,
            count=count,
            owner=user,
            node_id=node_id or None,
            data_limit_bytes=(data_limit_mb * 1024 * 1024) if data_limit_mb > 0 else None,
            expire_days=expire_days or None,
            max_connections=max_connections or None,
            speed_limit_kbps=speed_limit_kbps or None,
        )
        db.commit()
    except vpn_users_service.VpnUserError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    credentials = [{"username": c.user.username, "password": c.password} for c in created]
    return _render(request, "partials/bulk_credentials.html", user, credentials=credentials)


@router.post("/users/bulk/action", response_class=HTMLResponse)
def bulk_action(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    action: Annotated[str, Form()],
    ids: Annotated[list[str] | None, Form()] = None,
    days: Annotated[int, Form()] = 0,
) -> HTMLResponse:
    user_ids = _split_ids(" ".join(ids or []))
    owner_id = _owner_id(user)
    affected: list = []
    try:
        if action == "delete":
            vpn_users_service.bulk_delete(db, user_ids, owner_id=owner_id)
        elif action == "enable":
            affected = vpn_users_service.bulk_set_status(
                db, user_ids, VpnUserStatus.ACTIVE, owner_id=owner_id
            )
        elif action == "disable":
            affected = vpn_users_service.bulk_set_status(
                db, user_ids, VpnUserStatus.DISABLED, owner_id=owner_id
            )
            for row in affected:
                vpn_sessions_service.terminate_active_sessions(db, row)
        elif action == "extend":
            affected = vpn_users_service.bulk_extend(db, user_ids, days=days, owner_id=owner_id)
        elif action == "reset-traffic":
            vpn_users_service.bulk_reset_traffic(db, user_ids, owner_id=owner_id)
        _audit(
            request,
            db,
            user,
            f"user.bulk_{action}",
            target_type="vpn_user",
            detail=",".join(str(i) for i in user_ids),
        )
        db.commit()
    except vpn_users_service.VpnUserError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _render(
        request, "partials/users_table.html", user, active="users", **_relist(request, db, user)
    )


def _relist(request: Request, db, user) -> dict:  # noqa: ANN001
    owner_id = _owner_id(user)
    search = request.query_params.get("search") or None
    status = request.query_params.get("status") or None
    sort = request.query_params.get("sort", "username")
    direction = request.query_params.get("dir", "asc")
    page = max(1, int(request.query_params.get("page", 1) or 1))
    total = vpn_users_service.count_vpn_users(db, owner_id=owner_id, search=search, status=status)
    users = vpn_users_service.list_vpn_users(
        db,
        owner_id=owner_id,
        search=search,
        status=status,
        sort_by=sort,
        sort_dir=direction,
        limit=50,
        offset=(page - 1) * 50,
    )
    pages = max(1, (total + 49) // 50)
    return {
        "users": users,
        "nodes": list(db.scalars(select(Node).order_by(Node.name))),
        "search": search or "",
        "status": status or "",
        "sort": sort,
        "dir": direction,
        "page": page,
        "pages": pages,
        "total": total,
        "page_size": 50,
    }


@router.post("/users/{vpn_user_id}/enable", response_class=HTMLResponse)
def enable_user(
    request: Request, vpn_user_id: int, db: DbSession, user: CurrentUser
) -> HTMLResponse:
    vpn_user = _scoped(db, user, vpn_user_id)
    vpn_users_service.set_status(db, vpn_user, VpnUserStatus.ACTIVE)
    _audit(request, db, user, "user.enable", target_type="vpn_user", target_id=vpn_user.id)
    db.commit()
    _push_user(db, vpn_user)
    return _render(request, "partials/user_row.html", user, u=vpn_user)


@router.post("/users/{vpn_user_id}/disable", response_class=HTMLResponse)
def disable_user(
    request: Request, vpn_user_id: int, db: DbSession, user: CurrentUser
) -> HTMLResponse:
    vpn_user = _scoped(db, user, vpn_user_id)
    vpn_users_service.set_status(db, vpn_user, VpnUserStatus.DISABLED)
    vpn_sessions_service.terminate_active_sessions(db, vpn_user)
    _audit(request, db, user, "user.disable", target_type="vpn_user", target_id=vpn_user.id)
    db.commit()
    _push_user(db, vpn_user)
    return _render(request, "partials/user_row.html", user, u=vpn_user)


@router.post("/users/{vpn_user_id}/kick", response_class=HTMLResponse)
def kick_user(request: Request, vpn_user_id: int, db: DbSession, user: CurrentUser) -> HTMLResponse:
    vpn_user = _scoped(db, user, vpn_user_id)
    vpn_sessions_service.terminate_active_sessions(db, vpn_user)
    db.commit()
    return _render(request, "partials/user_row.html", user, u=vpn_user)


@router.post("/online/{vpn_user_id}/kick", response_class=HTMLResponse)
def kick_online_user(
    request: Request, vpn_user_id: int, db: DbSession, user: CurrentUser
) -> HTMLResponse:
    vpn_user = _scoped(db, user, vpn_user_id)
    vpn_sessions_service.terminate_active_sessions(db, vpn_user)
    db.commit()
    return HTMLResponse("")


@router.post("/users/{vpn_user_id}/reset-password", response_class=HTMLResponse)
def reset_password(
    request: Request, vpn_user_id: int, db: DbSession, user: CurrentUser
) -> HTMLResponse:
    vpn_user = _scoped(db, user, vpn_user_id)
    password = vpn_users_service.reset_password(db, vpn_user)
    _audit(request, db, user, "user.reset_password", target_type="vpn_user", target_id=vpn_user.id)
    db.commit()
    return _render(
        request,
        "partials/credentials.html",
        user,
        username=vpn_user.username,
        password=password,
    )


@router.post("/users/{vpn_user_id}/reset-traffic", response_class=HTMLResponse)
def reset_traffic(
    request: Request, vpn_user_id: int, db: DbSession, user: CurrentUser
) -> HTMLResponse:
    vpn_user = _scoped(db, user, vpn_user_id)
    vpn_users_service.reset_usage(db, vpn_user)
    db.commit()
    return _render(request, "partials/user_row.html", user, u=vpn_user)


@router.delete("/users/{vpn_user_id}", response_class=HTMLResponse)
def delete_user(
    request: Request, vpn_user_id: int, db: DbSession, user: CurrentUser
) -> HTMLResponse:
    vpn_user = _scoped(db, user, vpn_user_id)
    name = vpn_user.username
    vpn_users_service.delete_vpn_user(db, vpn_user)
    _audit(
        request,
        db,
        user,
        "user.delete",
        target_type="vpn_user",
        target_id=vpn_user_id,
        detail=name,
    )
    db.commit()
    return HTMLResponse("")


@router.get("/users/{vpn_user_id}/config", response_class=HTMLResponse)
def user_config(
    request: Request, vpn_user_id: int, db: DbSession, user: CurrentUser
) -> HTMLResponse:
    vpn_user = _scoped(db, user, vpn_user_id)
    node = db.get(Node, vpn_user.node_id) if vpn_user.node_id else db.scalar(select(Node))
    host = node.host if node else "vpn.example.com"
    port = node.port if node else 1194
    protocol = node.protocol if node else "udp"
    cipher = node.cipher if node else "AES-256-GCM"
    config_text = config_builder.build_client_config(
        host=host, port=port, protocol=protocol, cipher=cipher
    )
    try:
        result = helper_client.generate_client_config(
            common_name=vpn_user.common_name or vpn_user.username
        )
        if result.ok and result.data and result.data.get("config"):
            config_text = str(result.data["config"])
    except helper_client.HelperError:
        pass
    return _render(
        request, "partials/config.html", user, vpn_user=vpn_user, config_text=config_text
    )


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _subscription_context(request: Request, db, vpn_user) -> dict:  # noqa: ANN001
    from app.services import tunnels as tunnels_service

    subscriptions_service.ensure_token(db, vpn_user)
    hidden = subscriptions_service.hidden_node_ids(db, vpn_user.id)
    tunnel_by_node = {
        node.id: tunnels_service.active_tunnel_for_node(db, node.id)
        for node in db.scalars(select(Node))
    }
    return {
        "vpn_user": vpn_user,
        "sub_url": f"{_base_url(request)}/sub/{vpn_user.subscription_token}",
        "nodes": list(db.scalars(select(Node).order_by(Node.name))),
        "hidden_node_ids": hidden,
        "tunnel_by_node": tunnel_by_node,
    }


@router.get("/users/{vpn_user_id}/subscription", response_class=HTMLResponse)
def user_subscription(
    request: Request, vpn_user_id: int, db: DbSession, user: CurrentUser
) -> HTMLResponse:
    vpn_user = _scoped(db, user, vpn_user_id)
    return _render(
        request, "partials/subscription.html", user, **_subscription_context(request, db, vpn_user)
    )


@router.post("/users/{vpn_user_id}/subscription/token", response_class=HTMLResponse)
def regenerate_subscription_token(
    request: Request, vpn_user_id: int, db: DbSession, user: CurrentUser
) -> HTMLResponse:
    vpn_user = _scoped(db, user, vpn_user_id)
    subscriptions_service.regenerate_token(db, vpn_user)
    db.commit()
    return _render(
        request, "partials/subscription.html", user, **_subscription_context(request, db, vpn_user)
    )


@router.post("/users/{vpn_user_id}/subscription/visibility", response_class=HTMLResponse)
def save_subscription_visibility(
    request: Request,
    vpn_user_id: int,
    db: DbSession,
    user: CurrentUser,
    node_id: Annotated[list[int] | None, Form()] = None,
    use_tunnel: Annotated[str, Form()] = "",
) -> HTMLResponse:
    vpn_user = _scoped(db, user, vpn_user_id)
    visible = set(node_id or [])
    all_nodes = {n.id for n in db.scalars(select(Node))}
    subscriptions_service.set_hidden_nodes(db, vpn_user, all_nodes - visible)
    vpn_user.use_tunnel = use_tunnel == "on"
    db.commit()
    return _render(
        request, "partials/subscription.html", user, **_subscription_context(request, db, vpn_user)
    )


@router.post("/keys/new", response_class=HTMLResponse)
def new_key(request: Request, user: CurrentUser, db: DbSession) -> HTMLResponse:
    key, raw = api_keys_service.create_key(db, user, name="panel")
    db.commit()
    return _render(
        request,
        "partials/api_key.html",
        user,
        key=key,
        raw_key=raw,
    )


# --------------------------------------------------------------------------- #
# Per-user protocols (Phase 12-15)
# --------------------------------------------------------------------------- #
def _protocol_context(db, vpn_user) -> dict:  # noqa: ANN001
    from app.services import protocols_service

    instances = protocols_service.list_instances(db)
    rows_by_id = {
        row.protocol_instance_id: row for row in protocols_service.protocol_users(db, vpn_user.id)
    }
    rows = []
    for instance in instances:
        row = rows_by_id.get(instance.id)
        rows.append(
            {
                "instance": instance,
                "enabled": bool(row and row.enabled),
                "usage": row.used_bytes if row else 0,
            }
        )
    node = db.get(Node, vpn_user.node_id) if vpn_user.node_id else None
    configs = protocols_service.configs_for_user(db, vpn_user, node)
    return {"vpn_user": vpn_user, "rows": rows, "links": configs}


@router.get("/users/{vpn_user_id}/protocols", response_class=HTMLResponse)
def user_protocols(
    request: Request, vpn_user_id: int, db: DbSession, user: CurrentUser
) -> HTMLResponse:
    vpn_user = _scoped(db, user, vpn_user_id)
    return _render(request, "partials/protocols.html", user, **_protocol_context(db, vpn_user))


@router.post("/users/{vpn_user_id}/protocols", response_class=HTMLResponse)
def save_user_protocols(
    request: Request,
    vpn_user_id: int,
    db: DbSession,
    user: CurrentUser,
    instance_ids: Annotated[list[int] | None, Form()] = None,
) -> HTMLResponse:
    from app.services import protocols_service

    vpn_user = _scoped(db, user, vpn_user_id)
    selected = set(instance_ids or [])
    node = db.get(Node, vpn_user.node_id) if vpn_user.node_id else None
    for instance in protocols_service.list_instances(db):
        protocols_service.set_enabled(
            db,
            vpn_user=vpn_user,
            instance=instance,
            enabled=instance.id in selected,
            node=node,
        )
    _audit(
        request,
        db,
        user,
        "protocol.user_update",
        target_type="vpn_user",
        target_id=vpn_user.id,
        detail=",".join(str(i) for i in sorted(selected)),
    )
    db.commit()
    return _render(request, "partials/protocols.html", user, **_protocol_context(db, vpn_user))


# --------------------------------------------------------------------------- #
# Resellers (sub-admins)
# --------------------------------------------------------------------------- #
def _reseller_form_context(request: Request, db, reseller=None) -> dict:  # noqa: ANN001
    from app.services import resellers as resellers_service

    nodes = list(db.scalars(select(Node).where(Node.is_active.is_(True)).order_by(Node.name)))
    allowed = resellers_service.allowed_node_ids(db, reseller) if reseller else set()
    return {
        "reseller": reseller,
        "nodes": nodes,
        "allowed_node_ids": allowed,
        "unrestricted": bool(reseller) and not reseller.restrict_nodes,
    }


@router.get("/resellers/new", response_class=HTMLResponse)
def new_reseller_form(request: Request, user: AdminUser, db: DbSession) -> HTMLResponse:
    return _render(
        request, "partials/reseller_form.html", user, **_reseller_form_context(request, db)
    )


@router.get("/resellers/{reseller_id}/edit", response_class=HTMLResponse)
def edit_reseller_form(
    request: Request, reseller_id: int, user: AdminUser, db: DbSession
) -> HTMLResponse:
    from app.services import resellers as resellers_service

    reseller = resellers_service.get(db, reseller_id)
    if reseller is None:
        raise HTTPException(status_code=404, detail="not found")
    return _render(
        request,
        "partials/reseller_form.html",
        user,
        **_reseller_form_context(request, db, reseller),
    )


@router.post("/resellers", response_class=HTMLResponse)
def create_reseller_row(
    request: Request,
    db: DbSession,
    user: AdminUser,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    email: Annotated[str, Form()] = "",
    quota_users: Annotated[str, Form()] = "",
    max_data: Annotated[str, Form()] = "",
    expires_at: Annotated[str, Form()] = "",
    node_id: Annotated[list[int] | None, Form()] = None,
) -> HTMLResponse:
    from app.core.units import parse_size
    from app.services import audit as audit_service
    from app.services import resellers as resellers_service

    try:
        reseller = resellers_service.create_reseller(
            db,
            username=username,
            password=password,
            email=email or None,
            quota_users=int(quota_users) if quota_users else None,
            max_data_bytes=parse_size(max_data) if max_data else None,
            expires_at=_parse_date(expires_at),
            node_ids=set(node_id or []),
        )
        audit_service.record(
            db,
            action="reseller.create",
            actor=user,
            target_type="user",
            target_id=reseller.id,
            detail=reseller.username,
            ip_address=request.client.host if request.client else None,
        )
        db.commit()
    except (users_service.UserError, resellers_service.ResellerError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _relist_resellers(request, db, user)


@router.post("/resellers/{reseller_id}", response_class=HTMLResponse)
def update_reseller_row(
    request: Request,
    reseller_id: int,
    db: DbSession,
    user: AdminUser,
    password: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    quota_users: Annotated[str, Form()] = "",
    max_data: Annotated[str, Form()] = "",
    expires_at: Annotated[str, Form()] = "",
    is_active: Annotated[str, Form()] = "on",
    node_id: Annotated[list[int] | None, Form()] = None,
) -> HTMLResponse:
    from app.core.units import parse_size
    from app.services import audit as audit_service
    from app.services import resellers as resellers_service

    reseller = resellers_service.get(db, reseller_id)
    if reseller is None:
        raise HTTPException(status_code=404, detail="not found")
    try:
        resellers_service.update_reseller(
            db,
            reseller,
            password=password or None,
            email=email,
            quota_users=int(quota_users) if quota_users else None,
            max_data_bytes=parse_size(max_data) if max_data else None,
            expires_at=_parse_date(expires_at) if expires_at else None,
            is_active=is_active == "on",
            node_ids=set(node_id or []),
        )
        audit_service.record(
            db,
            action="reseller.update",
            actor=user,
            target_type="user",
            target_id=reseller.id,
            detail=reseller.username,
            ip_address=request.client.host if request.client else None,
        )
        db.commit()
    except (users_service.UserError, resellers_service.ResellerError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _relist_resellers(request, db, user)


@router.delete("/resellers/{reseller_id}", response_class=HTMLResponse)
def delete_reseller_row(
    request: Request, reseller_id: int, db: DbSession, user: AdminUser
) -> HTMLResponse:
    from app.services import audit as audit_service
    from app.services import resellers as resellers_service

    reseller = resellers_service.get(db, reseller_id)
    if reseller is None:
        raise HTTPException(status_code=404, detail="not found")
    name = reseller.username
    resellers_service.delete_reseller(db, reseller)
    audit_service.record(
        db,
        action="reseller.delete",
        actor=user,
        target_type="user",
        target_id=reseller_id,
        detail=name,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return HTMLResponse("")


@router.get("/resellers/{reseller_id}/usage", response_class=HTMLResponse)
def reseller_usage_partial(
    request: Request, reseller_id: int, user: AdminUser, db: DbSession, preset: str = "monthly"
) -> HTMLResponse:
    from app.services import reports as reports_service
    from app.services import resellers as resellers_service

    reseller = resellers_service.get(db, reseller_id)
    if reseller is None:
        raise HTTPException(status_code=404, detail="not found")
    report = reports_service.build_report(
        db, owner_id=reseller.id, preset=preset, owner_label=reseller.username
    )
    return _render(
        request,
        "partials/reseller_usage.html",
        user,
        reseller=reseller,
        report=report,
        usage=resellers_service.usage(db, reseller),
    )


def _relist_resellers(request: Request, db, user) -> HTMLResponse:  # noqa: ANN001
    from app.services import resellers as resellers_service

    rows = [
        {
            "user": reseller,
            "usage": resellers_service.usage(db, reseller),
            "nodes": resellers_service.allowed_nodes(db, reseller),
            "expired": resellers_service.is_expired(reseller),
        }
        for reseller in resellers_service.list_resellers(db)
    ]
    nodes = list(db.scalars(select(Node).where(Node.is_active.is_(True)).order_by(Node.name)))
    return _render(
        request,
        "partials/resellers_table.html",
        user,
        active="resellers",
        rows=rows,
        nodes=nodes,
    )


def _parse_date(value: str):  # noqa: ANN001
    from datetime import datetime, timezone

    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# --------------------------------------------------------------------------- #
# Tunnel Manager (HTMX fragments)
# --------------------------------------------------------------------------- #
def _tunnel_scoped(db, user, tunnel_id):  # noqa: ANN001
    from app.services import tunnels as tunnels_service

    tunnel = tunnels_service.get(db, tunnel_id)
    if tunnel is None:
        raise HTTPException(status_code=404, detail="not found")
    return tunnel


@router.get("/tunnels/{tunnel_id}/job", response_class=HTMLResponse)
def tunnel_job_progress(request: Request, tunnel_id: int, db: DbSession, user: AdminUser):
    """Live progress fragment polled by the wizard/detail page."""

    from app.services import tunnels as tunnels_service

    job = tunnels_service.latest_job(db, tunnel_id)
    return _render(request, "partials/tunnel_job.html", user, tunnel_id=tunnel_id, job=job)


@router.post("/tunnels/{tunnel_id}/{action}", response_class=HTMLResponse)
def tunnel_action(
    request: Request, tunnel_id: int, action: str, db: DbSession, user: AdminUser
) -> HTMLResponse:
    from app.services import audit as audit_service
    from app.services import tunnels as tunnels_service

    if action not in {"start", "stop", "restart", "update", "rollback"}:
        raise HTTPException(status_code=400, detail="invalid action")
    tunnel = _tunnel_scoped(db, user, tunnel_id)
    if action == "rollback":
        tunnels_service.rollback(db, tunnel)
    else:
        tunnels_service.operate(db, tunnel, action)
    audit_service.record(
        db,
        action=f"tunnel.{action}",
        actor=user,
        target_type="tunnel",
        target_id=tunnel.id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    job = tunnels_service.latest_job(db, tunnel.id)
    return _render(request, "partials/tunnel_job.html", user, tunnel_id=tunnel_id, job=job)


@router.post("/tunnels/{tunnel_id}/delete", response_class=HTMLResponse)
def tunnel_delete(request: Request, tunnel_id: int, db: DbSession, user: AdminUser) -> HTMLResponse:
    from sqlalchemy import select

    from app.services import audit as audit_service
    from app.services import tunnels as tunnels_service

    tunnel = _tunnel_scoped(db, user, tunnel_id)
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
    rows = [
        {
            "tunnel": t,
            "sample": tunnels_service.latest_sample(db, t.id),
            "job": tunnels_service.latest_job(db, t.id),
        }
        for t in tunnels_service.list_tunnels(db)
    ]
    _ = select  # keep import parity with other fragments
    return _render(request, "partials/tunnels_table.html", user, active="tunnels", rows=rows)


@router.post("/tunnels/fingerprint-probe", response_class=HTMLResponse)
def tunnel_fingerprint_probe(
    request: Request,
    user: AdminUser,
    host: Annotated[str, Form()] = "",
    port: Annotated[int, Form()] = 22,
) -> HTMLResponse:
    """Wizard helper: read an entry host key without sending credentials."""

    from app.services import tunnel_ssh

    fingerprint = ""
    error = ""
    if host.strip():
        try:
            fingerprint = tunnel_ssh.probe_fingerprint(host.strip(), port=port or 22)
        except tunnel_ssh.SSHError as exc:
            error = str(exc)
    badge = "" if fingerprint else "off"
    html = (
        f'<input type="text" readonly value="{fingerprint}" id="fp-probe" class="mono">'
        if fingerprint
        else f'<span class="badge badge-{badge}">{error or "—"}</span>'
    )
    return HTMLResponse(html)


@router.post("/tunnels/benchmark", response_class=HTMLResponse)
def tunnel_benchmark_probe(
    request: Request,
    db: DbSession,
    user: AdminUser,
    host: Annotated[str, Form()] = "",
    candidates: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Wizard helper: benchmark candidate backends/transports for a host."""

    from app.services import audit as audit_service
    from app.services import tunnel_benchmark

    if not host.strip():
        return HTMLResponse('<span class="muted">—</span>')
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
    report = tunnel_benchmark.benchmark_candidates(host.strip(), pairs)
    audit_service.record(
        db,
        action="tunnel.benchmark",
        actor=user,
        target_type="host",
        detail=host.strip(),
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    if not report.recommended:
        return HTMLResponse(f'<span class="muted">{report.reason}</span>')
    recommended = report.recommended
    return HTMLResponse(
        f'<span class="badge badge-ok">{recommended.backend}:{recommended.transport}</span> '
        f'<span class="muted">{report.reason}</span>'
    )


@router.post("/tunnels/{tunnel_id}/retry", response_class=HTMLResponse)
def tunnel_retry(request: Request, tunnel_id: int, db: DbSession, user: AdminUser) -> HTMLResponse:
    from app.services import audit as audit_service
    from app.services import tunnel_worker
    from app.services import tunnels as tunnels_service

    tunnel = _tunnel_scoped(db, user, tunnel_id)
    audit_service.record(
        db,
        action="tunnel.retry",
        actor=user,
        target_type="tunnel",
        target_id=tunnel.id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    tunnel_worker.submit_provision(tunnel.id, {})
    job = tunnels_service.latest_job(db, tunnel.id)
    return _render(request, "partials/tunnel_job.html", user, tunnel_id=tunnel_id, job=job)


@router.post("/tunnels/{tunnel_id}/fingerprint", response_class=HTMLResponse)
def tunnel_fingerprint(
    request: Request, tunnel_id: int, db: DbSession, user: AdminUser
) -> HTMLResponse:
    from app.services import tunnel_ssh

    tunnel = _tunnel_scoped(db, user, tunnel_id)
    fingerprint = tunnel.host_key_fingerprint or ""
    error = ""
    if not fingerprint:
        try:
            fingerprint = tunnel_ssh.probe_fingerprint(
                tunnel.entry_host, port=tunnel.entry_ssh_port
            )
        except tunnel_ssh.SSHError as exc:
            error = str(exc)
    return _render(
        request,
        "partials/tunnel_fingerprint.html",
        user,
        tunnel=tunnel,
        fingerprint=fingerprint,
        error=error,
    )


@router.post("/tunnels/{tunnel_id}/confirm-fingerprint", response_class=HTMLResponse)
def tunnel_confirm_fingerprint(
    request: Request,
    tunnel_id: int,
    db: DbSession,
    user: AdminUser,
    fingerprint: Annotated[str, Form()] = "",
) -> HTMLResponse:
    from app.services import audit as audit_service

    tunnel = _tunnel_scoped(db, user, tunnel_id)
    if fingerprint.strip():
        tunnel.host_key_fingerprint = fingerprint.strip()
        audit_service.record(
            db,
            action="tunnel.fingerprint",
            actor=user,
            target_type="tunnel",
            target_id=tunnel.id,
            detail=fingerprint.strip(),
            ip_address=request.client.host if request.client else None,
        )
        db.commit()
    return _render(
        request,
        "partials/tunnel_fingerprint.html",
        user,
        tunnel=tunnel,
        fingerprint=tunnel.host_key_fingerprint or "",
        error="",
    )


@router.post("/tunnels/{tunnel_id}/mtu", response_class=HTMLResponse)
def tunnel_mtu(
    request: Request,
    tunnel_id: int,
    db: DbSession,
    user: AdminUser,
    apply: Annotated[str, Form()] = "",
) -> HTMLResponse:
    from app.services import audit as audit_service
    from app.services import mtu_finder, tunnel_ssh
    from app.services import tunnels as tunnels_service

    tunnel = _tunnel_scoped(db, user, tunnel_id)
    try:
        ssh = tunnels_service.connect(tunnel)
        try:
            result = mtu_finder.find_mtu(tunnel.entry_host, ssh=ssh)
        finally:
            ssh.close()
    except tunnel_ssh.SSHError:
        result = mtu_finder.find_mtu(tunnel.entry_host)
    if result.ok and apply == "on" and result.mtu:
        tunnel.mtu = result.mtu
        tunnel.mssfix = result.mssfix
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
    return _render(request, "partials/tunnel_mtu.html", user, tunnel=tunnel, result=result)


@router.get("/tunnels/{tunnel_id}/logs", response_class=HTMLResponse)
def tunnel_logs(request: Request, tunnel_id: int, db: DbSession, user: AdminUser) -> HTMLResponse:
    """Live backend journal tail for the tunnel (polled by the dashboard)."""

    from app.services import tunnel_backend, tunnel_ssh
    from app.services import tunnels as tunnels_service

    tunnel = _tunnel_scoped(db, user, tunnel_id)
    log = ""
    error = ""
    try:
        ssh = tunnels_service.connect(tunnel)
        try:
            kind = tunnel.backend.value if tunnel.backend.value != "auto" else "backhaul"
            backend = tunnel_backend.get_backend(kind, ssh)
            log = backend.logs(tunnel.name, lines=200)
        finally:
            ssh.close()
    except tunnel_ssh.SSHError as exc:
        error = str(exc)
    return _render(request, "partials/tunnel_logs.html", user, tunnel=tunnel, log=log, error=error)


@router.post("/tunnels/{tunnel_id}/apply-mtu", response_class=HTMLResponse)
def tunnel_apply_mtu(
    request: Request, tunnel_id: int, db: DbSession, user: AdminUser
) -> HTMLResponse:
    from app.services import audit as audit_service
    from app.services import tunnels as tunnels_service

    tunnel = _tunnel_scoped(db, user, tunnel_id)
    ok, message = tunnels_service.apply_mtu_to_instance(tunnel)
    audit_service.record(
        db,
        action="tunnel.apply_mtu",
        actor=user,
        target_type="tunnel",
        target_id=tunnel.id,
        detail=message,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    badge = "ok" if ok else "off"
    return HTMLResponse(f'<span class="badge badge-{badge}">{message}</span>')
