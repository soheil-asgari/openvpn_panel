#!/usr/bin/env bash
#
# OpenVPN client-disconnect hook. Reports the final byte counters to the panel.
#
set -euo pipefail

CONFIG_DIR="${OVPN_PANEL_CONFIG_DIR:-/etc/ovpn-panel}"
ENV_FILE="${CONFIG_DIR}/panel.env"
HOOK_URL="${OVPN_PANEL_HOOK_DISCONNECT_URL:-http://127.0.0.1:8080/hooks/disconnect}"

TOKEN=""
if [[ -f "${ENV_FILE}" ]]; then
  TOKEN="$(grep -E '^OVPN_HOOK_TOKEN=' "${ENV_FILE}" | head -n1 | cut -d= -f2- || true)"
fi
[[ -n "${TOKEN}" ]] || exit 0

CN="${common_name:-${username:-}}"
[[ -n "${CN}" ]] || exit 0

json_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

PAYLOAD="$(printf '{"common_name":"%s","bytes_received":%s,"bytes_sent":%s}' \
  "$(json_escape "${CN}")" "${bytes_received:-0}" "${bytes_sent:-0}")"

curl -fsS --max-time 10 \
  -H "X-Hook-Token: ${TOKEN}" \
  -H 'Content-Type: application/json' \
  -X POST --data "${PAYLOAD}" \
  "${HOOK_URL}" >/dev/null || true
