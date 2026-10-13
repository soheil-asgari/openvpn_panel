# Roadmap

Short-term work is tracked here. Items are moved out of source code comments
(`TODO`/`FIXME`) during the release-hardening pass so the code stays clean.

## Planned

- **Opt-in update check.** A settings toggle plus a "Check for updates" button
  that queries the project's *own* GitHub releases API **only when the operator
  enables it**. Off by default; no request is made otherwise. (Deferred until
  the final project name/org/repo are chosen, so no repository URL is hardcoded.)
- **Named tunnel transports**: surface each backend's transport matrix in the
  wizard with presets and a re-run of the MTU finder after transport changes.
- **Reseller self-service portal** (scoped UI, separate from the admin panel).
- **PostgreSQL migration helper** as a first-class installer subcommand.
- **Per-node audit streaming** to an external syslog/webhook for SIEM use.
- **ARM64 release artifacts and CI matrix** for the tunnel backends' prebuilt
  binaries.

## Under consideration

- Additional subscription output formats (Clash / sing-box JSON) for the public
  subscription page.
- Backup to S3-compatible object storage (in addition to Telegram/webhook).
- Read-replica support for large multi-node deployments.

## Out of scope (by design)

- License keys, activation, "pro" gating or any paid tier.
- Telemetry or phone-home of any kind.
- Bundling GPL/AGPL tunnel binaries inside release artifacts (see
  [`THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md)).
