## Summary

<!-- One or two sentences describing the change. -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation
- [ ] Refactor / cleanup
- [ ] Security hardening

## Checklist

- [ ] `ruff check .` passes.
- [ ] `pytest` passes.
- [ ] `shellcheck` passes for changed shell scripts.
- [ ] No secrets, tokens, personal paths or credentials added.
- [ ] No license-key, activation or telemetry code added.
- [ ] New UI strings added to both `app/i18n/en.json` and `app/i18n/fa.json`.
- [ ] Docs updated (EN and FA where user-facing).
- [ ] New REST endpoints live under `/api/v1`, require an API key and are scoped,
      with a test asserting anonymous access is rejected.

## Migration / breaking changes

<!-- Describe DB migrations or behavioural changes, or write "none". -->

## How was this tested?

<!-- Exact commands and the result observed. -->
