# REST API

Base URL: `https://panel.example.com/api/v1`

Authenticate with a personal API key, created in **Settings → API keys**:

```
Authorization: Bearer ovpn_<prefix>_<secret>
```

or

```
X-API-Key: ovpn_<prefix>_<secret>
```

Interactive documentation is available at `/api/docs` (OpenAPI JSON at
`/api/openapi.json`). A copy of the reference lives in [docs/api.md](api.md)
and the versioned surface is under `/api/v1`.

## Scoping

- **admin** keys can manage all VPN users, nodes and resellers.
- **reseller** keys only see and modify VPN users they own; node and reseller
  endpoints return `403`.

## Endpoints

| Method | Path                            | Description                          |
| ------ | ------------------------------- | ------------------------------------ |
| GET    | `/users`                        | list VPN users (filter/sort/page)    |
| POST   | `/users`                        | create a VPN user                    |
| GET    | `/users/export`                 | download the listing as CSV          |
| GET    | `/users/online`                 | list currently online users          |
| POST   | `/users/bulk`                   | bulk create (prefix + count)         |
| POST   | `/users/bulk/delete`            | bulk delete                          |
| POST   | `/users/bulk/enable`            | bulk enable                          |
| POST   | `/users/bulk/disable`           | bulk disable (kicks sessions)        |
| POST   | `/users/bulk/extend`            | bulk extend expiry (`days`)          |
| POST   | `/users/bulk/reset-traffic`     | bulk reset traffic counters          |
| GET    | `/users/{id}`                   | fetch a VPN user                     |
| PATCH  | `/users/{id}`                   | update limits/expiry/status          |
| DELETE | `/users/{id}`                   | delete a VPN user                    |
| POST   | `/users/{id}/reset-password`    | generate a new random password       |
| POST   | `/users/{id}/reset-traffic`     | reset the user's traffic counters    |
| POST   | `/users/{id}/kick`              | disconnect all active sessions       |
| GET    | `/users/{id}/subscription`      | subscription link + node visibility  |
| POST   | `/users/{id}/subscription/token` | regenerate the subscription token   |
| PUT    | `/users/{id}/subscription/visibility` | set the visible node IDs       |
| GET    | `/nodes`                        | list nodes                           |
| POST   | `/nodes`                        | create a node (admin)                |
| PATCH  | `/nodes/{id}`                   | update a node (admin)                |
| DELETE | `/nodes/{id}`                   | delete a node (admin)                |
| GET    | `/nodes/{id}/install-command`   | one-line node-agent install command  |
| GET    | `/nodes/{id}/health`            | latest health sample + queue depth   |
| POST   | `/nodes/{id}/reconcile`         | push all users to the node           |
| GET    | `/nodes/{id}/config`            | read the instance config             |
| PUT    | `/nodes/{id}/config`            | write the instance config            |
| GET    | `/nodes/{id}/tasks`             | node retry-queue entries             |
| POST   | `/nodes/{id}/tasks/retry`       | re-schedule failed/pending tasks     |
| GET    | `/resellers`                    | list sub-admins (admin)              |
| POST   | `/resellers`                    | create a sub-admin (admin)           |
| GET    | `/resellers/{id}`               | fetch a sub-admin (admin)            |
| PATCH  | `/resellers/{id}`               | update limits/expiry/nodes (admin)   |
| DELETE | `/resellers/{id}`               | delete a sub-admin (admin)           |
| GET    | `/resellers/{id}/usage`         | sub-admin usage & remaining limits   |
| GET    | `/reports`                      | usage series (daily/weekly/monthly/custom) |
| GET    | `/keys`                         | list your API keys                   |
| POST   | `/keys`                         | create an API key                    |

### Listing: search, filter, sort, pagination

`GET /users` accepts `search`, `status` (`active`/`disabled`/`expired`/`online`/
`offline`), `node_id`, `sort_by` (`username`, `status`, `created_at`,
`expires_at`, `bytes_in`, `bytes_out`, `last_connected_at`), `sort_dir`
(`asc`/`desc`), `limit` and `offset`.

### API keys (scoped, revocable)

Keys are created under **Settings → API keys** or via `POST /keys`. The raw
secret is returned **once**; only its argon2 hash is stored.

```bash
# create a key limited to reading users and reports
curl -s -X POST https://panel.example.com/api/v1/keys \
  -H "Authorization: Bearer $OVPN_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"name":"ci","scopes":["users:read","reports:read"]}'

# rotate / revoke
curl -s -X POST .../api/v1/keys/3/rotate -H "Authorization: Bearer $OVPN_API_KEY"
curl -s -X POST .../api/v1/keys/3/revoke -H "Authorization: Bearer $OVPN_API_KEY"
```

