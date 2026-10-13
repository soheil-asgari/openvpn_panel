# Tunnel Manager

The Tunnel Manager provisions and operates **reverse/forward tunnels** that sit
in front of your OpenVPN instances. Users connect to an *entry* host; the entry
host forwards their traffic through the tunnel to the *exit* host where OpenVPN
actually listens. Everything — install, configure, start/stop, MTU discovery,
health, updates and uninstall — is driven from the web UI (the CLI mirrors it).

> See [`TUNNEL_RESEARCH.md`](TUNNEL_RESEARCH.md) for the upstream tool survey and
> license analysis. Confirmed backends: **Backhaul** (AGPL-3.0) and **WireGuard**
> (GPL-2.0). Both are downloaded and executed as *external programs*; no upstream
> code is copied or linked into the panel.

## Concepts

| Term | Meaning |
|------|---------|
| **Entry host** | Public server users reach. The panel SSHes here and installs the backend. |
| **Exit host** | Where OpenVPN listens. Traffic is forwarded here by the tunnel. |
| **Backend** | Program that terminates the tunnel: `backhaul` or `wireguard` (or `auto`). |
| **Transport** | `tcp`, `tcpmux`, `udp` (Backhaul) or `wg` (WireGuard). `auto` picks `tcpmux`/`wg`. |
| **Control port** | The port the two tunnel endpoints use to talk to each other. |
| **Public ports** | Ports exposed on the entry host and forwarded to OpenVPN. |
| **Attached instance** | The OpenVPN instance (`openvpn-server@<name>`) this tunnel fronts. |

## 1. Add a tunnel (UI wizard)

Open **Tunnels → Add tunnel**. The wizard collects everything it needs; **no
manual SSH or config editing is required**.

1. **Entry server** — host/IP, SSH port, username, and either a **password** or a
   **private key**. The password is held in memory for the single provisioning
   run and then discarded; the panel installs its own key for future runs. Tick
   *Disable password login after install* to harden the entry host.
2. **Host key fingerprint** — click **Probe fingerprint** to read the entry
   host's SSH key **without sending any credential**, then **Confirm** it. The
   fingerprint is pinned and every later connection is verified against it
   (MITM protection).
3. **Backend & transport** — choose `auto` (recommended) or a specific backend.
   **Test & benchmark** runs latency/loss/throughput probes and recommends the
   best option; you may always override it.
4. **Attach** — pick the **OpenVPN instance**/node, the **public ports** to
   expose, an optional MTU, and whether to enable **auto-heal**.

Submitting starts provisioning in the background. The detail page streams a
**live progress log** of each step:

```
verify_fingerprint → connect → install_key → install_backend → configure
→ mtu → start → healthcheck
```

Each run is recorded as a *provision job* with a retry button; **Rollback**
uninstalls everything the run created.

## 2. Auto mode (recommended backend)

For `backend=auto` and `transport=auto` the panel:

1. probes connectivity to the entry host (ICMP),
2. discovers the path **MTU** with DF-bit pings,
3. benchmarks **latency / packet loss / throughput** per candidate,
4. recommends the best `backend:transport`, unless you override it.

`tcpmux` is preferred for Backhaul (multiplexing + UDP support); `wg` is chosen
when you explicitly select the WireGuard transport/backend.

## 3. MTU tool

On a tunnel's page, **Find best MTU** runs DF-bit ping discovery (on the entry
host when reachable) and shows the recommended `tun-mtu` + `mssfix`. **Apply to
OpenVPN instance** writes `tun-mtu`/`mssfix` into the attached instance's config
through the privileged helper, restarting the instance with automatic rollback
on failure (same validate-write-restart-rollback path as the server-config
editor).

## 4. Tunnel dashboard

Each tunnel page shows:

- status badge (active / degraded / provisioning / error / stopped),
- latency, packet-loss and throughput from the latest probe,
- a **health history** chart,
- the backend version with an **Update** button,
- **start / stop / restart / update / rollback** actions,
- the live **provisioning/progress log**.

## 5. Config delivery

Attach a tunnel to a node so the node's clients are advertised via the entry
host:

- Per user, on the **Subscription** panel, tick
  **Deliver configs through the tunnel**. Rendered `.ovpn` files and the public
  subscription page then use `entry_host:public_port` instead of the exit
  host/port. Individual users can opt **direct vs tunnel**.
