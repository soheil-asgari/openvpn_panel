# Secret-scan status

This file records the outcome of the release-preparation secret scan. It contains
**no secret values** — only the conclusion and the items the owner must act on.

## What was scanned

- The full working tree (every text file under the repository, excluding
  `.venv/`).
- A manual review for: private keys, cloud tokens, API tokens, hardcoded
  passwords, personal paths, e-mail addresses, usernames and domains.

## Environment note

`gitleaks` and `trufflehog` were not installed in the build environment used for
this preparation, so the scans below were performed with a manual pattern review.
**The owner must run gitleaks (working tree + full history) after the first commit
and before any push**, and commit the redacted JSON reports into this directory.

```bash
gitleaks detect --no-git --redact --report-format json --report-path docs/security/gitleaks-worktree.json
gitleaks detect --redact  --report-format json --report-path docs/security/gitleaks-history.json
```

## Result — working tree

**Clean.** No real secret found. The only matches are intentional placeholders and
test fixtures, all covered by the allowlist in [`.gitleaks.toml`](../../.gitleaks.toml):

| Match type | Where | Verdict |
| --- | --- | --- |
| `OVPN_SECRET_KEY=change-me-at-install` etc. | `scripts/openvpn/panel.env.example` | Placeholder example — safe |
| Dummy admin/reseller/user passwords | `tests/*` | Test-only values — safe |
| `-----BEGIN ... PRIVATE KEY-----` | `tests/test_dashboard_backup.py` | Truncated PEM marker in a test — safe |
| `certifi` CA bundle bytes | `.venv/.../cacert.pem` | Public CA data, not committed — safe |

## Result — history

The repository had **no commits** at preparation time, so there is no history to
scan yet. Re-run after the first commit.

## Personal / environment data removed

| Item | Action |
| --- | --- |
| Absolute path `d:/asgari/openvpn` in `docs/TUNNEL_RESEARCH.md` | Replaced with a neutral phrase |
| Git remote `github.com/soheil-asgari/openvpn_panel` | **Not committed** (only in local `.git/config`) — the owner will set the final remote |
| `origin` URL with an owner username | Flagged below for the owner to replace |

## Rotate manually

The owner must rotate any value that has **ever** been exposed, even if it was
removed from history. From this review:

- **Nothing to rotate yet** — no real secret was found in the tree, and no commit
  has been pushed. Once the owner pushes, treat every `panel.env` secret on any
  already-running instance as separate and rotate as needed:
  - `OVPN_SECRET_KEY` (rotating invalidates all sessions)
  - `OVPN_HOOK_TOKEN`
  - Every panel API key
  - Every tunnel token / SSH key provisioned for a tunnel
  - The `git` remote owner username (rename/replace the remote before pushing)
