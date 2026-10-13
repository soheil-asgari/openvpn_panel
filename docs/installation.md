# Installation

Tested on Ubuntu 22.04 / 24.04; the installer also supports Debian, the RHEL
family (dnf/yum) and Alpine (apk).

## Quick start (one line, fresh server)

```bash
bash <(curl -fsSL https://github.com/soheil-asgari/openvpn_panel/releases/latest/download/install.sh)
```

This downloads the **pinned tagged release** (never a branch), verifies its
SHA256 from `SHA256SUMS` and aborts on mismatch, installs every prerequisite and
prints your panel URL and credentials. Nothing needs to be preinstalled.

> Do **not** install by cloning `main` — always use a tagged release so the code
> you run is exactly what was tested.

You can also run it fully non-interactively:

```bash
sudo ./scripts/install.sh --yes \
  --instances "udp1:udp:1194:10.8.0.0/24,tcp1:tcp:443:10.9.0.0/24" \
  --admin-user admin --admin-password 'S3cret-pw' --panel-port 8080 \
  --host vpn.example.com --dns 1.1.1.1 --mtu 1500
```

Pass `--no-openvpn` to install only the web panel (no OpenVPN core, PKI or
firewall changes).

## Re-runs, self-healing and rollback

The installer is **idempotent**: running it again on the same server reuses an
existing valid CA, server certificate, running instances and the panel database,
and never wipes user data. Incomplete parts (a PKI without a CA, a missing
server certificate or CRL, a half-written instance or systemd unit) are detected
and rebuilt automatically.

On **first-install** failure the installer rolls back the units and files it
created and prints what was rolled back; a second run then completes cleanly.
`install.sh repair` and `install.sh uninstall [--purge]` remain supported.

Known environment issues are fixed automatically instead of merely warned about:
NTP is enabled when the clock is not synchronised, the `tun`/`wireguard` kernel
modules are loaded, apt locks are waited on, and any active firewall (ufw,
firewalld or nftables) has the chosen ports opened.

## CI test seam (env-gated, for testing only)

The released `install.sh` can pull the release artifacts from a local directory
instead of GitHub via the `OVPN_RELEASE_BASE` environment variable using a
`file://` URL (e.g. `OVPN_RELEASE_BASE=file:///path/to/dist`). This seam is only
active when the variable is explicitly set, is never reachable in production,
and is what the end-to-end workflow (`.github/workflows/install-e2e.yml`) uses to
test the unmodified released installer against freshly built artifacts.

## What the installer does

1. **System packages** — python3/venv/pip, openvpn, easy-rsa, nftables, sqlite3,
   rsync, curl, openssl.
2. **PKI** — generates a CA and a server certificate with easy-rsa (falls back to
   `openssl` when easy-rsa is missing) under `/etc/openvpn/pki`, plus a
   `tls-crypt` key.
3. **Instances** — for each `name:proto:port:subnet` spec it writes
   `/etc/openvpn/instances/<name>.conf` (+ a small `<name>.env` metadata file)
   with its own port, protocol (UDP/TCP), subnet, pushed DNS and MTU, and starts
   via a systemd drop-in (`/etc/systemd/system/openvpn-server@.service.d/ovpn-panel.conf`)
   that overrides `WorkingDirectory` to `/etc/openvpn/instances` (Ubuntu's stock
   `openvpn-server@.service` expects config in `/etc/openvpn/server` otherwise).
   `openvpn-server@<name>.service`. Multiple UDP/TCP instances can coexist.
4. **Networking** — enables IP forwarding (persisted in
   `/etc/sysctl.d/99-ovpn-panel.conf`) and installs a `ovpn-panel-nat.service`
   oneshot unit that applies NAT (nftables, falling back to iptables) for every
   instance subnet on each boot. NAT rules are also written as an nftables
   fragment and loaded at boot when nftables is available.
5. **Firewall** — opens the OpenVPN ports and the panel port through ufw,
   firewalld or nftables, whichever is active.
6. **Authentication** — every instance uses username/password authentication via
   `auth-user-pass-verify`, which calls the panel over a **local unix socket**
   (`/run/ovpn-panel/auth.sock`). A shared CA is used so client certificates are
   not required (`verify-client-cert none`, `username-as-common-name`).
7. **Client template** — a `.ovpn` template is written to
   `/etc/ovpn-panel/client.ovpn.template` with `auth-user-pass` (inline
   credentials) and embedded CA + tls-crypt.
8. **Panel** — creates the `ovpn-panel` system user, a virtualenv, the systemd
   units, generated secrets (mode 600) and the database schema; asks for the
   admin username/password/port and prints the access information.

## Managing the panel

The installer installs an `ovpn-panel` menu at `/usr/local/bin/ovpn-panel`:

```
sudo ovpn-panel            # interactive menu
sudo ovpn-panel show       # print login / access information
sudo ovpn-panel set-port 9090
sudo ovpn-panel set-user admin2
sudo ovpn-panel set-password 'NewPassw0rd!'
sudo ovpn-panel add-instance "tcp2:tcp:8443:10.10.0.0/24"
sudo ovpn-panel remove-instance tcp2
sudo ovpn-panel restart
sudo ovpn-panel update
sudo ovpn-panel uninstall [--purge]
```

The menu covers: showing login info, changing the panel port/username/password,
adding/removing OpenVPN instances, restarting services, updating and
uninstalling.

## Reverse proxy

The panel binds to `127.0.0.1:<port>` (default `8080`). Put nginx or Caddy in
front and terminate TLS there:

```nginx
server {
    listen 443 ssl;
    server_name panel.example.com;

    ssl_certificate     /etc/letsencrypt/live/panel.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/panel.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Set `OVPN_TRUSTED_HOSTS=["panel.example.com"]` in `panel.env` once the proxy is
in place.

## OpenVPN integration details

Each instance configuration already contains the panel hooks:

```
management /run/openvpn/<name>.sock unix
script-security 2
auth-user-pass-verify /opt/ovpn-panel/scripts/hooks/auth-user-pass-verify.sh via-file
client-connect       /opt/ovpn-panel/scripts/hooks/client-connect.sh
client-disconnect    /opt/ovpn-panel/scripts/hooks/client-disconnect.sh
verify-client-cert none
username-as-common-name
duplicate-cn
```

The auth hook connects to the panel's unix socket; the connect/disconnect hooks
report accounting back to the panel's HTTP `/hooks/*` endpoints.

## Node agent

Install the agent on each additional node, then register the node in the panel
(or via the REST API). The agent token is shown only once at creation time;
write it to `/etc/ovpn-panel/agent.env` on the node as an argon2 hash:

```bash
python -c "from app.core.security import hash_password; print(hash_password('THE_TOKEN'))"
```

```
OVPN_AGENT_TOKEN_HASH=$argon2id$...
OVPN_AGENT_NODE_NAME=de1
```

## Updating

```bash
cd ovpn-panel && git pull
sudo ./scripts/update.sh
```

## Uninstalling

```bash
sudo ./scripts/uninstall.sh                 # keeps panel config, data, PKI, instances
sudo ./scripts/uninstall.sh --purge         # also removes panel config and data
sudo ./scripts/uninstall.sh --purge-openvpn # also removes PKI, instances, NAT and sysctl
```