- Nodes without an active tunnel keep advertising their own endpoint.

## 6. Health & self-healing

A scheduled job (`tunnel_health`) probes every active tunnel end-to-end:

- results are stored as health samples (dashboard charts),
- after **3 consecutive failures** the tunnel is marked *degraded* and, when
  **auto-heal** is on, automatically restarted,
- alerts are sent through the configured [notification](installation.md)
  channels (Telegram/webhook) on self-heal, recovery, or heal failure.

## 7. Offline install

When GitHub is unreachable, upload a backend binary from the panel
(**Offline install** on the tunnel page). The panel SFTPs the binary to the
entry host, installs it with mode `0755`, and starts the tunnel — no outbound
internet needed on the entry host.

## 8. Security

- **Strict validation** of every field (host, name, ports, instance name) via
  `app.core.validators` before anything reaches a subprocess or the filesystem.
- **Allow-listed remote templates** — all remote commands are built in
  `app.services.tunnel_backend` from fixed templates with shell-quoted arguments;
  the SSH layer never receives an assembled string.
- **Random secrets** — tokens and WireGuard keys are generated with
  `secrets.token_urlsafe`; the panel's SSH key and backend configs are written
  with **mode 600**.
- **Password used once, in memory only** — never written to the database, the
  job log, or disk.
- **Host-key pinning** — the confirmed SHA256 fingerprint is enforced on every
  connection.
- **Audit log** — every create/update/delete/action/provision is recorded in
  `audit_logs`.
- **Rate-limited provisioning API** — at most 6 provisioning requests per minute
  per actor/IP (`app.services.tunnel_worker`).

## 9. CLI (mirrors the UI)

```bash
ovpn-panel tunnel-list
ovpn-panel tunnel-add de1 198.51.100.5 --password '…' \
  --backend backhaul --transport tcpmux --ports 443,8443 \
  --fingerprint SHA256:… --node-id 1 --instance server
ovpn-panel tunnel-action de1 restart      # start|stop|restart|update|uninstall
ovpn-panel tunnel-mtu de1 --apply
```

## 10. REST API

All endpoints live under `/api/v1/tunnels` (admin-scoped, JSON):

| Method & path | Purpose |
|---------------|---------|
| `GET /tunnels` | list tunnels |
| `GET /tunnels/backends` | backend catalogue (transports, license, upstream) |
| `GET /tunnels/summary` | status counts |
| `POST /tunnels/fingerprint?host=&port=` | probe a host key |
| `POST /tunnels` | create (+ provision; `probe_only` to skip) |
| `GET /tunnels/{id}` | tunnel detail + latest job |
| `PATCH /tunnels/{id}` | edit transport/ports/token/MTU/instance/auto-heal |
| `DELETE /tunnels/{id}` | delete |
| `POST /tunnels/{id}/rotate-token` | rotate the shared token |
| `POST /tunnels/{id}/{start,stop,restart,update,retry,rollback}` | operations |
| `GET /tunnels/{id}/jobs` | provisioning history |
| `GET /tunnels/{id}/logs` | backend journal tail |
| `GET /tunnels/{id}/health` | health samples |
| `POST /tunnels/{id}/mtu?apply=` | discover/apply MTU |
| `POST /tunnels/benchmark?host=` | benchmark candidates |
| `POST /tunnels/{id}/offline-install` | upload a backend binary |

## 11. Tests

- **Unit (mocked SSH)** — `tests/test_tunnels.py`, `tests/test_tunnels_api.py`.
  A `FakeSSH` transport records commands so the allow-listed templates, secret
  handling, provisioning steps, self-healing and config delivery are verified
  without touching a host.
- **Integration (two containers)** —
  `tests/integration/test_tunnel_two_containers.py` brings up `entry` + `exit`
  containers (`tests/integration/docker-compose.yml`), provisions a real Backhaul
  tunnel over SSH and asserts the public port forwards to the exit echo server:

  ```bash
  OVPN_TUNNEL_INTEGRATION=1 python -m pytest tests/integration -m integration
  ```

## 12. Dependencies

The SSH provisioning uses the optional `paramiko` + `cryptography` extra:

```bash
pip install 'ovpn-panel[tunnels]'
```

Without them the Tunnel Manager reports a clear error and the rest of the panel
is unaffected.
