# Development

> See [CONTRIBUTING.md](../CONTRIBUTING.md) for the ground rules and PR process.

## Release checklist

1. Ensure `ruff check .` and `pytest` pass locally.
2. Bump the version in `pyproject.toml` and `app/__init__.py`.
3. Add a new Alembic migration if the schema changed (`alembic revision`).
4. Update the docs and `docs/api.md` for any API/behaviour change.
5. Tag the release: `git tag vX.Y.Z && git push --tags`. The
   [release workflow](../.github/workflows/release.yml) runs lint + tests, builds
   the wheel/sdist and the release tarball, then publishes a GitHub release.
6. The [security workflow](../.github/workflows/security.yml) runs `pip-audit`
   and `bandit` on every push and weekly.

## Setup

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

On Windows use `.venv\Scripts\activate`.

## Running

```bash
export OVPN_PANEL_CONFIG_DIR=$PWD/.dev-config
mkdir -p "$OVPN_PANEL_CONFIG_DIR"
python -m scripts.cli gen-secrets --output "$OVPN_PANEL_CONFIG_DIR/panel.env" \
  --database-url "sqlite:///$PWD/.dev-config/dev.sqlite3"
OVPN_PANEL_CONFIG_DIR="$OVPN_PANEL_CONFIG_DIR" python -m scripts.cli init-db
OVPN_PANEL_CONFIG_DIR="$OVPN_PANEL_CONFIG_DIR" uvicorn app.main:app --reload
```

## Pre-commit

```bash
pre-commit install
pre-commit run --all-files
```

The hooks run ruff, ruff-format, shellcheck and the usual file hygiene checks.

## Tests

```bash
ruff check .
pytest
```

Tests use a throwaway SQLite database created fresh per test and never touch
`/etc` or `/var`.

## Configuration

Settings are resolved, highest priority first, from explicit arguments, `OVPN_*`
environment variables, `/etc/ovpn-panel/panel.env`, then an optional
`/etc/ovpn-panel/config.toml` (see `scripts/openvpn/config.toml.example`). Use
`admin_path` to serve the web UI under a secret URL prefix.

## Guidelines

- Validate every external input with `app.core.validators` before use.
- Never build shell strings; pass argument lists to `subprocess.run` with
  `shell=False`.
- Keep privileged operations inside `helper/actions.py` and add them to the
  allow-list deliberately.
- Add UI strings to **both** `app/i18n/en.json` and `app/i18n/fa.json`.
- Every new feature or bug fix needs tests.
