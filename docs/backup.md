# Backup and restore

The panel can produce a single **encrypted** archive containing the database,
the panel/OpenVPN configuration and (optionally) the easy-rsa PKI. Archives use
a stdlib implementation (PBKDF2-HMAC-SHA256 + encrypt-then-MAC) so no extra
crypto dependency is required. **Keep the password safe** — there is no recovery.

## From the web UI

**Settings → Backups** lets an admin:

- choose a schedule (`off` / `daily` / `weekly`) and an upload target
  (`none` / `telegram` / `webhook`),
- download an encrypted archive immediately (enter an encryption password).

Scheduled backups run from the panel's background scheduler (hourly tick) and
are written to `/var/lib/ovpn-panel/backups/`. When Telegram/webhook upload is
configured, the same archive is pushed remotely and a notification is sent.

## From the CLI

```bash
# create (prompts are avoided; pass the password explicitly)
ovpn-panel backup --password 'S3cret-passphrase' --output /root/ovpn-backup.ovpnbak

# exclude the PKI (smaller archive)
ovpn-panel backup --password 'S3cret-passphrase' --no-pki

# inspect / restore
ovpn-panel restore /root/ovpn-backup.ovpnbak --password 'S3cret-passphrase'
ovpn-panel restore /root/ovpn-backup.ovpnbak --password 'S3cret-passphrase' --configs
```

`OVPN_BACKUP_PASSWORD` may be exported instead of passing `--password`.

## What a restore does

For safety, a restore **does not** overwrite a live database or PKI in place:

- the SQLite snapshot is written next to the database as
  `<database>.restored` (mode 600) so you can inspect and swap it in during a
  maintenance window;
- configuration files are only written when `--configs` / `restore_configs` is
  explicitly requested, and only to allow-listed destinations.

## Verification

```bash
head -c 8 /root/ovpn-backup.ovpnbak   # b"OVPNBK1\0"
```

A wrong password or a truncated archive fails authentication and is rejected
without writing anything.
