#!/usr/bin/env bash
#
# Verify that everything came back after a reboot.
#
#   sudo reboot            # reboot the server
#   # after it is back:
#   sudo ./scripts/post-reboot-check.sh
#
# Checks the panel, the helper, every OpenVPN instance and every tunnel, then
# exits non-zero if anything is missing.
set -uo pipefail

SLUG="${SLUG:-ovpn-panel}"
INSTANCES_DIR="${OVPN_INSTANCES_DIR:-/etc/openvpn/instances}"
PASS=0
FAIL=0

rc() { if [[ "$1" == PASS ]]; then printf '  PASS  %s\n' "$2"; PASS=$((PASS+1)); else printf '  FAIL  %s\n' "$2"; FAIL=$((FAIL+1)); fi; }

printf '%s post-reboot check — uptime %s\n' "${SLUG}" "$(uptime -p 2>/dev/null || uptime)"

printf '\n== services ==\n'
for u in "${SLUG}.service" "${SLUG}-helper.service"; do
  if systemctl is-active --quiet "$u"; then
    rc PASS "$u active"
  else
    rc FAIL "$u not active"
  fi
done
if systemctl is-enabled --quiet "${SLUG}.service" 2>/dev/null; then
  rc PASS "${SLUG}.service enabled (survives reboot)"
else
  rc FAIL "${SLUG}.service not enabled"
fi

printf '\n== OpenVPN instances ==\n'
found=0
for meta in "${INSTANCES_DIR}"/*.env; do
  [[ -f "${meta}" ]] || continue
  found=1
  # shellcheck source=/dev/null
  name="$( . "${meta}"; printf '%s' "${OVPN_INSTANCE_NAME}" )"
  if systemctl is-active --quiet "openvpn-server@${name}"; then
    rc PASS "openvpn-server@${name} active"
  else
    rc FAIL "openvpn-server@${name} not active"
  fi
done
[[ "${found}" -eq 1 ]] || printf '  SKIP  no OpenVPN instances found\n'

printf '\n== tunnel interface (best effort) ==\n'
if ip -brief link show 2>/dev/null | grep -qE '^(wg|tun|ovpn)'; then
  ip -brief link show | grep -E '^(wg|tun|ovpn)' | sed 's/^/  info  /'
  rc PASS "tunnel-like interface present"
else
  printf '  SKIP  no wg/tun interface (no tunnel configured, or none on this node)\n'
fi

printf '\n== NAT after reboot ==\n'
if [[ "$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo 0)" == "1" ]]; then
  rc PASS "ip_forward still 1"
else
  rc FAIL "ip_forward reverted"
fi
if command -v nft >/dev/null 2>&1; then
  if nft list ruleset 2>/dev/null | grep -q 'masquerade'; then
    rc PASS "NAT rules re-applied on boot"
  else
    rc FAIL "NAT rules missing after reboot"
  fi
fi

printf '\nRESULT: %d passed, %d failed\n' "${PASS}" "${FAIL}"
[[ "${FAIL}" -eq 0 ]]
