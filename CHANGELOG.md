# Changelog

All notable changes are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-13

### Added
- Release-hardening: `THIRD_PARTY_LICENSES.md`, `docs/NAMING.md`,
  `docs/ROADMAP.md`, `docs/RESPONSIBLE_USE.md`, `docs/RELEASING.md`,
  `docs/TESTING.md`, `docs/THREAT_MODEL.md`, `docs/CLI.md`, legal disclaimer
  (EN + FA) and the installer disclaimer screen.
- One-line installer template (`scripts/release/install-template.sh`) and
  reproducible builder (`scripts/release/build-release.sh`): downloads the
  pinned tagged tarball, verifies SHA256 from `SHA256SUMS`, aborts on mismatch.
- Verification kit: `scripts/vps-smoke-test.sh`, `scripts/post-reboot-check.sh`,
  `scripts/tunnel-check.sh`, `scripts/seed_demo.py`, `docs/test-report-template.md`.
- CI: `release-gate` workflow (fails on `main`-branch install URLs and bundled
  tunnel binaries), gitleaks (working tree + full history) in CI and as a
  pre-commit hook, gitleaks config, version endpoint and panel version display.
- Original logo and banner (SVG) and an original color theme.

### Changed
- README (EN + FA) rewritten: one-line install at the top, badges, feature
  table, architecture diagram, FAQ, disclaimer.
- `docs/installation.md` no longer instructs cloning from a mutable branch.

### Security
- Documented the threat model, including what a relay/tunnel server can and
  cannot see.

## [0.2.0]

### Added
- Multi-node agents, resellers with limits, tunnels (Backhaul/WireGuard/sing-box),
  additional protocols (WireGuard, AnyConnect/ocserv, L2TP/IPsec, sing-box),
  REST API with scoped keys, encrypted backups, subscriptions, i18n (EN/FA).

## [0.1.0]

### Added
- Initial release: OpenVPN management, users, traffic accounting, expiry/quota,
  web UI, server-config editor, SSL helpers.
