# Test report — vX.Y.Z

> Copy this file to `docs/test-reports/vX.Y.Z.md` and fill it in after running
> the manual checklist in [`TESTING.md`](TESTING.md). **Redact all secrets, IPs
> and domains** before sharing.

## Environment

| Field | Value |
| --- | --- |
| Tester | |
| Date (UTC) | |
| Release tested | vX.Y.Z (rc: yes/no) |
| Server provider / region | |
| OS / version | |
| Architecture | |
| RAM / disk | |
| Panel URL (redacted) | |

## Automated results

| Check | Result | Notes |
| --- | --- | --- |
| CI green (ruff, pytest, shellcheck, gitleaks, pip-audit) | | |
| `vps-smoke-test.sh` | __ passed / __ failed | |
| Redacted report path | | |

## Manual checklist

| # | Test | Result | Notes |
| --- | --- | --- | --- |
| 1 | Fresh install — Ubuntu 22.04 | | |
| 2 | Fresh install — Ubuntu 24.04 | | |
| 3 | Reboot test (`post-reboot-check.sh`) | | |
| 4 | Two-server tunnel — create + `tunnel-check.sh` | | |
| 5 | Two-server tunnel — auto-recovery after kill | | |
| 6 | Two-server tunnel — change transport | | |
| 7 | Two-server tunnel — remove + full cleanup | | |
| 8 | Real client — OpenVPN (desktop + mobile) | | |
| 9 | Real client — WireGuard | | |
| 10 | Real client — AnyConnect (ocserv) | | |
| 11 | Real client — L2TP/IPsec | | |
| 12 | Real client — sing-box | | |
| 13 | Traffic counters | | |
| 14 | Quota cutoff | | |
| 15 | Expiry cutoff | | |
| 16 | Kick | | |
| 17 | Max-connections limit | | |
| 18 | Update vX → vY (data preserved) | | |
| 19 | Uninstall (keep data) | | |
| 20 | Uninstall `--purge` | | |
| 21 | REST API auth (anonymous → 401) | | |

## Issues found

| # | Severity | Description | Steps to reproduce | Status |
| --- | --- | --- | --- | --- |
| | | | | |

## Verdict

- [ ] All checks passed — promote `vX.Y.Z-rcN` to a normal release.
- [ ] Passed with minor issues (list above) — release acceptable.
- [ ] Blocking issues — do **not** promote; fix and re-cut an rc.
