# Testing

Two layers: what CI can verify automatically, and the manual real-VPS checklist
the owner runs once before promoting a release candidate.

---

## 1. Automated (CI)

The [CI workflow](../.github/workflows/ci.yml) and
[security workflow](../.github/workflows/security.yml) run on every push and PR:

- `ruff check .` and `ruff format --check .`
- `pytest`
- `shellcheck` on all shell scripts
- `gitleaks detect` on the working tree **and** the full history
- `pip-audit`
- the release-gate job (no `main`-branch install URLs, no bundled tunnel binaries)

### Install matrix (containers/VMs)

A matrix job installs the built release inside throwaway Ubuntu 22.04 / 24.04
and Debian 12 containers, then runs:

```bash
bash install.sh --yes --user admin --password 'Aa1!aaaaAAAA' --port 8080
scripts/vps-smoke-test.sh
# create a user via the API, generate a config, run update, run uninstall
```

### What CI CANNOT verify

CI containers cannot validate:

- **Real network paths** — upstream ISP behaviour, MTU/PMTU on real links,
  carrier NAT, filtering.
- **Reboot behaviour** — containers do not reboot; use `post-reboot-check.sh`
  on a real VPS.
- **Real VPN clients** — desktop/mobile OpenVPN, WireGuard, AnyConnect, L2TP,
  sing-box clients.
- **Real two-server tunnels** — reachability between two distinct hosts,
  failover/auto-recovery under real packet loss.
- **Let's Encrypt issuance** — requires a real public domain.
- **Kernel/VPN throughput and latency** under real load.

These are covered by the manual checklist below.

---

## 2. Manual real-VPS checklist

Each step lists the exact command and the expected result.

### 2.1 Fresh install — Ubuntu 22.04

1. Provision a fresh Ubuntu 22.04 VPS, nothing preinstalled.
2. Run:
   ```bash
   bash <(curl -fsSL https://github.com/soheil-asgari/openvpn_panel/releases/latest/download/install.sh)
   ```
   **Expected:** disclaimer shown; after typing `yes`, preflight checks pass;
   prerequisites install; checksum verified; summary printed and saved to
   `/root/<slug>-install-info.txt`.
3. Inspect: `cat /root/<slug>-install-info.txt` → panel URL, admin user,
   password, paths (mode 600).
4. `sudo scripts/vps-smoke-test.sh` → **0 failed**; report at
   `/var/log/<slug>-smoke-report.txt`.

### 2.2 Fresh install — Ubuntu 24.04

Repeat 2.1 on Ubuntu 24.04. **Expected:** identical result.

### 2.3 Reboot test

```bash
sudo reboot
# reconnect, then:
sudo scripts/post-reboot-check.sh
```
**Expected:** all services active, every OpenVPN instance active, NAT
re-applied, `ip_forward=1`, 0 failed.

### 2.4 Two-server tunnel test

Prepare two servers: **entry** and **exit**.

1. On the panel, add the entry server from the UI (Tunnels → wizard): backend,
   transport, entry host/port, SSH credentials, public ports.
2. Confirm the host-key fingerprint when prompted.
3. On both servers:
   ```bash
   sudo scripts/tunnel-check.sh --peer <other-server-ip> --port <control-port>
   ```
   **Expected:** interface present, peer reachable, control port open,
   0 failed.
4. **Auto-recovery:** kill the tunnel process on the entry server
   (`sudo pkill -f <backend>`). **Expected:** the panel/tunnel worker restarts it
   within the retry window; re-run `tunnel-check.sh` → 0 failed.
5. **Change transport:** switch transport (e.g. `tcp` → `tcpmux`). **Expected:**
   tunnel re-provisions, checks pass again.
6. **Remove the tunnel** from the UI. **Expected:** full cleanup on **both**
   servers — processes stopped, configs removed, no leftover ports/units.

### 2.5 Real client test

For the main protocol (OpenVPN) and then each additional protocol
(WireGuard, AnyConnect/ocserv, L2TP/IPsec, sing-box):

1. Connect with a **real client** — desktop *and* mobile.
2. Connect **directly** and **through the tunnel**.
3. Verify **per-user traffic counters** increase in the panel.
4. Verify **quota cutoff**: set a small data limit, push past it → session
   drops and the user is cut off.
5. Verify **expiry cutoff**: set an expiry in the past → connection refused.
6. Verify **kick**: from the UI, kick the user → session drops immediately.
7. Verify **max-connections limit**: connect more than the allowed number of
   simultaneous sessions → extra sessions are refused.

### 2.6 Update test (vX → vY)

1. Install vX on a fresh server, create a user and a tunnel, keep the data.
2. Run the update:
   ```bash
   bash <(curl -fsSL <release URL for vY>/install.sh) update
   ```
   **Expected:** files and dependencies updated, database migrated, all data
   (users, tunnels, traffic) preserved, smoke test passes.
3. `sudo <slug> status` → new version.

### 2.7 Uninstall test

```bash
bash <(curl -fsSL https://github.com/soheil-asgari/openvpn_panel/releases/latest/download/install.sh) uninstall        # keep data
bash <(curl -fsSL https://github.com/soheil-asgari/openvpn_panel/releases/latest/download/install.sh) uninstall --purge  # delete data
```
**Expected:** services stopped and removed, units/firewall cleaned; `--purge`
also removes config/data/PKI and the service user.

### 2.8 REST API smoke

```bash
curl -fsS -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8080/api/v1/users
```
**Expected:** JSON list; anonymous request returns `401`.

---

## 3. Recording results

Copy [`test-report-template.md`](test-report-template.md) to
`docs/test-reports/<version>.md`, fill it in, and keep it with the release.
