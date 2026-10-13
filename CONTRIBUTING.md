# Contributing

Thanks for helping improve the OpenVPN Web Panel.

## Ground rules

- The project is MIT licensed. By contributing you agree your work is released
  under the same license.
- Write everything from scratch. Do **not** copy code, scripts, UI, text or
  branding from any other panel. Feature ideas are fine; copied material is not.
- Never add license keys, activation, telemetry or "phone home" code.

## Getting started

```bash
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check .
pytest
```

## Coding standards

- Python 3.11+, type hints on public functions, 100 character line limit.
- Validate all external input with `app.core.validators`.
- Never build shell commands by concatenation; use `subprocess.run` with an
  argument list and `shell=False`.
- Keep privileged logic in `helper/actions.py` and add it to the allow-list
  explicitly.
- Add every new UI string to both `app/i18n/en.json` and `app/i18n/fa.json`.
- Every feature or bug fix must come with tests under `tests/`.
- New REST endpoints live under `/api/v1`, require an API key, and must be
  scoped. Add a test that asserts anonymous access is rejected.

## Commit style

- Use short, imperative commit subjects (e.g. "add node retry queue").
- Keep changes scoped; one logical change per commit where practical.
- Reference the issue in the body when relevant.

## Pull requests

1. Fork and create a topic branch.
2. Keep the diff focused; update docs when behaviour changes.
3. Ensure `ruff check .` and `pytest` both pass.
4. Describe the change and any migration steps in the PR body.
