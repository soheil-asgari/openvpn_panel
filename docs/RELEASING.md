# Releasing

This project follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`,
with pre-release suffixes such as `-rc1`.

## Version locations

Bump the version in **both** places before tagging:

- `pyproject.toml` → `[project] version`
- `app/__init__.py` → `__version__`

The panel UI and the OpenAPI schema read `app.__version__`, so the version shown
in the UI follows automatically. Add a `CHANGELOG.md` entry under the new version.

## Release checklist (maintainer)

1. Confirm `main` is green: CI (ruff, pytest, shellcheck, gitleaks, pip-audit,
   release-gate) all pass.
2. Bump the version, update `CHANGELOG.md`, commit.
3. Tag and push the tag:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
4. The [release workflow](../.github/workflows/release.yml) runs on the tag and:
   - lints and tests,
   - builds the wheel/sdist,
   - runs `scripts/release/build-release.sh vX.Y.Z` to produce
     `<slug>-X.Y.Z.tar.gz`, `install.sh` (version injected) and `SHA256SUMS`,
   - creates the GitHub Release with those artifacts attached and generated
     release notes.
5. **For a release candidate** (e.g. `v1.0.0-rc1`), mark the GitHub Release as a
   **pre-release** so `releases/latest/download/...` does not serve it to users.

## Promotion to a normal release

Only promote after the owner has completed the real-VPS tests in
[`TESTING.md`](TESTING.md) on a genuine host:

1. Run the full manual checklist (fresh install, reboot, two-server tunnel,
   real clients).
2. Fix anything the tests surface as a patch release candidate if needed.
3. In the GitHub Release, **uncheck "Set as a pre-release"** and publish. Now
   `releases/latest/download/install.sh` serves it.
4. Record the promotion and the test result in `docs/test-report-template.md`.

## Tagged-release rule

- The one-line installer referenced from the README **must** point at a tagged
  release, never at a mutable branch:
  - `https://github.com/soheil-asgari/openvpn_panel/releases/latest/download/install.sh`
    (serves only published, non-draft, non-pre-release releases), or
  - `https://github.com/soheil-asgari/openvpn_panel/releases/download/vX.Y.Z/install.sh`.
- The `release-gate` workflow **fails** the build if any script or doc contains
  an install URL pointing at `.../main/` or `.../master/`.

## Reproducibility

`scripts/release/build-release.sh` builds the tarball with a fixed owner/group,
sorted entries and a fixed mtime, so two builds of the same commit produce
byte-identical archives wherever the toolchain permits. `install.sh` is generated
from `scripts/release/install-template.sh` by substituting `@VERSION@`, `@TAG@`
and `@SLUG@` only.

## Security gate (blocking)

Before any public push:

1. `gitleaks detect` on the working tree **and** `gitleaks detect --log-opts=--all`
   on the full history; both must be clean.
2. If a real secret is ever found, it must be removed from history (fresh
   single-commit repo or `git filter-repo`) and **the secret rotated by its
   owner** — rotation is mandatory because history rewrites do not un-leak a
   value that was already pushed.
3. Record every ever-exposed secret in `docs/RELEASE_REPORT.md` under
   **"Rotate manually"** (never the value itself).
