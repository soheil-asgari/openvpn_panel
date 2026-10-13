"""Central permission matrix for admins and resellers (sub-admins).

A single source of truth mapping ``(role, permission)`` to whether the action is
allowed. Routers consult :func:`can` / :func:`require` instead of duplicating
ad-hoc role checks, so the matrix can be rendered in the UI and audited.
"""

from __future__ import annotations

import enum

from app.models.user import Role


class Permission(str, enum.Enum):
    """Discrete capabilities recognised by the panel."""

    VIEW_DASHBOARD = "view_dashboard"
    VIEW_USERS = "view_users"
    MANAGE_USERS = "manage_users"
    VIEW_ONLINE = "view_online"
    MANAGE_SESSIONS = "manage_sessions"
    VIEW_NODES = "view_nodes"
    MANAGE_NODES = "manage_nodes"
    VIEW_RESELLERS = "view_resellers"
    MANAGE_RESELLERS = "manage_resellers"
    VIEW_REPORTS = "view_reports"
    VIEW_AUDIT = "view_audit"
    MANAGE_SETTINGS = "manage_settings"
    MANAGE_BRANDING = "manage_branding"
    MANAGE_API_KEYS = "manage_api_keys"


# Permissions granted to each role.
_MATRIX: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.RESELLER: frozenset(
        {
            Permission.VIEW_DASHBOARD,
            Permission.VIEW_USERS,
            Permission.MANAGE_USERS,
            Permission.VIEW_ONLINE,
            Permission.MANAGE_SESSIONS,
            Permission.VIEW_NODES,
            Permission.VIEW_REPORTS,
            Permission.MANAGE_API_KEYS,
        }
    ),
}

# Human-readable descriptions, used by the permission-matrix page.
LABELS: dict[Permission, str] = {
    Permission.VIEW_DASHBOARD: "View dashboard",
    Permission.VIEW_USERS: "View VPN users",
    Permission.MANAGE_USERS: "Create/edit/delete own VPN users",
    Permission.VIEW_ONLINE: "View online users",
    Permission.MANAGE_SESSIONS: "Disconnect own users",
    Permission.VIEW_NODES: "View nodes",
    Permission.MANAGE_NODES: "Create/edit/delete nodes",
    Permission.VIEW_RESELLERS: "View sub-admins",
    Permission.MANAGE_RESELLERS: "Create/edit/delete sub-admins",
    Permission.VIEW_REPORTS: "View usage reports",
    Permission.VIEW_AUDIT: "View audit log",
    Permission.MANAGE_SETTINGS: "Change settings",
    Permission.MANAGE_BRANDING: "Change subscription branding",
    Permission.MANAGE_API_KEYS: "Manage own API keys",
}


class PermissionError_(ValueError):
    """Raised when a role lacks a required permission."""


def permissions_for(role: Role) -> frozenset[Permission]:
    return _MATRIX.get(role, frozenset())


def can(role: Role, permission: Permission) -> bool:
    return permission in permissions_for(role)


def require(role: Role, permission: Permission) -> None:
    if not can(role, permission):
        raise PermissionError_(f"role {role.value} lacks permission {permission.value}")


def matrix_rows() -> list[dict]:
    """Return the matrix as rows for rendering (permission + per-role flags)."""

    ordered = sorted(Permission, key=lambda p: p.value)
    roles = [Role.ADMIN, Role.RESELLER]
    return [
        {
            "permission": permission.value,
            "label": LABELS.get(permission, permission.value),
            "roles": {role.value: can(role, permission) for role in roles},
        }
        for permission in ordered
    ]