Supported scopes: `users:read`, `users:write`, `nodes:read`, `nodes:write`,
`resellers:read`, `resellers:write`, `reports:read`, `admin`. An empty scope set
inherits the owner's full permissions. Keys may also carry an `expires_at`.

### Stats

`GET /stats` returns aggregate figures scoped to the caller: totals, online
users, node count, total bytes, per-node CPU/RAM/disk/network and the top users.

`GET /version` returns the installed panel version and, **only if the operator
enabled it**, the result of an optional update check (`update_check_enabled`,
`latest`, `update_available`, `url`). The check is off by default and sends no
telemetry — it merely reads the project's public releases when enabled in
**Settings → Panel**.

### Usage, renew and online

- `GET /users/{id}/usage` — current byte counters and limits.
- `POST /users/{id}/renew` — `{"days": 30}` or `{"expires_at": "..."}`, with an
  optional `"reset_traffic": true`.
- `GET /users/online` — the online list.

### Per-user limits

`PATCH /users/{id}` (and `POST /users`) accept:

- `data_limit_bytes` — data quota in bytes (omit for unlimited).
- `expires_at` — fixed expiry date, **or** `expire_days` — expiry counted from
  the first successful connection.
- `max_connections` — per-user concurrency limit (`0` = unlimited; omit to use
  the global setting).
- `speed_limit_kbps` — optional per-user speed cap.

### Example

```bash
curl -s -X POST https://panel.example.com/api/v1/users \
  -H "Authorization: Bearer $OVPN_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"s3cret-pass","data_limit_bytes":10737418240}'
```

## Subscriptions

Each VPN user can have a public subscription URL, `https://panel.example.com/sub/<token>`,
that shows usage, expiry, a QR code and a `.ovpn` download per visible node.
The page and download endpoints are unauthenticated — the token is the
credential — and only expose the single user the token resolves to.

- `GET /users/{id}/subscription` returns the URL plus the visible/hidden node IDs.
- `POST /users/{id}/subscription/token` rotates the token (the old URL stops working).
- `PUT /users/{id}/subscription/visibility` with `{"node_ids": [1, 2]}` controls
  which nodes appear on the page.

Branding (name, logo, support URL, footer) for the public page is configured in
**Settings → Subscription page branding** and stored in the `settings` table.

## Nodes (multi-node)

Each node runs a lightweight agent. The panel shows a one-line install command
(`GET /nodes/{id}/install-command`) that installs the agent, writes the shared
secret to `/etc/ovpn-panel/agent.env` (mode 600) and starts
`ovpn-panel-agent@<name>`.

Panel→agent calls are authenticated with **HMAC-SHA256** over
`method\npath\ntimestamp\nbody` (`X-OVPN-Signature` / `X-OVPN-Timestamp`); a
shared-secret bearer token remains supported for hand-provisioned nodes.

- User limits/enable-disable are pushed on change and reconciled when a node
  comes back online; failures are persisted to a **retry queue** (`node_tasks`)
  and replayed with backoff.
- `/nodes/{id}/health` returns the latest CPU/RAM/disk/network/client sample and
  the pending queue depth. The scheduler polls every node and auto-reconciles
  once it is reachable.
- The per-node instance config is editable via `GET`/`PUT /nodes/{id}/config`
  (also written to the agent).

## Resellers (sub-admins)

Create sub-admins with `POST /resellers`, setting `quota_users` (max VPN users),
`max_data_bytes` (aggregate data quota across their users), `expires_at`
(account expiry) and `node_ids` (node allow-list; omit to leave unrestricted).

Limits are enforced **server-side on every action**: creating users, assigning
them to nodes and authenticating OpenVPN connections all re-check the owner's
account state, user quota, aggregate data quota and node allow-list. A disabled
or expired sub-admin's users cannot connect.

`GET /reports` returns a usage series for the caller:

- `preset` — `daily` (14 days), `weekly` (8 weeks), `monthly` (6 months) or
  `custom` (requires `start`/`end` ISO dates).
- Admins may target a sub-admin with `owner_id`; resellers are always scoped to
  their own users.

The web UI mounts these under `/reports`, `/resellers`, `/permissions` (the
permission matrix) and `/audit` (the audit log of admin/sub-admin actions).

## Hook endpoints

`POST /hooks/auth`, `/hooks/connect` and `/hooks/disconnect` are called by the
OpenVPN wrapper scripts. They require the `X-Hook-Token` header (generated at
install) and must only be reachable from localhost.
