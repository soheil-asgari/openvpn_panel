"""HTML pages (Jinja2 + HTMX)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app import __version__
from app.config import get_settings
from app.core.i18n import translator
from app.core.units import format_size
from app.dependencies import AdminUser, CurrentUser, DbSession
from app.models.node import Node
from app.models.node_task import NodeTask
from app.models.user import Role, User
from app.services import vpn_sessions as vpn_sessions_service
from app.services import vpn_users as vpn_users_service

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["size"] = format_size

PAGE_SIZE = 50


def _context(request: Request, user: User | None = None, **extra: object) -> dict:
    settings = get_settings()
    language = user.language if user else settings.default_language
    theme = user.theme if user else settings.default_theme
    t = translator(language)
    ctx = {
        "request": request,
        "t": t,
        "lang": t.language,
        "is_rtl": t.is_rtl,
        "theme": theme,
        "user": user,
        "base_path": settings.admin_prefix,
        "csrf_token": getattr(getattr(request.state, "session", None), "csrf_token", ""),
    }
    ctx.update(extra)
    # Panel title/logo branding (best effort; a missing table must not break).
    if "branding" not in ctx and "panel_title" not in ctx:
        try:
            from app.db import get_session_factory
            from app.services import settings as settings_service

            db = get_session_factory()()
            try:
                ctx["panel_title"] = settings_service.get(db, "panel.title")
                ctx["branding"] = settings_service.get_branding(db)
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            pass
    return ctx


def render(
    request: Request, template: str, user: User | None = None, **extra: object
) -> HTMLResponse:
    return templates.TemplateResponse(request, template, _context(request, user, **extra))


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: DbSession, user: CurrentUser) -> HTMLResponse:
    from app.services import dashboard as dashboard_service

    owner_id = None if user.role == Role.ADMIN else user.id
    data = dashboard_service.build(db, owner_id=owner_id)
    return render(
        request,
        "dashboard.html",
        user,
        active="dashboard",
        data=data,
        total_users=data.total_users,
        online_users=data.online_users,
        total_nodes=data.total_nodes,
    )


def _owner_id(user: User) -> int | None:
    return None if user.role == Role.ADMIN else user.id


def _valid_repo(value: str) -> bool:
    """Accept only a plain ``owner/repo`` slug for the opt-in update check."""

    if not value or value.count("/") != 1:
        return False
    owner, repo = value.split("/", 1)
    return all(part and all(c.isalnum() or c in "-._" for c in part) for part in (owner, repo))


@router.get("/users", response_class=HTMLResponse)
def users_page(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    search: str = "",
    status: str = "",
    sort: str = "username",
    dir: str = "asc",  # noqa: A002 - query parameter name matches the UI
    page: int = 1,
) -> HTMLResponse:
    page = max(1, page)
    owner_id = _owner_id(user)
    total = vpn_users_service.count_vpn_users(
        db, owner_id=owner_id, search=search or None, status=status or None
    )
    users = vpn_users_service.list_vpn_users(
        db,
        owner_id=owner_id,
        search=search or None,
        status=status or None,
        sort_by=sort,
        sort_dir=dir,
        limit=PAGE_SIZE,
        offset=(page - 1) * PAGE_SIZE,
    )
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return render(
        request,
        "users.html",
        user,
        active="users",
        users=users,
        nodes=list(db.scalars(select(Node).order_by(Node.name))),
        search=search,
        status=status,
        sort=sort,
        dir=dir,
        page=page,
        pages=pages,
        total=total,
        page_size=PAGE_SIZE,
    )


@router.get("/users/export.csv")
def export_users_csv(
    db: DbSession,
    user: CurrentUser,
    search: str = "",
    status: str = "",
) -> Response:
    spec = vpn_users_service.FilterSpec(
        owner_id=_owner_id(user),
        search=search or None,
        status=status or None,
        limit=100000,
    )
    csv_text = vpn_users_service.export_csv(db, spec=spec)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=vpn_users.csv"},
    )


@router.get("/online", response_class=HTMLResponse)
def online_users_page(request: Request, db: DbSession, user: CurrentUser) -> HTMLResponse:
    users = vpn_sessions_service.list_online_users(db, owner_id=_owner_id(user))
    return render(request, "online.html", user, active="online", users=users)


@router.get("/nodes", response_class=HTMLResponse)
def nodes_page(request: Request, db: DbSession, user: CurrentUser) -> HTMLResponse:
    from app.services import node_sync

    nodes = list(db.scalars(select(Node).order_by(Node.name)))
    rows = [
        {
            "node": node,
            "metric": node_sync.latest_metric(db, node.id),
            "pending": len(node_sync.pending_tasks(db, node_id=node.id, limit=1000)),
        }
        for node in nodes
    ]
    return render(request, "nodes.html", user, active="nodes", nodes=nodes, rows=rows)


@router.get("/nodes/agent/install.sh")
def node_agent_install_script(user: AdminUser) -> Response:
    from app.services.node_install import build_install_script

    return Response(
        content=build_install_script(),
        media_type="text/x-shellscript",
        headers={"Content-Disposition": 'attachment; filename="ovpn-agent-install.sh"'},
    )


@router.get("/nodes/{node_id}", response_class=HTMLResponse)
def node_detail_page(
    request: Request, node_id: int, db: DbSession, user: AdminUser
) -> HTMLResponse:
    from app.services import node_client, node_install, node_sync

    node = db.get(Node, node_id)
    if node is None:
        return render(request, "error.html", user, code=404, detail="node not found")
    metric = node_sync.latest_metric(db, node.id)
    tasks = list(
        db.scalars(
            select(NodeTask)
            .where(NodeTask.node_id == node.id)
            .order_by(NodeTask.created_at.desc())
            .limit(50)
        )
    )
    settings = get_settings()
    base = str(request.base_url).rstrip("/") + settings.admin_prefix
    command = node_install.build_install_command(node, panel_url=base)
    config_text = node.config or ""
    if node.agent_url:
        try:
            result = node_client.NodeAgentClient(node).read_config()
            if result.ok:
                config_text = result.data.get("content", config_text)
        except node_client.NodeClientError:
            pass
    return render(
        request,
        "node_detail.html",
        user,
        active="nodes",
        node=node,
        metric=metric,
        tasks=tasks,
        install_command=command,
        config_text=config_text,
        agent_port=node_install.agent_port(node),
    )


@router.get("/resellers", response_class=HTMLResponse)
def resellers_page(request: Request, db: DbSession, user: AdminUser) -> HTMLResponse:
    from app.services import resellers as resellers_service

    resellers = resellers_service.list_resellers(db)
    rows = [
        {
            "user": reseller,
            "usage": resellers_service.usage(db, reseller),
            "nodes": resellers_service.allowed_nodes(db, reseller),
            "expired": resellers_service.is_expired(reseller),
        }
        for reseller in resellers
    ]
    nodes = list(db.scalars(select(Node).where(Node.is_active.is_(True)).order_by(Node.name)))
    return render(
        request,
        "resellers.html",
        user,
        active="resellers",
        rows=rows,
        resellers=resellers,
        nodes=nodes,
    )


@router.get("/reports", response_class=HTMLResponse)
def reports_page(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    preset: str = "daily",
    start: str = "",
    end: str = "",
    reseller_id: int = 0,
) -> HTMLResponse:
    from datetime import date as date_type

    from app.services import reports as reports_service

    owner_id = _owner_id(user)
    owner_label = None
    if user.role == Role.ADMIN and reseller_id:
        owner_id = reseller_id
        target = db.get(User, reseller_id)
        owner_label = target.username if target else None

    def _parse(value: str) -> date_type | None:
        try:
            return date_type.fromisoformat(value)
        except ValueError:
            return None

    try:
        report = reports_service.build_report(
            db,
            owner_id=owner_id,
            preset=preset,
            start=_parse(start),
            end=_parse(end),
            owner_label=owner_label,
        )
    except reports_service.ReportError:
        report = reports_service.build_report(db, owner_id=owner_id, preset="daily")

    resellers = (
        list(db.scalars(select(User).where(User.role == Role.RESELLER).order_by(User.username)))
        if user.role == Role.ADMIN
        else []
    )
    return render(
        request,
        "reports.html",
        user,
        active="reports",
        report=report,
        preset=preset,
        start=start,
        end=end,
        reseller_id=reseller_id,
        resellers=resellers,
    )


@router.get("/audit", response_class=HTMLResponse)
def audit_page(
    request: Request,
    db: DbSession,
    user: AdminUser,
    page: int = 1,
    action: str = "",
    actor_id: int = 0,
) -> HTMLResponse:
    from app.services import audit as audit_service

    page = max(1, page)
    size = 100
    entries, total = audit_service.list_entries(
        db,
        action=action or None,
        actor_id=actor_id or None,
        limit=size,
        offset=(page - 1) * size,
    )
    return render(
        request,
        "audit.html",
        user,
        active="audit",
        entries=entries,
        total=total,
        page=page,
        pages=max(1, (total + size - 1) // size),
        action=action,
        actor_id=actor_id,
        actions=audit_service.distinct_actions(db),
    )


@router.get("/permissions", response_class=HTMLResponse)
def permissions_page(request: Request, db: DbSession, user: CurrentUser) -> HTMLResponse:
    from app.core import permissions as permissions_module

    return render(
        request,
        "permissions.html",
        user,
        active="permissions",
        rows=permissions_module.matrix_rows(),
        roles=["admin", "reseller"],
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: DbSession, user: AdminUser) -> HTMLResponse:
    from app.services import server_config, ssl_certs
    from app.services import settings as settings_service

    return render(
        request,
        "settings.html",
        user,
        active="settings",
        branding=settings_service.get_branding(db),
        panel={
            "title": settings_service.get(db, "panel.title"),
            "language": settings_service.get(db, "panel.language"),
            "theme": settings_service.get(db, "panel.theme"),
            "port": settings_service.get(db, "panel.port"),
            "update_enabled": settings_service.get(db, "update.enabled"),
            "update_repo": settings_service.get(db, "update.repo"),
        },
        backup={
            "schedule": settings_service.get(db, "backup.schedule"),
            "upload": settings_service.get(db, "backup.upload"),
            "telegram_token": settings_service.get(db, "backup.telegram_token"),
            "telegram_chat": settings_service.get(db, "backup.telegram_chat"),
            "webhook_url": settings_service.get(db, "backup.webhook_url"),
            "last_run": settings_service.get(db, "backup.last_run"),
        },
        ssl_status=ssl_certs.status(db),
        server_config_text_len=len(server_config.read_config()),
        version=__version__,
    )


@router.post("/settings/panel")
def save_panel_settings(
    request: Request,
    db: DbSession,
    user: AdminUser,
    title: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "en",
    theme: Annotated[str, Form()] = "system",
    port: Annotated[str, Form()] = "",
    update_enabled: Annotated[str, Form()] = "",
    update_repo: Annotated[str, Form()] = "",
) -> RedirectResponse:
    from app.services import audit as audit_service
    from app.services import settings as settings_service

    values = {
        "panel.title": title.strip() or "OpenVPN Web Panel",
        "panel.language": language if language in {"en", "fa"} else "en",
        "panel.theme": theme if theme in {"light", "dark", "system"} else "system",
        # Opt-in update check: default off. Only a plain "owner/repo" is accepted.
        "update.enabled": "true" if update_enabled == "on" else "false",
        "update.repo": update_repo.strip() if _valid_repo(update_repo.strip()) else "",
    }
    if port.isdigit() and 1 <= int(port) <= 65535:
        values["panel.port"] = port
    settings_service.set_many(db, values)
    audit_service.record(
        db,
        action="settings.panel",
        actor=user,
        target_type="setting",
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    prefix = get_settings().admin_prefix
    return RedirectResponse(url=f"{prefix}/settings?saved=panel", status_code=303)


@router.post("/branding")
def save_branding(
    request: Request,
    db: DbSession,
    user: AdminUser,
    brand_name: Annotated[str, Form()] = "",
    logo_url: Annotated[str, Form()] = "",
    support_url: Annotated[str, Form()] = "",
    footer: Annotated[str, Form()] = "",
) -> RedirectResponse:
    from app.services import audit as audit_service
    from app.services import settings as settings_service

    settings_service.set_many(
        db,
        {
            "branding.name": brand_name.strip(),
            "branding.logo_url": logo_url.strip(),
            "branding.support_url": support_url.strip(),
            "branding.footer": footer.strip(),
        },
    )
    audit_service.record(
        db,
        action="settings.branding",
        actor=user,
        target_type="setting",
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    url = f"{get_settings().admin_prefix}/settings?saved=branding"
    return RedirectResponse(url=url, status_code=303)


@router.post("/settings/ssl/letsencrypt")
def save_ssl_letsencrypt(
    request: Request,
    db: DbSession,
    user: AdminUser,
    domain: Annotated[str, Form()],
    email: Annotated[str, Form()] = "",
    auto_renew: Annotated[str, Form()] = "on",
) -> RedirectResponse:
    from app.services import audit as audit_service
    from app.services import ssl_certs

    base = f"{get_settings().admin_prefix}/settings"
    try:
        ssl_certs.configure_letsencrypt(
            db, domain=domain, email=email, auto_renew=auto_renew == "on"
        )
        audit_service.record(
            db,
            action="settings.ssl",
            actor=user,
            target_type="setting",
            detail=f"letsencrypt:{domain}",
            ip_address=request.client.host if request.client else None,
        )
        db.commit()
    except ssl_certs.SslError:
        db.rollback()
        return RedirectResponse(url=f"{base}?error=ssl", status_code=303)
    return RedirectResponse(url=f"{base}?saved=ssl", status_code=303)


@router.post("/settings/ssl/custom")
def save_ssl_custom(
    request: Request,
    db: DbSession,
    user: AdminUser,
    cert_pem: Annotated[str, Form()],
    key_pem: Annotated[str, Form()],
) -> RedirectResponse:
    from app.services import audit as audit_service
    from app.services import ssl_certs

    base = f"{get_settings().admin_prefix}/settings"
    try:
        ssl_certs.configure_custom(db, cert_pem=cert_pem, key_pem=key_pem)
        audit_service.record(
            db,
            action="settings.ssl",
            actor=user,
            target_type="setting",
            detail="custom",
            ip_address=request.client.host if request.client else None,
        )
        db.commit()
    except ssl_certs.SslError:
        db.rollback()
        return RedirectResponse(url=f"{base}?error=ssl", status_code=303)
    return RedirectResponse(url=f"{base}?saved=ssl", status_code=303)


@router.post("/settings/background")
def save_background_settings(
    request: Request,
    db: DbSession,
    user: AdminUser,
    schedule: Annotated[str, Form()] = "off",
    upload: Annotated[str, Form()] = "none",
    telegram_token: Annotated[str, Form()] = "",
    telegram_chat: Annotated[str, Form()] = "",
    webhook_url: Annotated[str, Form()] = "",
) -> RedirectResponse:
    from app.services import audit as audit_service
    from app.services import settings as settings_service

    settings_service.set_many(
        db,
        {
            "backup.schedule": schedule if schedule in {"off", "daily", "weekly"} else "off",
            "backup.upload": upload if upload in {"none", "telegram", "webhook"} else "none",
            "backup.telegram_token": telegram_token.strip(),
            "backup.telegram_chat": telegram_chat.strip(),
            "backup.webhook_url": webhook_url.strip(),
        },
    )
    audit_service.record(
        db,
        action="settings.backup",
        actor=user,
        target_type="setting",
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    prefix = get_settings().admin_prefix
    return RedirectResponse(url=f"{prefix}/settings?saved=backup", status_code=303)


@router.post("/settings/backup/download")
def download_backup(
    request: Request,
    db: DbSession,
    user: AdminUser,
    password: Annotated[str, Form()],
) -> Response:
    from app.services import audit as audit_service
    from app.services import backup

    prefix = get_settings().admin_prefix
    if not password:
        return RedirectResponse(url=f"{prefix}/settings?error=backup", status_code=303)
    result = backup.create_backup(password)
    audit_service.record(
        db,
        action="backup.create",
        actor=user,
        target_type="backup",
        detail=result.filename,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return Response(
        content=result.payload,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{result.filename}"'},
    )


@router.get("/settings/server-config", response_class=HTMLResponse)
def server_config_page(request: Request, db: DbSession, user: AdminUser) -> HTMLResponse:
    from app.services import server_config

    return render(
        request,
        "server_config.html",
        user,
        active="settings",
        config_text=server_config.read_config()[:200000],
    )


@router.post("/settings/server-config/apply")
def apply_server_config(
    request: Request,
    db: DbSession,
    user: AdminUser,
    content: Annotated[str, Form()] = "",
    restart: Annotated[str, Form()] = "on",
) -> RedirectResponse:
    from app.services import audit as audit_service
    from app.services import server_config

    result = server_config.apply(content, restart=restart == "on")
    audit_service.record(
        db,
        action="settings.server_config",
        actor=user,
        target_type="config",
        detail=result.message,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    base = f"{get_settings().admin_prefix}/settings/server-config"
    status = "saved=1" if result.ok else "error=validation"
    return RedirectResponse(url=f"{base}?{status}", status_code=303)


@router.post("/preferences")
def save_preferences(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    language: Annotated[str, Form()] = "en",
    theme: Annotated[str, Form()] = "system",
) -> RedirectResponse:
    if language in {"en", "fa"}:
        user.language = language
    if theme in {"light", "dark", "system"}:
        user.theme = theme
    db.add(user)
    db.commit()
    return RedirectResponse(url=f"{get_settings().admin_prefix}/settings", status_code=303)


@router.post("/change-password")
def change_password(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    current_password: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
    confirm_password: Annotated[str, Form()] = "",
) -> RedirectResponse:
    from app.core.security import verify_password
    from app.services import audit as audit_service
    from app.services import sessions as sessions_service
    from app.services import users as users_service

    settings = get_settings()
    base = settings.admin_prefix
    if not verify_password(current_password, user.password_hash):
        return RedirectResponse(url=f"{base}/settings?error=current", status_code=303)
    if new_password != confirm_password:
        return RedirectResponse(url=f"{base}/settings?error=mismatch", status_code=303)
    try:
        users_service.set_password(db, user, new_password)
    except users_service.UserError:
        db.rollback()
        return RedirectResponse(url=f"{base}/settings?error=weak", status_code=303)
    session = getattr(request.state, "session", None)
    sessions_service.delete_user_sessions(
        db, user.id, except_id=session.session_id if session else None
    )
    audit_service.record(
        db,
        action="user.change_password",
        actor=user,
        target_type="user",
        target_id=user.id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return RedirectResponse(url=f"{base}/settings?saved=password", status_code=303)


# --------------------------------------------------------------------------- #
# Tunnel Manager
# --------------------------------------------------------------------------- #
@router.get("/tunnels", response_class=HTMLResponse)
def tunnels_page(request: Request, db: DbSession, user: AdminUser) -> HTMLResponse:
    from app.services import tunnel_backend
    from app.services import tunnels as tunnels_service

    rows = []
    for tunnel in tunnels_service.list_tunnels(db):
        rows.append(
            {
                "tunnel": tunnel,
                "sample": tunnels_service.latest_sample(db, tunnel.id),
                "job": tunnels_service.latest_job(db, tunnel.id),
            }
        )
    return render(
        request,
        "tunnels.html",
        user,
        active="tunnels",
        rows=rows,
        summary=tunnels_service.summary(db),
        backends=tunnel_backend.backend_metadata(),
    )


@router.get("/tunnels/new", response_class=HTMLResponse)
def tunnel_wizard_page(request: Request, db: DbSession, user: AdminUser) -> HTMLResponse:
    from app.services import tunnel_backend
    from app.services import tunnels as tunnels_service

    return render(
        request,
        "tunnel_wizard.html",
        user,
        active="tunnels",
        nodes=tunnels_service.node_options(db),
        backends=tunnel_backend.backend_metadata(),
        suggested_port=tunnels_service.allocate_control_port(db),
        suggested_token=tunnels_service.random_token(),
    )


@router.post("/tunnels")
def create_tunnel_form(  # noqa: PLR0913
    request: Request,
    db: DbSession,
    user: AdminUser,
    name: Annotated[str, Form()],
    entry_host: Annotated[str, Form()],
    entry_ssh_port: Annotated[int, Form()] = 22,
    entry_username: Annotated[str, Form()] = "root",
    auth_method: Annotated[str, Form()] = "password",
    password: Annotated[str, Form()] = "",
    private_key: Annotated[str, Form()] = "",
    host_key_fingerprint: Annotated[str, Form()] = "",
    backend: Annotated[str, Form()] = "auto",
    transport: Annotated[str, Form()] = "auto",
    exit_host: Annotated[str, Form()] = "",
    control_port: Annotated[int, Form()] = 0,
    public_ports: Annotated[str, Form()] = "",
    node_id: Annotated[int, Form()] = 0,
    instance_name: Annotated[str, Form()] = "",
    mtu: Annotated[int, Form()] = 0,
    auto_heal: Annotated[str, Form()] = "on",
    disable_password_login: Annotated[str, Form()] = "",
) -> RedirectResponse:
    from app.services import audit as audit_service
    from app.services import tunnel_worker
    from app.services import tunnels as tunnels_service

    prefix = get_settings().admin_prefix
    ports = [int(p) for p in public_ports.replace(",", " ").split() if p.strip().isdigit()]
    spec = tunnels_service.TunnelSpec(
        name=name.strip(),
        entry_host=entry_host.strip(),
        control_port=control_port or tunnels_service.allocate_control_port(db),
        token=tunnels_service.random_token(),
        backend=backend,
        transport=transport,
        entry_ssh_port=entry_ssh_port or 22,
        entry_username=entry_username.strip() or "root",
        auth_method=auth_method if auth_method in {"password", "key"} else "password",
        exit_host=exit_host.strip() or None,
        public_ports=ports,
        host_key_fingerprint=host_key_fingerprint.strip() or None,
        node_id=node_id or None,
        instance_name=instance_name.strip() or None,
        mtu=mtu or None,
        auto_heal=auto_heal == "on",
        owner_id=None,
    )
    try:
        tunnel = tunnels_service.create_tunnel(db, spec)
        tunnels_service.ensure_panel_key(tunnel)
    except tunnels_service.TunnelError as exc:
        db.rollback()
        return RedirectResponse(url=f"{prefix}/tunnels/new?error={exc}", status_code=303)
    audit_service.record(
        db,
        action="tunnel.create",
        actor=user,
        target_type="tunnel",
        target_id=tunnel.id,
        ip_address=request.client.host if request.client else None,
        detail=f"backend={backend} transport={transport}",
    )
    db.commit()
    tunnel_worker.submit_provision(
        tunnel.id,
        {
            "password": password or None,
            "private_key": private_key or None,
            "disable_password_login": disable_password_login == "on",
        },
    )
    return RedirectResponse(url=f"{prefix}/tunnels/{tunnel.id}", status_code=303)


# --------------------------------------------------------------------------- #
# Protocols (Phase 12-15)
# --------------------------------------------------------------------------- #
@router.get("/protocols", response_class=HTMLResponse)
def protocols_page(request: Request, db: DbSession, user: AdminUser) -> HTMLResponse:
    from sqlalchemy import func

    from app.models.protocol import ProtocolUser
    from app.services import protocols, protocols_service

    counts = dict(
        db.execute(
            select(ProtocolUser.protocol_instance_id, func.count(ProtocolUser.id)).group_by(
                ProtocolUser.protocol_instance_id
            )
        ).all()
    )
    rows = []
    for instance in protocols_service.list_instances(db):
        rows.append(
            {
                "instance": instance,
                "settings": instance.settings_dict,
                "node": db.get(Node, instance.node_id) if instance.node_id else None,
                "users": int(counts.get(instance.id, 0)),
            }
        )
    return render(
        request,
        "protocols.html",
        user,
        active="protocols",
        rows=rows,
        backends=protocols.backend_metadata(),
        nodes=list(db.scalars(select(Node).order_by(Node.name))),
    )


@router.post("/protocols")
def create_protocol_form(  # noqa: PLR0913
    request: Request,
    db: DbSession,
    user: AdminUser,
    kind: Annotated[str, Form()],
    name: Annotated[str, Form()],
    node_id: Annotated[int, Form()] = 0,
    port: Annotated[int, Form()] = 0,
    address: Annotated[str, Form()] = "",
    subnet: Annotated[str, Form()] = "",
    dns: Annotated[str, Form()] = "",
    mtu: Annotated[int, Form()] = 0,
    inbound_type: Annotated[str, Form()] = "vless",
    security: Annotated[str, Form()] = "reality",
    sni: Annotated[str, Form()] = "",
    psk: Annotated[str, Form()] = "",
    is_default: Annotated[str, Form()] = "",
) -> RedirectResponse:
    from app.services import audit as audit_service
    from app.services import protocols_service

    prefix = get_settings().admin_prefix
    settings = {
        "inbound_type": inbound_type,
        "security": security,
        "sni": sni,
    }
    if psk:
        settings["psk"] = psk
    try:
        instance = protocols_service.create_instance(
            db,
            kind=kind,
            name=name.strip(),
            node_id=node_id or None,
            port=port or 0,
            address=address.strip() or None,
            subnet=subnet.strip() or None,
            dns=dns.strip() or None,
            mtu=mtu or None,
            is_default=is_default == "on",
            settings=settings,
        )
        audit_service.record(
            db,
            action="protocol.instance_create",
            actor=user,
            target_type="protocol_instance",
            target_id=instance.id,
            detail=f"{kind}:{name}",
            ip_address=request.client.host if request.client else None,
        )
        db.commit()
    except protocols_service.ProtocolServiceError as exc:
        db.rollback()
        return RedirectResponse(url=f"{prefix}/protocols?error={exc}", status_code=303)
    return RedirectResponse(url=f"{prefix}/protocols", status_code=303)


@router.post("/protocols/{instance_id}/install")
def install_protocol_form(
    request: Request, instance_id: int, db: DbSession, user: AdminUser
) -> RedirectResponse:
    from app.services import audit as audit_service
    from app.services import protocols_service

    prefix = get_settings().admin_prefix
    instance = protocols_service.get(db, instance_id)
    if instance is None:
        return RedirectResponse(url=f"{prefix}/protocols?error=notfound", status_code=303)
    node = db.get(Node, instance.node_id) if instance.node_id else None
    outcome = protocols_service.install_instance(db, instance, node)
    audit_service.record(
        db,
        action="protocol.install",
        actor=user,
        target_type="protocol_instance",
        target_id=instance.id,
        detail=f"ok={outcome.ok}",
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    status = "ok=1" if outcome.ok else "error=install"
    return RedirectResponse(url=f"{prefix}/protocols?{status}", status_code=303)


@router.post("/protocols/{instance_id}/health")
def health_protocol_form(instance_id: int, db: DbSession, user: AdminUser) -> RedirectResponse:
    from app.services import protocols_service

    prefix = get_settings().admin_prefix
    instance = protocols_service.get(db, instance_id)
    if instance is None:
        return RedirectResponse(url=f"{prefix}/protocols?error=notfound", status_code=303)
    node = db.get(Node, instance.node_id) if instance.node_id else None
    protocols_service.healthcheck_instance(db, instance, node)
    db.commit()
    return RedirectResponse(url=f"{prefix}/protocols?checked=1", status_code=303)


@router.post("/protocols/{instance_id}/delete")
def delete_protocol_form(instance_id: int, db: DbSession, user: AdminUser) -> RedirectResponse:
    from app.services import protocols_service

    prefix = get_settings().admin_prefix
    instance = protocols_service.get(db, instance_id)
    if instance is not None:
        protocols_service.delete_instance(db, instance)
        db.commit()
    return RedirectResponse(url=f"{prefix}/protocols", status_code=303)


@router.get("/tunnels/{tunnel_id}", response_class=HTMLResponse)
def tunnel_detail_page(
    request: Request, tunnel_id: int, db: DbSession, user: AdminUser
) -> HTMLResponse:
    from app.services import tunnel_backend
    from app.services import tunnels as tunnels_service

    tunnel = tunnels_service.get(db, tunnel_id)
    if tunnel is None:
        return render(request, "error.html", user, code=404, detail="tunnel not found")
    return render(
        request,
        "tunnel_detail.html",
        user,
        active="tunnels",
        tunnel=tunnel,
        sample=tunnels_service.latest_sample(db, tunnel.id),
        history=tunnels_service.recent_health(db, tunnel.id),
        job=tunnels_service.latest_job(db, tunnel.id),
        nodes=tunnels_service.node_options(db),
        backends=tunnel_backend.backend_metadata(),
    )
