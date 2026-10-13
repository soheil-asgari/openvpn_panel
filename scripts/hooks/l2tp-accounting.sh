#!/usr/bin/env bash
# L2TP/IPsec per-session accounting hook (Phase 14).
#
# Installed as /etc/ppp/ip-up.d/ and /etc/ppp/ip-down.d/ (or referenced from
# /etc/ppp/ip-up and /etc/ppp/ip-down). pppd passes the interface, peer address
# and the authenticated user name; this hook records per-user byte counters into
# the panel's protocol-state directory, which the L2TP backend reads back via
# ``app.services.protocols.l2tp.L2tpBackend.usage``.
#
# It never builds a shell string from the ppp environment beyond fixed paths and
# numeric validation, mirroring the panel's command-safety rules.

set -euo pipefail

STATE_DIR="/var/lib/ovpn-panel/protocol-state/l2tp"
PHASE="${1:-up}"          # "up" or "down"
IFACE="${PPP_IFACE:-${6:-}}"
USER_NAME="${PEERNAME:-${2:-}}"

[ -n "$USER_NAME" ] || exit 0
# Restrict the username to the same charset the panel accepts.
case "$USER_NAME" in
  *[!a-zA-Z0-9_.-]*) exit 0 ;;
esac

mkdir -p "$STATE_DIR"
USAGE_FILE="${STATE_DIR}/${USER_NAME}.usage"

if [ "$PHASE" = "up" ]; then
  printf '0 0\n' > "$USAGE_FILE"
  chmod 600 "$USAGE_FILE"
  exit 0
fi

# On down: read the interface counters and append them to the user's totals.
if [ -n "$IFACE" ] && [ -r "/sys/class/net/${IFACE}/statistics/rx_bytes" ]; then
  RX=$(cat "/sys/class/net/${IFACE}/statistics/rx_bytes" 2>/dev/null || echo 0)
  TX=$(cat "/sys/class/net/${IFACE}/statistics/tx_bytes" 2>/dev/null || echo 0)
else
  RX=0
  TX=0
fi

case "$RX" in *[!0-9]*) RX=0 ;; esac
case "$TX" in *[!0-9]*) TX=0 ;; esac

PREV_RX=0
PREV_TX=0
if [ -r "$USAGE_FILE" ]; then
  read -r PREV_RX PREV_TX < "$USAGE_FILE" || true
fi
case "$PREV_RX" in *[!0-9]*) PREV_RX=0 ;; esac
case "$PREV_TX" in *[!0-9]*) PREV_TX=0 ;; esac

printf '%s %s %s\n' "$USER_NAME" "$((PREV_RX + RX))" "$((PREV_TX + TX))" > "$USAGE_FILE"
chmod 600 "$USAGE_FILE"
