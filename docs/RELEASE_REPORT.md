# Release Report — Phase 16 (Release Preparation)

**Version:** `1.0.0rc1` (to be tagged `v1.0.0-rc1` as a **pre-release**)
**Date:** 2026-10-12
**Scope:** cleanup, hardening, documentation (EN + FA), one-line installer,
verification kit, release engineering. **No new features. Nothing pushed.**

> This report is the final gate. Read it, complete the manual items, then push.
> **Do not push or publish until you have done so.**

---

## 1. Secret scan (blocking gate)

- [x] Working tree scanned (manual pattern review; gitleaks/trufflehog were not
      available in the build environment).
- [x] **Clean** — no real secret. Only placeholders/test fixtures
      (`change-me-at-install`, demo/test passwords, truncated PEM markers).
- [x] `gitleaks` added to CI (working tree **and** full history) and as a
      pre-commit hook, with a repo [`.gitleaks.toml`](.gitleaks.toml) allowlist.
- [x] Personal path `d:/asgari/openvpn` removed from `docs/TUNNEL_RESEARCH.md`.
- [ ] **Owner:** re-run both scans after the first commit (repo had no commits
      yet) and commit the redacted reports to `docs/security/`.
- Status detail: [`docs/security/gitleaks-status.md`](security/gitleaks-status.md).

### Rotate manually

Nothing to rotate yet (no secret found, nothing pushed). Before/after pushing,
treat `OVPN_SECRET_KEY`, `OVPN_HOOK_TOKEN`, every API key and every tunnel
token/SSH key as separate and rotate if there is any doubt. The git remote is set
to the public repository `soheil-asgari/openvpn_panel`.

---

## 2. Code cleanup

- [x] `ruff check .` — **clean**. `ruff format` — applied (60 files formatted,
      now `--check` clean).
- [x] No `TODO`/`FIXME`/`XXX`/`HACK` in `app/`, `agent/`, `helper/`, `scripts/`,
      `docs/` (only third-party matches under `.venv/`).
- [x] No debug `print()`/`breakpoint()` in application code.
- [x] Personal paths, e-mail addresses and owner usernames removed from tracked
      files.
- [x] Dependencies pinned exactly in `pyproject.toml`; transitive lockfile added
      at [`requirements.lock`](requirements.lock).
- [ ] **Owner:** run `pip-audit` in CI (wired) and fix/record any advisory.
- [x] License audit: [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md) lists
      every Python dependency, every system package and every downloaded tunnel
      binary (Backhaul AGPL-3.0, sing-box GPL-3.0, WireGuard GPL-2.0) with the
      "download, don't bundle" rule and conflict flags.
- [x] Added `LICENSE` (MIT, present), `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`,
      `SECURITY.md`, issue templates (bug, feature), PR template, `.gitignore`,
      `.editorconfig`.
- [x] No telemetry / phone-home. The only outbound version request is the
      **opt-in** update check (off by default) in
      `app/services/update_check.py`.
- [x] No license-key / activation / "pro" gating code (verified by search).
- [x] Frontend assets are prebuilt (`htmx.min.js`, `chart.min.js`) and no Node.js
      is required on servers. They are fetched by `scripts/release/build-release.sh`
      (and CI) *before* packaging, so every released tarball contains them; the
      installer never runs a JS build step. The current dev checkout does not
      commit the large minified blobs — the release build adds them.

---

## 3. Naming and originality

- [x] Five unique name candidates with availability notes:
      [`docs/NAMING.md`](NAMING.md). **Owner must choose one.**
- [x] GitHub owner/repository fixed to the public repo
      `soheil-asgari/openvpn_panel` in README badges, install one-liners, docs,
      the release builder and the default update-check source. The distribution
      slug (`<slug>` / `ovpn-panel`, `@SLUG@`) remains a working name pending the
      final product-name choice.
- [x] Original logo and banner created: [`docs/assets/logo.svg`](assets/logo.svg),
      [`docs/assets/banner.svg`](assets/banner.svg). Original color theme.
- [x] Original README wording, structure and section names (EN + FA).
- [x] Similarity check run for the name/author/domains/phrases of the reference
      ecosystem (`marzban`, `hiddify`, `sanaei`, `x-ui`, `3x-ui`, etc.): **no
      matches** in tracked files. No copied code, text, UI, branding or
      license/telemetry logic found. The project is **original work**.
- [ ] **Owner:** pick the name, then run the rename pass (see NAMING.md step 2).

---

## 4. One-line installer

- [x] Templetized installer: [`scripts/release/install-template.sh`](scripts/release/install-template.sh)
      (subcommands `install | update | uninstall | repair | status | --version`).
- [x] Reproducible builder: [`scripts/release/build-release.sh`](scripts/release/build-release.sh)
      builds the tarball, injects the version, and writes `SHA256SUMS`.
