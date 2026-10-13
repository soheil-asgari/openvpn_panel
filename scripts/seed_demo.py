#!/usr/bin/env python3
"""Populate a demo instance with FAKE data for screenshots and local testing.

Never point this at a production database. All values are synthetic: no real
IPs, domains, emails or usernames. Run after ``init-db``:

    OVPN_PANEL_CONFIG_DIR=/path/to/config python scripts/seed_demo.py

The generated credentials are printed once and are intentionally weak test
values — they exist only to make a demo look realistic.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

DEMO_ADMIN = "admin"
DEMO_ADMIN_PW = "demo-admin-password"
DEMO_RESELLER = "reseller"
DEMO_RESELLER_PW = "demo-reseller-password"
DEMO_USER_PW = "demo-user-password"


def _require_demo_opt_in() -> None:
    # Guard against accidental use on a real deployment.
    if os.environ.get("OVPN_ALLOW_DEMO_SEED") != "1":
        raise SystemExit(
            "refusing to seed demo data: set OVPN_ALLOW_DEMO_SEED=1 to confirm "
            "this database contains no real data"
        )


def main() -> int:
    _require_demo_opt_in()

    from app.db import session_scope
    from app.models.user import Role
    from app.services import users as users_service
    from app.services import vpn_users as vpn_users_service

    now = datetime.now(timezone.utc)
    with session_scope() as db:
        if users_service.get_by_username(db, DEMO_ADMIN) is None:
            users_service.create_user(
                db, username=DEMO_ADMIN, password=DEMO_ADMIN_PW, role=Role.ADMIN
            )

        reseller = users_service.get_by_username(db, DEMO_RESELLER)
        if reseller is None:
            reseller = users_service.create_user(
                db,
                username=DEMO_RESELLER,
                password=DEMO_RESELLER_PW,
                role=Role.RESELLER,
                quota_users=25,
            )

        for i in range(1, 6):
            name = f"demo{i}"
            if vpn_users_service.get_by_username(db, name) is None:
                vpn_users_service.create_vpn_user(
                    db,
                    username=name,
                    password=DEMO_USER_PW,
                    owner=reseller,
                    expires_at=now + timedelta(days=30 * i),
                    data_limit_bytes=10 * 1024**3 * i,
                )

    print("demo data ready (fake):")
    print(f"  admin    / {DEMO_ADMIN_PW}")
    print(f"  reseller / {DEMO_RESELLER_PW}")
    print(f"  demo1..5 / {DEMO_USER_PW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
