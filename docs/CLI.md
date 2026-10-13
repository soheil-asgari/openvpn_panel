# CLI reference

Two entry points are installed: the management menu (`<slug>`) and the Python
CLI (`<slug>` module / `scripts/cli.py` for service tasks). Commands that touch
the database or secrets must run as the service user or root.

## Management menu — `<slug>`

Installed at `/usr/local/bin/<slug>`. Interactive when run with no arguments.

| Command | Description |
| --- | --- |
| `<slug>` | Interactive menu |
| `<slug> show` | Print login / access information |
| `<slug> set-port PORT` | Change the panel port (updates the unit + firewall) |
| `<slug> set-user NAME` | Change the admin username |
| `<slug> set-password [PW]` | Change the admin password (random if omitted) |
| `<slug> add-instance SPEC` | Add an OpenVPN instance `name:proto:port:subnet[:dns[:mtu]]` |
| `<slug> remove-instance NAME` | Remove an OpenVPN instance |
| `<slug> restart` | Restart panel, helper and OpenVPN services |
| `<slug> update` | Update the panel from its source |
| `<slug> uninstall [--purge]` | Uninstall (keep data, or `--purge` everything) |

## Service CLI — `<slug>` (Python)

Used by the installer, units and operators.

| Command | Description |
| --- | --- |
| `init-db` | Create/upgrade the database schema |
| `create-admin USER [--password PW]` | Create an initial admin (random password if omitted) |
| `reset-password USER [--password PW]` | Reset a panel user's password |
| `gen-secrets --output FILE --database-url URL` | Write secrets to a mode-600 file |
| `import-auth` | Export VPN users to OpenVPN's auth file via the helper |
| `backup [--output DIR] [--no-pki]` | Create an encrypted backup archive |
| `restore ARCHIVE [--configs]` | Restore from an encrypted archive |
| `migrate-db --target URL` | Copy all rows to another database (e.g. PostgreSQL) |
| `tunnel-list` | List tunnels and their state |
| `tunnel-add ...` | Create and provision a tunnel (wizard equivalent) |
| `tunnel-action NAME ACTION` | `status` / `restart` / `update` / `uninstall` a tunnel |
| `tunnel-mtu NAME [--apply]` | Run the MTU finder (optionally apply to the instance) |

## One-line installer

| Command | Description |
| --- | --- |
| `install.sh` | Install (or repair) on a fresh server |
| `install.sh update` | Update to the installer's release, keeping data |
| `install.sh repair` | Re-apply units, permissions and firewall |
| `install.sh status` | Print service and panel status |
| `install.sh uninstall [--purge]` | Uninstall, optionally deleting data |
| `install.sh --version` | Print the installer version |

Flags: `--yes --user --password --port --path --domain --email`.

## Verification helpers

| Script | Purpose |
| --- | --- |
| `scripts/vps-smoke-test.sh` | Post-install PASS/FAIL smoke test (writes a redacted report) |
| `scripts/post-reboot-check.sh` | Verify everything survived a reboot |
| `scripts/tunnel-check.sh` | Verify a two-server tunnel end to end |
| `scripts/seed_demo.py` | Populate a demo instance with fake data (screenshots) |