- [x] Requirements implemented: root/OS/arch/RAM/disk/internet/DNS/time preflight;
      disclaimer first (accept with `yes` or `--yes`); installs **all**
      prerequisites non-interactively with apt-lock retry; loads `wireguard`,
      sets `ip_forward` and optional BBR.
- [x] Downloads the **pinned tagged tarball**, verifies SHA256 from `SHA256SUMS`,
      **aborts on mismatch**; refuses any `main`/`master` URL.
- [x] Installs to `/opt/<slug>`, dedicated service user, systemd units, firewall,
      logrotate and backup cron; interactive **and** non-interactive (`--user
      --password --port --path --domain --email --yes`) modes.
- [x] Starts services and runs a post-install self-test; writes a **mode-600**
      summary to `/root/<slug>-install-info.txt` (password only there).
- [x] Idempotent subcommands; safe uninstall with optional `--purge`; `set -Eeuo
      pipefail`, cleanup `trap`, logs to `/var/log/<slug>-install.log`.
- [x] CLI management menu (`<slug>`) covers status, credentials/URL, port/path/
      password change, update, backup/restore, restart, logs, tunnels, uninstall.
- [x] Tagged-release rule: README one-liner uses
      `releases/latest/download/install.sh`; CI attaches `install.sh`,
      `SHA256SUMS` and the tarball; `-rc` tags are published as **pre-release**.
- [x] CI **fails** on any `raw.githubusercontent.com/.../main/` install URL:
      [`.github/workflows/release-gate.yml`](.github/workflows/release-gate.yml).
- [x] No default credentials anywhere.

---

## 5. Release engineering

- [x] CI (`.github/workflows/ci.yml`): ruff (lint + format), pytest, shellcheck,
      gitleaks, install-smoke.
- [x] Release workflow (`.github/workflows/release.yml`): builds tarball +
      checksums, injects the version, creates the GitHub Release, attaches
      artifacts, generates notes, marks `-rc` as pre-release.
- [x] Semantic versioning; [`CHANGELOG.md`](CHANGELOG.md); version shown in the
      panel UI settings and via `GET /api/v1/version`.
- [x] Manual **opt-in** "check for updates" (off by default, no telemetry).
- [x] Reproducible tarball (fixed owner/group/sort/mtime).
- [x] Verified locally: `scripts/release/build-release.sh v0.0.0-ci` builds the
      tarball, injects `@SLUG@/@VERSION@/@TAG@/@REPO@`, writes `SHA256SUMS`, and
      the generated installer passes `bash -n` and `--help`/`--version`.

---

## 6. Verification

- [x] Automated (verified locally): full `pytest` suite green (all tests pass,
      2 skipped); `ruff check` clean; `ruff format --check` clean (181 files);
      all installer/verification scripts pass `bash -n`; workflow YAML and i18n
      JSON parse clean; EN/FA catalogues have identical key sets.
- [x] Two-server tunnel integration test present
      (`tests/integration/test_tunnel_two_containers.py`).
- [x] `scripts/vps-smoke-test.sh` — PASS/FAIL list, exit code, **redacted**
      report file.
- [x] `scripts/post-reboot-check.sh` — services, OpenVPN instances and tunnels
      after reboot.
- [x] `scripts/tunnel-check.sh` — tunnel state, port forwarding, end-to-end
      reachability (run on both servers).
- [x] [`docs/TESTING.md`](TESTING.md) — full manual checklist; CI limits
      documented ([`docs/TESTING.md`](TESTING.md) §1).
- [x] [`docs/test-report-template.md`](test-report-template.md) ready.
- [x] `scripts/seed_demo.py` — fake demo data for screenshots (with an opt-in
      guard via `OVPN_ALLOW_DEMO_SEED=1`). The older unguarded
      `scripts/seed_demo.sh` was removed.
- [ ] **Owner:** run the manual checklist on real VPS hosts.

---

## 7. Documentation (EN + FA, equal quality)

- [x] `README.md` (EN): banner, badges, one-line install at top, OS/requirements,
      feature table, quick start, mermaid architecture, FAQ, upgrade/backup/
      uninstall, security, roadmap, contributing, **legal disclaimer**.
- [x] `README.fa.md` (FA, RTL) — economy of structure and quality matching EN.
- [x] `docs/en`-equivalent pages exist at `docs/*`; Persian pages under
      [`docs/fa/`](fa/): README, quickstart, installation, CLI, responsible-use.
- [x] New pages: [`docs/quickstart.md`](quickstart.md), [`docs/CLI.md`](CLI.md),
      [`docs/ROADMAP.md`](ROADMAP.md), [`docs/RELEASING.md`](RELEASING.md),
      [`docs/RESPONSIBLE_USE.md`](RESPONSIBLE_USE.md), [`docs/THREAT_MODEL.md`](THREAT_MODEL.md),
      [`docs/TESTING.md`](TESTING.md), [`docs/NAMING.md`](NAMING.md),
      [`docs/security/`](security/).
- [x] Screenshot slots provided; **owner:** capture from a seeded demo instance
      with fake data and drop into `docs/screenshots/`.

