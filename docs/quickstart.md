# First steps

After the one-line installer finishes, you have a working panel. This page
takes you from first login to a first working user, tunnel and subscription.

## 1. First login

1. Open the panel URL printed in `/root/<slug>-install-info.txt`
   (e.g. `http://<server-ip>:8080/`).
2. Log in with the admin username and password from that file.
3. **Immediately** go to **Settings → Password** and change the admin password,
   then delete `/root/<slug>-install-info.txt`.

## 2. Put the panel behind TLS

The panel binds to `127.0.0.1`. Terminate TLS with a reverse proxy (nginx or
Caddy) and set `OVPN_TRUSTED_HOSTS=["panel.example.com"]` in
`/etc/<slug>/panel.env`. See [installation.md](installation.md) and, for
automatic certificates, the **Settings → SSL** page (Let's Encrypt).

## 3. Create your first VPN user

1. Go to **Users → New user**.
2. Choose a username, set a data quota and an expiry date.
3. Copy the generated password (shown once) and the `.ovpn` config.
4. Share the config and credentials with the user.

## 4. Connect a client

Import the `.ovpn` file into the OpenVPN client on desktop or mobile, enter the
username/password, and connect. The user should appear under **Online** and the
traffic counters under **Users** should start moving.

## 5. Add a second server (multi-node)

1. Go to **Nodes → Add node**, then run the one-line node command shown on the
   node detail page on the second server.
2. The node reports health and metrics; manage its OpenVPN instances and config
   from **Node detail**.

## 6. Create a tunnel (entry ↔ exit)

1. Go to **Tunnels → New tunnel** (wizard).
2. Pick a backend and transport, enter the entry server's SSH details, confirm
   the host-key fingerprint, and add the public ports to forward.
3. Provision, then verify from both servers:
   `sudo scripts/tunnel-check.sh --peer <other-ip> --port <control-port>`.

## 7. Subscription page

Each user has a public page at `/sub/<token>` with usage, expiry, a QR code and
per-node `.ovpn` downloads. Regenerate the token any time from the user's row.

## 8. Backups

Set `OVPN_BACKUP_PASSWORD` in `/etc/<slug>/backup.env` to enable the daily
encrypted backup (see [`backup.md`](backup.md)). Restore with
`<slug> restore <archive>`.
