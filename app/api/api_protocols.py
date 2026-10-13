"""JSON API for the protocol abstraction (Phases 12-15).

Mounted at ``/api/v1``. Admin-only for instance management; per-user protocol
enable/disable and credentials are available to the owning reseller as well.
Every mutating call is written to the audit log.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.api.schemas import (
    Message,
    ProtocolHealthOut,
    ProtocolInstallOut,
    ProtocolInstanceCreate,
    ProtocolInstanceDetail,
    ProtocolInstanceOut,
    ProtocolInstanceUpdate,
    ProtocolUserConfigOut,
    ProtocolUserOut,
    ProtocolUserToggle,
    SubscriptionLinks,
)
from app.dependencies import ApiUser, DbSession, require_scope
from app.models.node import Node
from app.models.user import Role
from app.services import audit as audit_service
from app.services import protocols
from app.services import protocols_service as proto_service
from app.services import vpn_users as vpn_users_service

router = APIRouter(prefix="/protocols", tags=["protocols"])


def _require_admin(user) -> None:  # noqa: ANN001
    if user.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")


def _instance_or_404(db, instance_id: int):  # noqa: ANN001
    instance = proto_service.get(db, instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="protocol instance not found")
    return instance


def _node_for(db, instance):  # noqa: ANN001
    return db.get(Node, instance.node_id) if instance.node_id else None


def _out(instance) -> ProtocolInstanceOut:  # noqa: ANN001
    return ProtocolInstanceOut.model_validate(instance)


def _scoped_user(db, user, vpn_user_id: int):  # noqa: ANN001
    vpn_user = vpn_users_service.get(db, vpn_user_id)
    if vpn_user is None:
        raise HTTPException(status_code=404, detail="user not found")
    if user.role != Role.ADMIN and vpn_user.owner_id != user.id:
        raise HTTPException(status_code=404, detail="user not found")
    return vpn_user


# --------------------------------------------------------------------------- #
# Catalogue + instances
# --------------------------------------------------------------------------- #
@router.get("/backends", tags=["protocols"])
def api_backends(user: ApiUser) -> list[dict]:
    """Protocol backend catalogue (capabilities, upstream, license)."""

    return protocols.backend_metadata()


@router.get("/instances", response_model=list[ProtocolInstanceOut], tags=["protocols"])
def api_list_instances(
    db: DbSession, user: ApiUser, kind: str | None = None, node_id: int | None = None
) -> list[ProtocolInstanceOut]:
    _require_admin(user)
    return [_out(i) for i in proto_service.list_instances(db, kind=kind, node_id=node_id)]


@router.post(
    "/instances", response_model=ProtocolInstanceDetail, status_code=201, tags=["protocols"]
)
def api_create_instance(
    request: Request, payload: ProtocolInstanceCreate, db: DbSession, user: ApiUser
) -> ProtocolInstanceDetail:
    _require_admin(user)
    require_scope(request, "nodes:write")
    try:
        instance = proto_service.create_instance(db, **payload.model_dump())
        db.commit()
    except proto_service.ProtocolServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_service.record(
        db,
        action="protocol.instance_create",
        actor=user,
        target_type="protocol_instance",
        target_id=instance.id,
        detail=f"{instance.kind}:{instance.name}",
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    out = ProtocolInstanceDetail.model_validate(instance)
    out.settings = instance.settings_dict
    return out


@router.get("/instances/{instance_id}", response_model=ProtocolInstanceDetail, tags=["protocols"])
def api_get_instance(instance_id: int, db: DbSession, user: ApiUser) -> ProtocolInstanceDetail:
    _require_admin(user)
    instance = _instance_or_404(db, instance_id)
    out = ProtocolInstanceDetail.model_validate(instance)
    out.settings = instance.settings_dict
    return out


@router.patch("/instances/{instance_id}", response_model=ProtocolInstanceDetail, tags=["protocols"])
def api_update_instance(
    instance_id: int,
    payload: ProtocolInstanceUpdate,
    db: DbSession,
    user: ApiUser,
) -> ProtocolInstanceDetail:
    _require_admin(user)
    instance = _instance_or_404(db, instance_id)
    proto_service.update_instance(db, instance, **payload.model_dump(exclude_unset=True))
    db.commit()
    out = ProtocolInstanceDetail.model_validate(instance)
    out.settings = instance.settings_dict
    return out


@router.delete("/instances/{instance_id}", response_model=Message, tags=["protocols"])
def api_delete_instance(instance_id: int, db: DbSession, user: ApiUser) -> Message:
    _require_admin(user)
    instance = _instance_or_404(db, instance_id)
    proto_service.delete_instance(db, instance)
    db.commit()
    return Message(detail="deleted")


@router.post(
    "/instances/{instance_id}/install", response_model=ProtocolInstallOut, tags=["protocols"]
)
def api_install_instance(
    request: Request, instance_id: int, db: DbSession, user: ApiUser
) -> ProtocolInstallOut:
    _require_admin(user)
    require_scope(request, "nodes:write")
    instance = _instance_or_404(db, instance_id)
    outcome = proto_service.install_instance(db, instance, _node_for(db, instance))
    audit_service.record(
        db,
        action="protocol.install",
        actor=user,
        target_type="protocol_instance",
        target_id=instance.id,
        detail=f"{instance.kind}:{instance.name} ok={outcome.ok}",
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return ProtocolInstallOut(
        instance_id=instance.id,
        ok=outcome.ok,
        version=outcome.version,
        error=outcome.error,
        log=outcome.log or [],
    )


@router.get("/instances/{instance_id}/health", response_model=ProtocolHealthOut, tags=["protocols"])
def api_instance_health(instance_id: int, db: DbSession, user: ApiUser) -> ProtocolHealthOut:
    _require_admin(user)
    instance = _instance_or_404(db, instance_id)
    result = proto_service.healthcheck_instance(db, instance, _node_for(db, instance))
    db.commit()
    return ProtocolHealthOut(
        instance_id=instance.id,
        ok=result.ok,
        detail=result.detail,
        version=result.version,
        status=instance.status.value,
    )


@router.post("/instances/{instance_id}/sync", response_model=Message, tags=["protocols"])
def api_instance_sync(instance_id: int, db: DbSession, user: ApiUser) -> Message:
    _require_admin(user)
    instance = _instance_or_404(db, instance_id)
    node = _node_for(db, instance)
    usage = proto_service.collect_usage(db, instance, node)
    online = proto_service.reconcile_online(db, instance, node)
    db.commit()
    return Message(detail=f"usage={usage} online={online}")


# --------------------------------------------------------------------------- #
# Per-user protocol state
# --------------------------------------------------------------------------- #
@router.get("/users/{vpn_user_id}", response_model=list[ProtocolUserOut], tags=["protocols"])
def api_user_protocols(vpn_user_id: int, db: DbSession, user: ApiUser) -> list[ProtocolUserOut]:
    vpn_user = _scoped_user(db, user, vpn_user_id)
    rows = proto_service.protocol_users(db, vpn_user.id)
    out: list[ProtocolUserOut] = []
    for row in rows:
        item = ProtocolUserOut.model_validate(row)
        item.credentials = row.credentials_dict
        out.append(item)
    return out


@router.put(
    "/users/{vpn_user_id}/{instance_id}", response_model=ProtocolUserOut, tags=["protocols"]
)
def api_toggle_user_protocol(
    request: Request,
    vpn_user_id: int,
    instance_id: int,
    payload: ProtocolUserToggle,
    db: DbSession,
    user: ApiUser,
) -> ProtocolUserOut:
    vpn_user = _scoped_user(db, user, vpn_user_id)
    instance = _instance_or_404(db, instance_id)
    node = _node_for(db, instance)
    try:
        row = proto_service.set_enabled(
            db, vpn_user=vpn_user, instance=instance, enabled=payload.enabled, node=node
        )
        db.commit()
    except proto_service.ProtocolServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_service.record(
        db,
        action="protocol.user_toggle",
        actor=user,
        target_type="vpn_user",
        target_id=vpn_user.id,
        detail=f"{instance.kind}:{instance.name}={payload.enabled}",
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    out = ProtocolUserOut.model_validate(row)
    out.credentials = row.credentials_dict
    return out


@router.delete("/users/{vpn_user_id}/{instance_id}", response_model=Message, tags=["protocols"])
def api_delete_user_protocol(
    vpn_user_id: int, instance_id: int, db: DbSession, user: ApiUser
) -> Message:
    vpn_user = _scoped_user(db, user, vpn_user_id)
    instance = _instance_or_404(db, instance_id)
    proto_service.delete_protocol_user(
        db, vpn_user=vpn_user, instance=instance, node=_node_for(db, instance)
    )
    db.commit()
    return Message(detail="removed")


@router.get(
    "/users/{vpn_user_id}/configs",
    response_model=list[ProtocolUserConfigOut],
    tags=["protocols"],
)
def api_user_configs(vpn_user_id: int, db: DbSession, user: ApiUser) -> list[ProtocolUserConfigOut]:
    vpn_user = _scoped_user(db, user, vpn_user_id)
    node = db.get(Node, vpn_user.node_id) if vpn_user.node_id else None
    configs = proto_service.configs_for_user(db, vpn_user, node)
    return [
        ProtocolUserConfigOut(
            kind=c.kind, text=c.text, uri=c.uri, qr_payload=c.qr_payload, filename=c.filename
        )
        for c in configs
    ]


@router.get("/users/{vpn_user_id}/links", response_model=SubscriptionLinks, tags=["protocols"])
def api_user_links(vpn_user_id: int, db: DbSession, user: ApiUser) -> SubscriptionLinks:
    vpn_user = _scoped_user(db, user, vpn_user_id)
    node = db.get(Node, vpn_user.node_id) if vpn_user.node_id else None
    configs = proto_service.configs_for_user(db, vpn_user, node)
    return SubscriptionLinks(
        vpn_user_id=vpn_user.id,
        links=[c.uri for c in configs if c.uri],
        aggregated=proto_service.aggregated_subscription(configs),
        configs=[
            ProtocolUserConfigOut(
                kind=c.kind,
                text=c.text,
                uri=c.uri,
                qr_payload=c.qr_payload,
                filename=c.filename,
            )
            for c in configs
        ],
    )


@router.post("/users/{vpn_user_id}/kick", response_model=Message, tags=["protocols"])
def api_kick_user_protocols(vpn_user_id: int, db: DbSession, user: ApiUser) -> Message:
    vpn_user = _scoped_user(db, user, vpn_user_id)
    node = db.get(Node, vpn_user.node_id) if vpn_user.node_id else None
    killed = proto_service.kick_user(db, vpn_user, node)
    db.commit()
    return Message(detail=f"killed {killed} protocol session(s)")