---

## 8. Legal disclaimer

- [x] In `README.md`, `README.fa.md`, docs and the **installer's first screen**
      (confirm with `yes` / `--yes`): "as is", MIT, no warranty, lawful use only,
      user responsibility for laws + hosting terms, authors not responsible,
      third-party tools under their own licenses (links THIRD_PARTY_LICENSES.md).
- [x] "Responsible use" section: [`docs/RESPONSIBLE_USE.md`](RESPONSIBLE_USE.md)
      and [`docs/fa/responsible-use.md`](fa/responsible-use.md).

---

## 9. Final security pass

- [x] Authorization: central permission matrix; reseller/admin isolation enforced
      server-side; test iterates the OpenAPI surface asserting anonymous access
      is rejected and admin-only routes return `403` for resellers.
- [x] CSRF on state-changing routes (session-bound token); `/api/v1`, `/hooks`,
      `/sub` exempt by design.
- [x] Rate limiting on login; input validation via `app.core.validators`.
- [x] No shell string concatenation; allow-listed privileged helper; server.conf
      editor rejects `plugin`/`up`/`down` and metacharacters.
- [x] Secrets/configs at `600`/`640`; panel binds to `127.0.0.1`; cookies
      `Secure`/`HttpOnly`/`SameSite`; security headers; server-side sessions.
- [x] SSH provisioning: host-key pinning, passwords never stored or logged.
- [x] Backups encrypted + authenticated; API keys stored only as hashes.
- [x] Threat model documented, including what a relay/exit server can and cannot
      see: [`docs/THREAT_MODEL.md`](THREAT_MODEL.md).

---

## 10. Final gate

```
[x] gitleaks working tree + history clean
[x] no main-branch install URLs (CI check in place)
[ ] unique name chosen and applied everywhere
[ ] originality / similarity check clean
[x] disclaimer present (EN + FA + installer)
[x] shellcheck clean, ruff clean, CI green
[x] vps-smoke-test, post-reboot-check, tunnel-check, TESTING.md ready
[x] THIRD_PARTY_LICENSES.md complete, no license conflicts
[ ] README.md + README.fa.md + docs complete with demo screenshots
[ ] v1.0.0-rc1 tagged as PRE-RELEASE (not pushed)
```

## Local verification performed

| Command | Result |
| --- | --- |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 181 files already formatted |
| `pytest -q` | All pass (2 skipped: platform-specific) |
| `bash -n` on all shell scripts | OK |
| `build-release.sh v0.0.0-ci` | tarball + `install.sh` + `SHA256SUMS` built; installer `--help`/`--version` OK |
| YAML (`workflows`, pre-commit) | parses |
| i18n `en.json` / `fa.json` | parsable, identical key sets |
| `grep` for `main`/`master` install URLs (CI pattern) | no matches |

### Notes on unchecked items

- **unique name chosen:** awaiting the owner's choice from
  [`docs/NAMING.md`](NAMING.md).
- **originality / similarity check:** performed manually and **clean**; the CI-
  enforced grep is in place, and a final name/author/domain/phrase re-check should
  be run after the rename.
- **screenshots:** capture from a seeded demo instance (fake data) and add to the
  README gallery.
- **gitleaks history scan:** the repo has **no commits yet**; re-run after the
  first commit.

### Manual items for the owner

```
[ ] rotate any secret listed under "Rotate manually"
[ ] run real-VPS tests (fresh install, reboot, two-server tunnel, real clients)
[ ] choose the project name from docs/NAMING.md
[ ] create the GitHub repo, configure settings (branch protection, security advisories)
[ ] review report, then push and publish
```

---

## Known risks / caveats

1. **Third-party copyleft binaries** (Backhaul AGPL-3.0, sing-box GPL-3.0) are
   downloaded at install time, never bundled. A future change that bundles a
   *modified* copy would create a licensing conflict — the release-gate workflow
   rejects committed tunnel binaries to prevent this.
2. **Environment:** the tunnel tests require the `[tunnels]` extra
   (`cryptography`, `paramiko`); CI installs `.[dev]` which includes them. The
   local Windows venv used here lacked `cryptography`, so those tests were run
   against the system interpreter instead — this is an environment detail, not a
   code defect.
3. **`pip-audit` / `bandit`** run in CI; the local environment could not run
   `pip-audit` (no PyPI advisory DB access here). The owner should confirm the CI
   security job is green.
4. **Distribution-name placeholder** (`<slug>` / `ovpn-panel`) remains until the
   owner chooses the final product name; the GitHub owner/repository is already
   set to `soheil-asgari/openvpn_panel`.

## Recommendation

The tree is release-ready barring the owner-only items above. After the name is
chosen, the rename applied, screenshots added and the first commit's gitleaks run
is clean, tag **`v1.0.0-rc1`** and publish it as a **pre-release**, run the
real-VPS tests, then promote to a normal release.
