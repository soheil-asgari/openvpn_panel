"""``ovpn-panel`` command line interface.

Subcommands:
  init-db          create/upgrade the database schema
  create-admin     create an initial admin account
  reset-password   reset a panel user's password
  gen-secrets      write secrets into the config directory (mode 600)
  import-auth      export VPN users to OpenVPN's auth file via the helper
"""

from __future__ import annotations

import argparse
import os
import secrets
import stat
import sys
from pathlib import Path

from app.config import CONFIG_DIR, get_settings
from app.core.security import generate_password
from app.core.validators import validate_username
from app.db import session_scope
from app.models.user import Role
from app.services import users as users_service

PANEL_ENV = CONFIG_DIR / "panel.env"


def _write_env_file(values: dict[str, str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 600


def cmd_gen_secrets(args: argparse.Namespace) -> int:
    path = Path(args.output) if args.output else PANEL_ENV
    values = {
        "OVPN_SECRET_KEY": secrets.token_urlsafe(48),
        "OVPN_HOOK_TOKEN": secrets.token_urlsafe(32),
        "OVPN_DATABASE_URL": args.database_url,
        "OVPN_SESSION_SECURE": "true",
    }
    if path.exists() and not args.force:
        print(f"refusing to overwrite {path} (use --force)", file=sys.stderr)
        return 1
    _write_env_file(values, path)
    print(f"wrote {path} (mode 600)")
    return 0


def cmd_init_db(_args: argparse.Namespace) -> int:
    from app.db import get_engine
    from app.models import Base

    Base.metadata.create_all(bind=get_engine())
    print("database schema ready")
    return 0


def cmd_create_admin(args: argparse.Namespace) -> int:
    validate_username(args.username)
    password = args.password or generate_password()
    with session_scope() as db:
        if users_service.get_by_username(db, args.username):
            print("user already exists", file=sys.stderr)
            return 1
        users_service.create_user(db, username=args.username, password=password, role=Role.ADMIN)
    print(f"admin '{args.username}' created")
    if not args.password:
        print(f"generated password: {password}")
    return 0


def cmd_reset_password(args: argparse.Namespace) -> int:
    validate_username(args.username)
    password = args.password or generate_password()
    with session_scope() as db:
        user = users_service.get_by_username(db, args.username)
        if user is None:
            print("user not found", file=sys.stderr)
            return 1
        users_service.set_password(db, user, password)
    print("password updated")
    if not args.password:
        print(f"generated password: {password}")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    from app.services import backup

    password = args.password or os.environ.get("OVPN_BACKUP_PASSWORD", "")
    if not password:
        print("a password is required (--password or OVPN_BACKUP_PASSWORD)", file=sys.stderr)
        return 1
    try:
        result = backup.create_backup(password, include_pki=not args.no_pki)
    except backup.BackupError as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    target = Path(args.output) if args.output else Path.cwd() / result.filename
    target.write_bytes(result.payload)
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    print(f"wrote {target} ({len(result.entries)} entries)")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    from app.services import backup

    password = args.password or os.environ.get("OVPN_BACKUP_PASSWORD", "")
    archive = Path(args.archive)
    if not archive.is_file():
        print(f"archive not found: {archive}", file=sys.stderr)
        return 1
    try:
        result = backup.restore_backup(archive.read_bytes(), password, restore_configs=args.configs)
    except Exception as exc:  # noqa: BLE001
        print(f"restore failed: {exc}", file=sys.stderr)
        return 1
    print(f"restored: {', '.join(result.get('extracted', [])) or 'nothing'}")
    return 0


def cmd_migrate_db(args: argparse.Namespace) -> int:
    """Copy all rows from the current (SQLite) database to a target URL."""

    from sqlalchemy import create_engine, select

    from app.db import get_engine
    from app.models import Base

    target_url = args.target
    if not target_url:
        print("--target is required (e.g. postgresql+psycopg://...)", file=sys.stderr)
        return 1
    source_engine = get_engine()
    target_engine = create_engine(target_url, future=True)
    Base.metadata.create_all(bind=target_engine)

    with source_engine.connect() as src, target_engine.begin() as dst:
        for table in reversed(Base.metadata.sorted_tables):
            dst.execute(table.delete())
        for table in Base.metadata.sorted_tables:
            rows = [dict(row) for row in src.execute(select(table)).mappings()]
            if rows:
                dst.execute(table.insert(), rows)
            print(f"copied {len(rows):>6} rows -> {table.name}")
    print("migration complete")
    return 0


def cmd_tunnel_list(_args: argparse.Namespace) -> int:
    """List tunnels (mirrors the web UI's tunnel dashboard)."""

    from app.services import tunnels as tunnels_service

    with session_scope() as db:
        rows = tunnels_service.list_tunnels(db)
        if not rows:
            print("no tunnels")
            return 0
        for tunnel in rows:
            ports = ",".join(str(p) for p in tunnel.port_list()) or str(tunnel.control_port)
            print(
                f"{tunnel.name:<20} {tunnel.backend.value:<10} {tunnel.transport.value:<7} "
                f"{tunnel.status.value:<12} {tunnel.entry_host}:{tunnel.entry_ssh_port} "
                f"ports={ports}"
            )
    return 0


def cmd_tunnel_add(args: argparse.Namespace) -> int:
    """Create + provision a tunnel from the CLI (equivalent to the wizard)."""

    from app.services import tunnels as tunnels_service

    ports = [int(p) for p in (args.ports or "").replace(",", " ").split() if p.isdigit()]
    with session_scope() as db:
        spec = tunnels_service.TunnelSpec(
            name=args.name,
            entry_host=args.entry_host,
            control_port=args.control_port or tunnels_service.allocate_control_port(db),
            token=tunnels_service.random_token(),
            backend=args.backend,
            transport=args.transport,
            entry_ssh_port=args.ssh_port,
            entry_username=args.username,
            auth_method="key" if args.private_key else "password",
            exit_host=args.exit_host,
            public_ports=ports,
            host_key_fingerprint=args.fingerprint,
            node_id=args.node_id,
            instance_name=args.instance,
            mtu=args.mtu,
        )
        tunnel = tunnels_service.create_tunnel(db, spec)
        tunnels_service.ensure_panel_key(tunnel)
        ctx = tunnels_service.ProvisionContext(
            spec=spec,
            password=args.password,
            private_key=args.private_key,
        )
        job = tunnels_service.provision(db, tunnel, ctx)
        print(job.log)
        print(f"tunnel '{tunnel.name}' status={job.status.value}")
    return 0 if job.status.value == "done" else 1


def cmd_tunnel_action(args: argparse.Namespace) -> int:
    from app.services import tunnels as tunnels_service

    with session_scope() as db:
        tunnel = tunnels_service.get_by_name(db, args.name)
        if tunnel is None:
            print("tunnel not found", file=sys.stderr)
            return 1
        job = tunnels_service.operate(db, tunnel, args.action)
        print(job.log)
        print(f"{args.action}: {job.status.value}")
    return 0 if job.status.value == "done" else 1


def cmd_tunnel_mtu(args: argparse.Namespace) -> int:
    from app.services import mtu_finder
    from app.services import tunnels as tunnels_service

    with session_scope() as db:
        tunnel = tunnels_service.get_by_name(db, args.name)
        target = tunnel.entry_host if tunnel else args.host
        result = mtu_finder.find_mtu(target)
        if not result.ok:
            print(f"mtu discovery failed: {result.detail}", file=sys.stderr)
            return 1
        print(f"mtu={result.mtu} mssfix={result.mssfix} target={result.target}")
        if tunnel and args.apply:
            tunnel.mtu = result.mtu
            tunnel.mssfix = result.mssfix
            ok, message = tunnels_service.apply_mtu_to_instance(tunnel)
            print(f"applied: {message}" if ok else f"apply failed: {message}")
    return 0


def cmd_import_auth(_args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.models.vpn_user import VpnUser, VpnUserStatus
    from app.services import helper_client

    entries: dict[str, str] = {}
    with session_scope() as db:
        for user in db.scalars(select(VpnUser).where(VpnUser.status == VpnUserStatus.ACTIVE)):
            entries[user.username] = user.password_hash
    result = helper_client.write_auth_file(entries)
    if not result.ok:
        print(f"helper error: {result.error}", file=sys.stderr)
        return 1
    print(f"exported {len(entries)} users")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ovpn-panel", description="OpenVPN Web Panel CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("gen-secrets", help="generate secrets into /etc/ovpn-panel")
    p.add_argument("--output", help="target file (default: /etc/ovpn-panel/panel.env)")
    p.add_argument("--database-url", default="sqlite:////var/lib/ovpn-panel/panel.sqlite3")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_gen_secrets)

    p = sub.add_parser("init-db", help="create database schema")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("create-admin", help="create an admin account")
    p.add_argument("username")
    p.add_argument("--password")
    p.set_defaults(func=cmd_create_admin)

    p = sub.add_parser("reset-password", help="reset a panel user's password")
    p.add_argument("username")
    p.add_argument("--password")
    p.set_defaults(func=cmd_reset_password)

    p = sub.add_parser("import-auth", help="export VPN users to the OpenVPN auth file")
    p.set_defaults(func=cmd_import_auth)

    p = sub.add_parser("backup", help="create an encrypted backup archive")
    p.add_argument("--password", help="encryption password (or OVPN_BACKUP_PASSWORD)")
    p.add_argument("--output", help="output path (default: current directory)")
    p.add_argument("--no-pki", action="store_true", help="exclude the PKI directory")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", help="restore from an encrypted backup archive")
    p.add_argument("archive")
    p.add_argument("--password", help="decryption password (or OVPN_BACKUP_PASSWORD)")
    p.add_argument("--configs", action="store_true", help="also restore config files")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("migrate-db", help="copy all rows to another database URL")
    p.add_argument("--target", required=True, help="target SQLAlchemy database URL")
    p.set_defaults(func=cmd_migrate_db)

    p = sub.add_parser("tunnel-list", help="list tunnels")
    p.set_defaults(func=cmd_tunnel_list)

    p = sub.add_parser("tunnel-add", help="create and provision a tunnel")
    p.add_argument("name")
    p.add_argument("entry_host")
    p.add_argument("--ssh-port", type=int, default=22)
    p.add_argument("--username", default="root")
    p.add_argument("--password")
    p.add_argument("--private-key")
    p.add_argument("--fingerprint")
    p.add_argument("--backend", default="auto", choices=["auto", "backhaul", "wireguard"])
    p.add_argument("--transport", default="auto", choices=["auto", "tcp", "tcpmux", "udp", "wg"])
    p.add_argument("--exit-host")
    p.add_argument("--control-port", type=int, default=0)
    p.add_argument("--ports", default="", help="comma separated public ports")
    p.add_argument("--node-id", type=int, default=None)
    p.add_argument("--instance", default=None)
    p.add_argument("--mtu", type=int, default=None)
    p.set_defaults(func=cmd_tunnel_add)

    p = sub.add_parser("tunnel-action", help="start/stop/restart/update/uninstall a tunnel")
    p.add_argument("name")
    p.add_argument("action", choices=["start", "stop", "restart", "update", "uninstall"])
    p.set_defaults(func=cmd_tunnel_action)

    p = sub.add_parser("tunnel-mtu", help="find (and optionally apply) the best MTU")
    p.add_argument("name", nargs="?", default="")
    p.add_argument("--host", default="1.1.1.1")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_tunnel_mtu)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    get_settings()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
