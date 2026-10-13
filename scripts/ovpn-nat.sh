#!/usr/bin/env bash
#
# Apply IP forwarding + NAT for every ovpn-panel OpenVPN instance.
# Idempotent: safe to run on every boot (via ovpn-panel-nat.service) and after
# instances are added or removed. Uses nftables when available, falls back to
# iptables.
#
set -euo pipefail

INSTANCES_DIR="${OVPN_INSTANCES_DIR:-/etc/openvpn/instances}"

ewm="$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -n1)"
ewm="${ewm:-eth0}"

subnets=()
for meta in "${INSTANCES_DIR}"/*.env; do
  [[ -f "${meta}" ]] || continue
  # shellcheck disable=SC1090
  snet="$( . "${meta}"; printf '%s' "${OVPN_INSTANCE_SUBNET:-}" )"
  [[ -n "${snet}" ]] && subnets+=("${snet}")
done

sysctl -w net.ipv4.ip_forward=1 >/dev/null

if command -v nft >/dev/null 2>&1; then
  nft list table ip ovpn_panel >/dev/null 2>&1 || nft add table ip ovpn_panel
  nft list chain ip ovpn_panel postrouting >/dev/null 2>&1 || \
    nft add chain ip ovpn_panel postrouting '{ type nat hook postrouting priority srcnat ; policy accept ; }'
  for snet in "${subnets[@]}"; do
    nft add rule ip ovpn_panel postrouting ip saddr "${snet}" oifname "${ewm}" masquerade 2>/dev/null || true
  done
elif command -v iptables >/dev/null 2>&1; then
  for snet in "${subnets[@]}"; do
    iptables -t nat -C POSTROUTING -s "${snet}" -o "${ewm}" -j MASQUERADE 2>/dev/null || \
      iptables -t nat -A POSTROUTING -s "${snet}" -o "${ewm}" -j MASQUERADE
    iptables -C FORWARD -s "${snet}" -j ACCEPT 2>/dev/null || \
      iptables -A FORWARD -s "${snet}" -j ACCEPT
  done
  iptables -C FORWARD -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
    iptables -A FORWARD -m state --state RELATED,ESTABLISHED -j ACCEPT
fi

exit 0
