# Protocols

The panel manages several VPN/tunnel protocols through a single abstraction so
that user creation, limits, quota/expiry enforcement, traffic accounting and the
subscription page behave identically no matter which protocol a user is on.

Nothing in this module is copied from any commercial panel. Every backend is
written from scratch against the public upstream documentation, and upstream
programs are installed as external binaries/tools — no upstream source is linked
into the panel.

## The `ProtocolBackend` interface

Defined in [`app/services/protocols/base.py`](../app/services/protocols/base.py).
Every backend implements:

| Method | Purpose |
| --- | --- |
| `install()` | Install/upgrade the protocol server on a host. |
| `healthcheck()` | Report whether the instance is healthy. |
| `create_user(spec)` | Create the credential + enforcement state for a user. |
| `delete_user(username)` | Remove the user entirely. |
| `set_limits(spec)` | Apply quota/speed/expiry/concurrency limits. |
| `disable(username)` | Disable the user and drop live sessions. |
| `usage()` | Per-user traffic counters from the protocol. |
| `online()` | Currently connected users. |
| `config_for_user(spec)` | Render a client config and/or share link. |

Backends never touch the operating system directly: they receive an
[`Executor`](../app/services/protocols/executor.py) and pass it **structured
argv lists** (never shell strings built from untrusted input). Three executors
are provided:

* `HelperExecutor` — local host, through the privileged helper socket.
* `AgentExecutor` — remote node, through the node agent HTTP API.
* `LocalExecutor` — used inside the helper process and by tests.

## Backends

| Kind | Phase | Upstream | License | Notes |
| --- | --- | --- | --- | --- |
| `openvpn` | 12 | openvpn.net | GPL-2.0 | Reference implementation; wraps the management interface, easy-rsa PKI and auth file. |
| `wireguard` | 13 | wireguard.com | GPL-2.0 | Multiple interfaces (`wg0`, `wg1`, …); peers added/removed live with `wg set`; traffic from `wg show transfer`. |
| `ocserv` | 14 | gitlab.com/openconnect/ocserv | GPL-2.0 | Cisco AnyConnect; per-user passwords via `ocpasswd`, kick/disconnect via `occtl`. |
| `l2tp` | 14 | libreswan.org | GPL-2.0 | L2TP/IPsec; `chap-secrets` for users, `ipsec.secrets` for the PSK, ppp hooks for accounting. |
| `singbox` | 15 | github.com/SagerNet/sing-box | GPL-3.0-or-later | VMess/VLESS/Shadowsocks/Trojan/Hysteria2; Reality/TLS options; stats via the V2Ray API; share links + aggregated subscription. |

The sing-box upstream license is verified from the GitHub API at install time,
and the official release binary is downloaded and executed — see
[`SingBoxBackend._verify_license`](../app/services/protocols/singbox.py).

## Data model

Two tables (migration `0010_protocols`):

* `protocol_instances` — one configured endpoint per protocol on a node
  (e.g. `wg0`, a VLESS inbound). Carries port, subnet/address, DNS, MTU and a
  JSON `settings` blob (inbound type, security, SNI, Reality keys, PSK, …).
* `protocol_users` — per-user, per-instance credential + enable flag. A user may
  be enabled on several protocols at once; each row stores its own credential
  material (UUID, WireGuard keys, passwords) and its own traffic counters.

Per-user protocol enable/disable flags are managed from:

* the **VPN Users** page → *Protocols* button (HTMX partial), and
* the REST API under `/api/v1/protocols/users/{id}`.

## User lifecycle

`app/services/vpn_users.py` hooks into the protocol layer:

* creating a user provisions them on the node's **default** instances,
* disabling/expiring a user disables them on every protocol,
* deleting a user removes them from every protocol.

Every protocol operation that targets an unreachable node is persisted to the
`node_tasks` retry queue (`kind="protocol_user"`) and replayed by the scheduler.

## Traffic and enforcement

A background job (`sync_protocols`, every `ovpn_traffic_poll_interval` seconds)
pulls per-user counters and online state from every active instance and updates
the `protocol_users` rows and the aggregate counters on `vpn_users`. Quota and
expiry are enforced by the existing `enforce_limits` pass; when a limit is hit
the user is disabled on all protocols.

## Subscription page

`app/services/subscriptions.py` renders one `UserConfig` per enabled protocol.
The subscription page shows the per-protocol share links, a QR code and an
aggregated subscription link:

* `/sub/<token>/subscription.txt` — newline-joined share links.
* `/sub/<token>/protocol-qr.svg?kind=<kind>` — QR for one link (or the aggregate).

## API

All endpoints live under `/api/v1/protocols`:

* `GET /backends` — backend catalogue.
* `GET|POST /instances`, `GET|PATCH|DELETE /instances/{id}` — instance CRUD.
* `POST /instances/{id}/install`, `GET /instances/{id}/health`,
  `POST /instances/{id}/sync`.
* `GET /users/{id}`, `PUT|DELETE /users/{id}/{instance_id}` — per-user flags.
* `GET /users/{id}/configs`, `GET /users/{id}/links`, `POST /users/{id}/kick`.

## Adding a backend

1. Subclass `ProtocolBackend` in `app/services/protocols/<name>.py` and set the
   class metadata (`kind`, `display_name`, `upstream`, `license`,
   `capabilities`).
2. Implement the abstract methods using the injected `Executor`.
3. Register the class in `BACKENDS` in
   [`app/services/protocols/__init__.py`](../app/services/protocols/__init__.py).
4. Add a credential allocator branch in
   `protocols_service._credentials_for` if the protocol needs non-password
   material.
