# Third-Party Licenses

This project is licensed under the [MIT License](LICENSE). It depends on, and at
install time downloads, software written by others. This file lists each
component, its license and how it is used, and flags anything that needs care
when distributing from an MIT-licensed project.

The rule applied throughout: **third-party VPN/tunnel cores are downloaded as
official release binaries at install time and executed as separate programs.**
They are never bundled, statically linked or shipped inside our artifacts. That
keeps each project's copyleft obligations attached to that project, not to this
MIT-licensed codebase.

---

## Python dependencies (installed via pip)

| Package | License | SPDX | Notes |
| --- | --- | --- | --- |
| fastapi | MIT | MIT | Web framework |
| uvicorn | BSD-3-Clause | BSD-3-Clause | ASGI server |
| starlette | BSD-3-Clause | BSD-3-Clause | Transitive (via fastapi) |
| sqlalchemy | MIT | MIT | ORM |
| alembic | MIT | MIT | Migrations |
| apscheduler | MIT | MIT | Scheduler (backups, tasks) |
| jinja2 | BSD-3-Clause | BSD-3-Clause | Templates |
| argon2-cffi | MIT | MIT | Password hashing |
| python-multipart | Apache-2.0 | Apache-2.0 | Form parsing |
| itsdangerous | BSD-3-Clause | BSD-3-Clause | Signed values |
| pydantic | MIT | MIT | Validation / settings |
| pydantic-settings | MIT | MIT | Settings loading |
| httpx | BSD-3-Clause | BSD-3-Clause | HTTP client |
| email-validator | CC0-1.0 | CC0-1.0 | Email validation |
| psycopg[binary] | LGPL-3.0-only | LGPL-3.0-only | Optional PostgreSQL driver — dynamically linked; no modification |
| paramiko | LGPL-2.1-or-later | LGPL-2.1-or-later | Optional SSH provisioning for tunnels; imported as a library (unmodified) |
| cryptography | Apache-2.0 OR BSD-3-Clause | Apache-2.0 OR BSD-3-Clause | Key handling for tunnels |
| certifi | MPL-2.0 | MPL-2.0 | CA bundle (transitive) |

All of the above are compatible with an MIT-licensed distribution. The two
LGPL entries are used as unmodified, dynamically-linked libraries; LGPL
requires that recipients can relink, which is satisfied because they are
ordinary pip-installed packages listed in `pyproject.toml`.

## Front-end libraries (vendored into `app/static/js`)

| Component | License | SPDX | Delivery |
| --- | --- | --- | --- |
| htmx | BSD-2-Clause | BSD-2-Clause | Fetched by `scripts/fetch-assets.sh`, committed as prebuilt asset |
| Chart.js | MIT | MIT | Fetched by `scripts/fetch-assets.sh`, committed as prebuilt asset |

## System packages installed by the installer (apt/dnf/yum/apk)

Installed from the distribution's own signed repositories; the distribution
publisher is responsible for the license texts. None are bundled by us.

| Package | License | Purpose |
| --- | --- | --- |
| openvpn | GPL-2.0 | Primary VPN daemon |
| easy-rsa | GPL-2.0 | PKI tooling |
| nftables / iptables | GPL-2.0 | NAT and firewalling |
| wireguard-tools | GPL-2.0 | Optional WireGuard protocol |
| ocserv (OpenConnect) | GPL-2.0 | Optional Cisco AnyConnect-compatible protocol |
| libreswan + xl2tpd | GPL-2.0 | Optional L2TP/IPsec protocol |
| certbot | Apache-2.0 | Optional Let's Encrypt certificates |
| python3 / sqlite3 / curl / openssl / rsync | PSF-2.0 / Public-Domain / curl / Apache-2.0 / GPL-3.0 | Runtime prerequisites |

## Tunnel backends downloaded at install time

These are **downloaded as official release binaries** from their upstream
projects (never bundled) and invoked as separate programs.

| Component | License | SPDX | Delivery | Compatibility note |
| --- | --- | --- | --- | --- |
| Backhaul | AGPL-3.0 | AGPL-3.0-only | Official upstream release binary | **AGPL-3.0 is copyleft.** Acceptable because we download and run the *unmodified upstream binary* as a separate process — no linking, no code copying, no derivative work. **Do not bundle a modified Backhaul** in any artifact. Preserve upstream notices. |
| sing-box | GPL-3.0-or-later | GPL-3.0-or-later | Official upstream release binary | Same external-binary reasoning. Do not bundle; download at install time and keep upstream notices. |
| WireGuard | GPL-2.0 | GPL-2.0 | Kernel module + `wireguard-tools` from the distro | External process/kernel use only; never statically link GPL code into this panel. |

### Flagged items

- **Backhaul (AGPL-3.0)** and **sing-box (GPL-3.0)** are the only strong-copyleft
  pieces. They remain compliant with an MIT project **only as separate,
  unmodified, downloaded binaries**. This repository therefore:
  - does not commit their binaries or source,
  - resolves them at install/provision time from official release endpoints,
  - keeps their notices intact on the target host.
- If a future change ever requires shipping a *modified* copy of Backhaul or
  sing-box inside a release artifact, it would create a licensing conflict and
  must be rejected.

## Summary

- No license conflict prevents distributing this project under MIT.
- No third-party *source* is copied into this repository.
- All copyleft components are consumed as separate, unmodified programs or
  dynamically-linked libraries.
