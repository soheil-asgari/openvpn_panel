# Architecture

The project is split into three cooperating processes and a small CLI.

```
┌──────────────────────┐        unix socket (JSON)       ┌─────────────────────────┐
│  app (FastAPI)       │ ──────────────────────────────► │  helper (root)          │
│  runs as ovpn-panel  │                                 │  allow-listed actions   │
│  web UI + REST API   │                                 │  easy-rsa, systemctl    │
└──────────┬───────────┘                                 └─────────────────────────┘
           │ HTTPS to node agent (HMAC-signed)                      │
           ▼                                                        │ writes
┌──────────────────────┐                                          ▼
│  agent (per node)    │  ───► OpenVPN management unix socket   /etc/openvpn/**
│  runs as ovpn-panel  │  ───► OpenVPN hooks (auth/connect)
└──────────────────────┘
```

The panel authenticates to each node agent with an HMAC-SHA256 signature over
`method\npath\ntimestamp\nbody` using a per-node shared secret (bearer tokens are
still accepted for legacy nodes). User/limit changes are pushed immediately and,
on failure, queued in `node_tasks` for retry with backoff; the scheduler
reconciles a node's full user set once it becomes reachable again. Health
samples (CPU/RAM/disk/network/clients) are stored in `node_metrics` and surfaced
on the node dashboard.

## Components

### `app/` — panel
- **`api/`** — routers: `auth.py` (login/logout), `web.py` (pages),
  `partials.py` (HTMX fragments), `api_v1.py` (JSON REST API),
  `subscription.py` (public `/sub/<token>` pages, `hooks.py` (OpenVPN callbacks).
- **`core/`** — validation, argon2 hashing, sessions, CSRF, rate limiting, i18n,
  the permission matrix (`permissions.py`) and the dependency-free QR generator
  (`qr.py`).
- **`models/`** — SQLAlchemy 2.x declarative models.
- **`services/`** — business logic; the only place that mutates models.
- **`templates/` + `static/`** — Jinja2, HTMX, Tailwind-style CSS, Chart.js.

### `helper/` — privileged helper
The only root component. It listens on `/run/ovpn-panel/helper.sock` (mode 660,
group `ovpn-panel`) and dispatches to a fixed allow-list. Every subprocess call
uses an argument list and never a shell string.

Allow-listed actions (`helper/actions.py`):

| Action                 | Purpose                                                     |
| ---------------------- | ----------------------------------------------------------- |
| `service`              | start/stop/restart/status of a fixed set of systemd units   |
| `agent_service`        | the same for `ovpn-panel-agent@<node_id>`                   |
| `instance`             | start/stop/restart/status of `openvpn-server@<name>`        |
| `read_config_file`     | read a file inside an allow-listed config root              |
| `write_config_file`    | atomically write a file inside an allow-listed config root  |
| `reload_firewall`      | restart `ovpn-panel-nat.service` to re-apply NAT rules      |
| `issue_client_certificate` / `revoke_client_certificate` | easy-rsa PKI |
| `write_auth_file`      | persist `username:argon2-hash` for the auth hook            |
| `generate_client_config` | render a validated client config                          |

Config-file paths are resolved and must stay inside `/etc/openvpn/instances`,
`/etc/ovpn-panel`, `/etc/openvpn/ccd` or one of a small set of explicitly
allowed files; `..` and symlink traversal are rejected after `resolve()`.

### OpenVPN management & hooks
- `app/services/ovpn.py` is a thin client for the OpenVPN management unix
  socket: live clients (`status 3`), byte counters, `kill <cn>` and `reload`
  (SIGHUP).
- The `auth-user-pass-verify` hook calls the local auth unix socket
  (`app/services/auth_socket.py`), which checks that the user is active, not
  expired, within quota and under the per-user concurrent-connection limit.
- `client-connect` opens a `vpn_sessions` row; `client-disconnect` closes it,
  records the final byte counters and appends a `traffic_logs` sample.

### `agent/` — node agent
A minimal FastAPI app on each node, authenticated with a per-node bearer token
(stored as an argon2 hash). It exposes status, client creation/revocation and
forced disconnect.

### `scripts/` — install, update, uninstall, CLI and OpenVPN hook wrappers.

## Security model

| Concern          | Measure                                                     |
| ---------------- | ----------------------------------------------------------- |
| Passwords        | argon2id, per-hash salt, rehash-on-login aware              |
| Sessions         | server-side rows in the DB, signed cookie, survives restart |
| CSRF             | double-submit token bound to the session                    |
| Login abuse      | sliding-window rate limiter keyed by `ip:username`          |
| Cookies          | `HttpOnly`, `SameSite=Lax`, `Secure` in production          |
| Shell injection  | strict regex validation + `subprocess` arg lists            |
| Privilege        | panel/agent never root; helper is root but allow-listed     |
| Secrets          | generated at install, `/etc/ovpn-panel/*.env`, mode 600     |
| Headers          | `X-Content-Type-Options`, `X-Frame-Options`, referrer policy |

## Database

SQLite is the default (`/var/lib/ovpn-panel/panel.sqlite3`) with WAL enabled.
PostgreSQL is supported by changing `OVPN_DATABASE_URL`; models and Alembic
migrations are database agnostic. A helper script converts an existing SQLite
database to PostgreSQL (`scripts/migrate-to-postgres.sh`).
