# Security scan reports

This directory holds **redacted** secret-scan reports produced during release
preparation. **Secret values are never included** — only file names, rule IDs
and line numbers.

## How to produce a report

Run gitleaks on the working tree and on the full history:

```bash
# working tree
gitleaks detect --no-git --redact --report-format json \
  --report-path docs/security/gitleaks-worktree.json

# full git history
gitleaks detect --redact --report-format json \
  --report-path docs/security/gitleaks-history.json
```

Optionally, with trufflehog:

```bash
trufflehog git file://. --json --no-update > docs/security/trufflehog-history.json
```

## Current status

At the time of this release preparation the repository had **no commits** (the
tree was staged but never committed), so the history scan trivially has nothing
to hide and the working-tree scan is the meaningful one. The owner must re-run
both scans **after the first commit** and before any push, and confirm both
reports are clean.

## Rules

- If any finding is real: remove it from history (fresh single-commit repo or
  `git filter-repo`) **and rotate the secret** — rewrites do not un-leak a value
  that was already pushed.
- List every ever-exposed secret in `docs/RELEASE_REPORT.md` under
  **"Rotate manually"** (never the value itself).
- Keep the reports redacted; they are safe to commit and share.
