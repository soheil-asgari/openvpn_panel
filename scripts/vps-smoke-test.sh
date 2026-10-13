#!/usr/bin/env bash
#
# Post-install smoke test for a fresh VPS.
#
# Run AFTER the one-line installer finishes:
#   sudo ./scripts/vps-smoke-test.sh
#
# Prints a PASS/FAIL list, exits non-zero on any FAIL, and writes a REDACTED
# report (safe to share) to /var/log/ovpn-panel-smoke-report.txt.
set -uo pipefail

SLUG="${SLUG:-ovpn-panel}"
CONFIG_DIR="${OVPN_PANEL_CONFIG_DIR:-/etc/${SLUG}}"
REPORT="/var/log/${SLUG}-smoke-report.txt"
PASS=0
FAIL=0

# --- helpers -----------------------------------------------------------------
res() { # res <PASS|FAIL> <label>
  if [[ "$1" == PASS ]]; then
    printf '  PASS  %s\n' "$2"; PASS=$((PASS+1))
  else
    printf '  FAIL  %s\n' "$2"; FAIL=$((FAIL+1))
  fi
}
section() { printf '\n== %s ==\n' "$1"; }
redact() { sed -E 's/(password|token|secret|key)=[^ ]+/\1=<redacted>/Ig'; }

log() { printf '%s\n' "$*" | tee -a "${REPORT}"; }
# Redirect everything through redaction into the report.
exec > >(tee >(redact >>"${REPORT}")) 2>&1

panel_port() {
  local v
  v="$(grep -E '^OVPN_PANEL_PORT=' "${CONFIG_DIR}/panel.env" 2>/dev/null | cut -d= -f2- || true)"
  printf '%s' "${v:-8080}"
}

PORT="$(panel_port)"

log "${SLUG} smoke test — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "host: $(hostname)  kernel: $(uname -r)  port: ${PORT}"

section "systemd services"
for u in "${SLUG}.service" "${SLUG}-helper.service"; do
  if systemctl is-active --quiet "$u"; then res PASS "$u active"; else res FAIL "$u not active"; fi
done

section "listening ports"
if command -v ss >/dev/null 2>&1; then
  while read -r p; do
    [[ -n "${p}" ]] || continue
    if ss -ltn "sport = :${p}" 2>/dev/null | grep -q ":${p}"; then
      res PASS "TCP port ${p} listening"
    else
      res FAIL "TCP port ${p} not listening"
    fi
  done <<<"${PORT}"
  for p in 1194 51820; do
    if ss -lun "sport = :${p}" 2>/dev/null | grep -q ":${p}"; then
      res PASS "UDP port ${p} listening"
    else
      res FAIL "UDP port ${p} not listening (check if an instance uses it)"
    fi
  done
else
  res FAIL "ss not available; cannot check ports"
fi

section "panel HTTP"
code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:${PORT}/" 2>/dev/null || echo 000)"
[[ "${code}" == "200" || "${code}" == "302" ]] && res PASS "panel responds (${code})" || res FAIL "panel HTTP ${code}"

section "TLS (only if a reverse proxy / certbot is configured)"
if compgen -G "/etc/letsencrypt/live/*/fullchain.pem" >/dev/null; then
  domain="$(ls -d /etc/letsencrypt/live/*/ 2>/dev/null | head -1 | xargs -I{} basename {})"
  hcode="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 8 "https://${domain}/" 2>/dev/null || echo 000)"
  if [[ "${hcode}" =~ ^(200|302)$ ]]; then
    res PASS "HTTPS ${domain} (${hcode})"
  else
    res FAIL "HTTPS ${domain} (${hcode})"
  fi
else
  log "  SKIP  no Let's Encrypt certificate found"
fi

section "IP forwarding / NAT"
if [[ "$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo 0)" == "1" ]]; then
  res PASS "net.ipv4.ip_forward=1"
else
  res FAIL "net.ipv4.ip_forward != 1"
fi
if command -v nft >/dev/null 2>&1; then
  if nft list ruleset 2>/dev/null | grep -q 'masquerade'; then
    res PASS "nftables masquerade present"
  else
    res FAIL "nftables masquerade missing"
  fi
fi

section "firewall exposure"
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q 'Status: active'; then
  res PASS "ufw active"
elif command -v firewall-cmd >/dev/null 2>&1; then
  res PASS "firewalld present"
else
  log "  SKIP  no host firewall manager active (cloud firewall may be used)"
fi

section "disk and logs"
root_use="$(df -P / | awk 'NR==2{print $5}' | tr -d '%')"
if [[ "${root_use:-100}" -lt 90 ]]; then res PASS "root disk usage ${root_use}%"; else res FAIL "root disk usage ${root_use}%"; fi
for f in "/var/log/${SLUG}-install.log" "/var/log/${SLUG}-smoke-report.txt"; do
  if [[ -f "${f}" ]]; then
    sz="$(du -m "${f}" | awk '{print $1}')"
    [[ "${sz}" -lt 50 ]] && res PASS "$(basename "${f}") ${sz} MB" || res FAIL "$(basename "${f}") ${sz} MB (large)"
  fi
done

section "journal errors (last 15 min)"
if command -v journalctl >/dev/null 2>&1; then
  errs="$(journalctl -u "${SLUG}.service" --since '15 min ago' -p err --no-pager 2>/dev/null | grep -c . || true)"
  [[ "${errs:-0}" -eq 0 ]] && res PASS "no error-level journal entries" || res FAIL "${errs} error journal entries"
fi

log ""
log "RESULT: ${PASS} passed, ${FAIL} failed"
log "Redacted report written to ${REPORT}"

[[ "${FAIL}" -eq 0 ]]
