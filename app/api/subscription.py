"""Public subscription endpoints (keyed by an unguessable per-user token).

These routes are intentionally unauthenticated: the token in the URL *is* the
credential. They only ever expose the data of the single VPN user the token
resolves to, and honour that user's hidden-node overrides.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.core import qr
from app.core.i18n import translator
from app.core.units import format_size
from app.db import get_session_factory
from app.models.node import Node
from app.services import subscriptions as subscriptions_service

router = APIRouter()

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
templates.env.filters["size"] = format_size


def _open(token: str):  # noqa: ANN001
    """Open a DB session and resolve the token, or raise ``404``."""

    db = get_session_factory()()
    try:
        vpn_user = subscriptions_service.get_by_token(db, token)
        if vpn_user is None:
            raise HTTPException(status_code=404, detail="subscription not found")
        return db, vpn_user
    except Exception:
        db.close()
        raise


def _config_url(request: Request, token: str, node_id: int) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/sub/{token}/config/{node_id}.ovpn"


@router.get("/sub/{token}", response_class=HTMLResponse)
def subscription_page(token: str, request: Request) -> HTMLResponse:
    db, vpn_user = _open(token)
    try:
        view = subscriptions_service.build_view(db, vpn_user)
        settings = get_settings()
        t = translator(settings.default_language)
        context = {
            "request": request,
            "t": t,
            "lang": t.language,
            "is_rtl": t.is_rtl,
            "theme": "system",
            "user": None,
            # Subscription routes live at the site root, not under admin_prefix.
            "base_path": "",
            "csrf_token": "",
            "view": view,
            "branding": view.branding,
            "token": vpn_user.subscription_token,
            "config_url": (
                _config_url(request, token, view.nodes[0].node.id) if view.nodes else None
            ),
        }
        return templates.TemplateResponse(request, "subscription.html", context)
    finally:
        db.close()


@router.get("/sub/{token}/config/{node_id}.ovpn")
def subscription_config(token: str, node_id: int) -> Response:
    db, vpn_user = _open(token)
    try:
        node = db.get(Node, node_id)
        visible = {n.node.id for n in subscriptions_service.build_view(db, vpn_user).nodes}
        if node is None or node.id not in visible:
            raise HTTPException(status_code=404, detail="node not available")
        host, port, _tunnel = subscriptions_service.resolve_endpoint(db, vpn_user, node)
        config_text = subscriptions_service.build_config_for(vpn_user, node, host=host, port=port)
        filename = subscriptions_service.config_filename(vpn_user, node)
    finally:
        db.close()
    return Response(
        content=config_text,
        media_type="application/x-openvpn-profile",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/sub/{token}/qr.svg")
def subscription_qr(token: str, request: Request, node_id: int | None = None) -> Response:
    db, vpn_user = _open(token)
    try:
        view = subscriptions_service.build_view(db, vpn_user)
        visible = {n.node.id for n in view.nodes}
        if node_id is None:
            node_id = next(iter(sorted(visible)), None)
        if node_id is None or node_id not in visible:
            raise HTTPException(status_code=404, detail="node not available")
        target = _config_url(request, token, node_id)
    finally:
        db.close()
    return Response(content=qr.to_svg(target), media_type="image/svg+xml")


@router.get("/sub/{token}/qr.png")
def subscription_qr_png(token: str, request: Request, node_id: int | None = None) -> Response:
    # SVG renders in every browser; serve it for the ``.png`` alias too.
    return subscription_qr(token, request, node_id)


@router.get("/sub/{token}/subscription.txt")
def subscription_links(token: str) -> Response:
    """Aggregated per-protocol share links (one per line)."""

    db, vpn_user = _open(token)
    try:
        view = subscriptions_service.build_view(db, vpn_user)
        body = view.aggregated_link
    finally:
        db.close()
    return Response(
        content=body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="subscription.txt"'},
    )


@router.get("/sub/{token}/protocol-qr.svg")
def subscription_protocol_qr(token: str, kind: str | None = None) -> Response:
    """QR code for one protocol link (or the aggregated subscription)."""

    db, vpn_user = _open(token)
    try:
        view = subscriptions_service.build_view(db, vpn_user)
        if kind:
            payload = next((p.qr_payload for p in view.protocols if p.kind == kind), "")
        else:
            payload = view.aggregated_link
    finally:
        db.close()
    if not payload:
        raise HTTPException(status_code=404, detail="no protocol link available")
    return Response(content=qr.to_svg(payload), media_type="image/svg+xml")
