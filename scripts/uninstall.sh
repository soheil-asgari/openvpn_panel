#!/usr/bin/env bash
#
# Uninstall the panel.
#
#   sudo ./scripts/uninstall.sh            # keep config, data, PKI and instances
#   sudo ./scripts/uninstall.sh --purge    # remove panel config/data as well
#   sudo ./scripts/uninstall.sh --purge-openvpn  # also remove PKI, NAT and instances
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/ovpn-core.sh
. "${SCRIPT_DIR}/lib/ovpn-core.sh"
# shellcheck source=scripts/lib/panel-core.sh
. "${SCRIPT_DIR}/lib/panel-core.sh"

PURGE=0
PURGE_OPENVPN=0
for arg in "$@"; do
  case "${arg}" in
    --purge) PURGE=1 ;;
    --purge-openvpn) PURGE=1; PURGE_OPENVPN=1 ;;
    *) ovpn_die "unknown option: ${arg}" ;;
  esac
done

ovpn_require_root

ovpn_log "Stopping and disabling panel services"
for unit in ovpn-panel.service ovpn-panel-helper.service \
            ovpn-panel-agent.service ovpn-panel-nat.service; do
  systemctl disable --now "${unit}" 2>/dev/null || true
done

if [[ "${PURGE_OPENVPN}" -eq 1 ]]; then
  ovpn_log "Removing OpenVPN instances"
  for meta in "${OVPN_INSTANCES_DIR}"/*.env; do
    [[ -f "${meta}" ]] || continue
    # shellcheck disable=SC1090
    _inst_name="$( . "${meta}"; printf '%s' "${OVPN_INSTANCE_NAME}" )"
    systemctl disable --now "openvpn-server@${_inst_name}" 2>/dev/null || true
  done
fi

rm -f /etc/systemd/system/ovpn-panel.service \
      /etc/systemd/system/ovpn-panel-helper.service \
      /etc/systemd/system/ovpn-panel-agent.service \
      /etc/systemd/system/ovpn-panel-agent@.service \
      /etc/systemd/system/ovpn-panel-nat.service
systemctl daemon-reload

ovpn_log "Removing application files"
rm -rf "${OVPN_PANEL_DIR}" /run/ovpn-panel

if [[ "${PURGE}" -eq 1 ]]; then
  ovpn_log "Purging panel configuration and data"
  rm -rf "${OVPN_PANEL_CONFIG_DIR}" /var/lib/ovpn-panel /var/backups/ovpn-panel
  userdel ovpn-panel 2>/dev/null || true
  groupdel ovpn-panel 2>/dev/null || true
else
  ovpn_warn "keeping ${OVPN_PANEL_CONFIG_DIR} and /var/lib/ovpn-panel (use --purge)"
fi

if [[ "${PURGE_OPENVPN}" -eq 1 ]]; then
  ovpn_log "Purging OpenVPN core (PKI, instances, NAT, sysctl)"
  rm -rf "${OVPN_PKI_DIR}" "${OVPN_INSTANCES_DIR}" "${OVPN_CCD_DIR}" \
         "${OVPN_AUTH_DIR}" "${OVPN_CLIENT_DIR}"
  rm -f "${SYSCTL_FILE}" "${NFT_FRAGMENT}"
  # Best-effort removal of the applied NAT rules.
  if command -v nft >/dev/null 2>&1; then
    nft delete table ip ovpn_panel 2>/dev/null || true
    nft delete table inet ovpn_panel_input 2>/dev/null || true
  fi
  if command -v iptables >/dev/null 2>&1; then
    iptables -t nat -F POSTROUTING 2>/dev/null || true
  fi
  sysctl -w net.ipv4.ip_forward=0 >/dev/null 2>&1 || true
fi

ovpn_log "Uninstall complete."
