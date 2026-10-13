#!/usr/bin/env bash
#
# Tunnel check — run on BOTH servers (entry and exit) to verify a two-server
# tunnel: interface state, forwarding, the control/status port and end-to-end
# reachability through the tunnel.
#
#   sudo ./scripts/tunnel-check.sh --peer <other-server-ip> [--interface wg1] \
#        [--port 8080] [--forward 443:10.8.0.2:443]
#
# Exit code is non-zero if any check fails.
set -uo pipefail

PEER=""
IFACE=""
PORT=""
FORWARD=""   # publicPort:internalHost:internalPort

while [[ $# -gt 0 ]]; do
  case "$1" in
    --peer) PEER="${2:-}"; shift 2 ;;
    --interface) IFACE="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --forward) FORWARD="${2:-}"; shift 2 ;;
    -h|--help) echo "usage: $0 --peer IP [--interface wg1] [--port N] [--forward p:h:p]"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

PASS=0; FAIL=0
rc() { if [[ "$1" == PASS ]]; then echo "  PASS  $2"; PASS=$((PASS+1)); else echo "  FAIL  $2"; FAIL=$((FAIL+1)); fi; }

echo "tunnel-check on $(hostname) — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
[[ -n "${PEER}" ]] || { echo "error: --peer is required" >&2; exit 2; }

echo
echo "== interface =="
if [[ -n "${IFACE}" ]]; then
  if ip link show "${IFACE}" >/dev/null 2>&1; then rc PASS "interface ${IFACE} exists"; else rc FAIL "interface ${IFACE} missing"; fi
else
  if ip -brief link show 2>/dev/null | grep -qE '^(wg|tun|ovpn)'; then
    ip -brief link show | grep -E '^(wg|tun|ovpn)' | sed 's/^/  info  /'
    rc PASS "a tunnel interface exists"
  else
    rc FAIL "no tunnel interface found"
  fi
fi

echo
echo "== peer reachability =="
if ping -c 2 -W 3 "${PEER}" >/dev/null 2>&1; then rc PASS "peer ${PEER} reachable"; else rc FAIL "peer ${PEER} unreachable"; fi

if [[ -n "${PORT}" ]]; then
  echo
  echo "== tunnel control/status port =="
  if timeout 5 bash -c "cat < /dev/null > /dev/tcp/${PEER}/${PORT}" 2>/dev/null; then
    rc PASS "TCP ${PEER}:${PORT} open"
  else
    rc FAIL "TCP ${PEER}:${PORT} closed"
  fi
fi

if [[ -n "${FORWARD}" ]]; then
  echo
  echo "== port forwarding =="
  IFS=':' read -r pub int_host int_port <<<"${FORWARD}"
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS -o /dev/null --max-time 6 "http://127.0.0.1:${pub}/" 2>/dev/null \
       || timeout 5 bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/${pub}" 2>/dev/null; then
      rc PASS "public port ${pub} forwards (toward ${int_host}:${int_port})"
    else
      rc FAIL "public port ${pub} not forwarding"
    fi
  fi
fi

echo
echo "== forwarding ==="
if [[ "$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo 0)" == "1" ]]; then
  rc PASS "ip_forward=1"
else
  rc FAIL "ip_forward != 1"
fi

echo
echo "RESULT: ${PASS} passed, ${FAIL} failed"
[[ "${FAIL}" -eq 0 ]]
