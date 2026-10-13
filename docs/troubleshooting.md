# Troubleshooting

Logs first:

```bash
journalctl -u ovpn-panel -n 200 --no-pager
journalctl -u ovpn-panel-helper -n 100 --no-pager
journalctl -u 'ovpn-panel-agent@*' -n 100 --no-pager
```

## The panel does not start

- Check `/etc/ovpn-panel/panel.env` exists and is mode 600.
- Regenerate secrets with `sudo ovpn-panel gen-secrets --force` (rotates the
  session key and hook token; users must log in again).
- Confirm the database URL is reachable: `ovpn-panel init-db`.

## Login fails

- Verify the account exists: `ovpn-panel reset-password <user>`.
- Too many attempts trigger a sliding-window lockout keyed by `ip:username`;
  wait for the window to pass.
- Behind a reverse proxy, ensure `X-Forwarded-For` is honoured consistently so
  the limiter keys are stable.

## Clients cannot connect

- `auth-user-pass-verify` calls a local unix socket; verify the hook token
  matches `/etc/ovpn-panel/panel.env` and that `/run/ovpn-panel/auth.sock`
  exists.
- Check the user is active, not expired, within quota and below the
  concurrent-connection limit.
- If the user belongs to a sub-admin, confirm that account is active and not
  expired and that the node is in its allow-list.

## Node shows offline

- Confirm the agent is running: `curl -s localhost:8090/health`.
- Panel→agent calls are HMAC-signed. Re-issue the secret by re-running the
  install command from **Nodes → <node>** and restarting the agent.
- Pending user-sync operations are retried automatically; the **Retry queue**
  table on the node page shows attempts and the last error.

## Applying server.conf fails

The editor validates the file first. `plugin`, `up`/`down` and similar script
hooks are rejected, as are unbalanced inline blocks and shell metacharacters. If
the instance fails to restart, the previous configuration is restored
automatically (the result reports `rolled back=true`).

## HTTPS / certificates

- Let's Encrypt: run the printed `certbot` command on the host, then enable the
  renewal timer. Point your reverse proxy at the certificate paths shown in
  **Settings → TLS certificate**.
- Custom certificate: paste a PEM certificate and its matching private key; they
  are stored mode 600 under `/etc/ovpn-panel/tls/`.

## Database

- SQLite is fine for small deployments; enable PostgreSQL for many concurrent
  writes.
- Migrate with `ovpn-panel migrate-db --target 'postgresql+psycopg://user:pass@host/ovpn'`,
  then set `OVPN_DATABASE_URL` in `panel.env` and restart.
