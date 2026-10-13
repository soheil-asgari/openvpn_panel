#!/usr/bin/env bash
#
# Update the panel (and its systemd units) from the current checkout.
# OpenVPN instances, PKI, NAT rules and the database are left untouched.
#
# Run as root:  sudo ./scripts/update.sh
#
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/ovpn-core.sh
. "${SCRIPT_DIR}/lib/ovpn-core.sh"
# shellcheck source=scripts/lib/panel-core.sh
. "${SCRIPT_DIR}/lib/panel-core.sh"

# Surface the exact source location and command on any unhandled failure.
trap 'ovpn_on_err' ERR

SRC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

ovpn_require_root

ovpn_log "Stopping panel service"
systemctl stop ovpn-panel.service 2>/dev/null || true

ovpn_log "Backing up database"
install -d -m 0700 /var/backups/ovpn-panel
if [[ -f /var/lib/ovpn-panel/panel.sqlite3 ]]; then
  cp -a /var/lib/ovpn-panel/panel.sqlite3 \
    "/var/backups/ovpn-panel/panel-$(date +%Y%m%d%H%M%S).sqlite3"
fi

ovpn_log "Syncing files"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
    "${SRC_DIR}/app" "${SRC_DIR}/agent" "${SRC_DIR}/helper" \
    "${SRC_DIR}/scripts" "${SRC_DIR}/pyproject.toml" "${SRC_DIR}/README.md" \
    "${SRC_DIR}/LICENSE" "${OVPN_PANEL_DIR}/"
else
  for item in app agent helper scripts pyproject.toml README.md LICENSE; do
    cp -a "${SRC_DIR}/${item}" "${OVPN_PANEL_DIR}/"
  done
fi
chmod 0755 "${OVPN_PANEL_DIR}/scripts/hooks/"*.sh 2>/dev/null || true
chmod 0755 "${OVPN_PANEL_DIR}/scripts/ovpn-nat.sh" 2>/dev/null || true

ovpn_log "Updating dependencies"
"${OVPN_PANEL_VENV}/bin/pip" install --upgrade "${OVPN_PANEL_DIR}"

ovpn_log "Applying database migrations"
OVPN_PANEL_CONFIG_DIR="${OVPN_PANEL_CONFIG_DIR}" \
  "${OVPN_PANEL_VENV}/bin/python" -m scripts.cli init-db

ovpn_log "Refreshing systemd units"
install -m 0644 "${OVPN_PANEL_DIR}/scripts/systemd/ovpn-panel.service" /etc/systemd/system/
install -m 0644 "${OVPN_PANEL_DIR}/scripts/systemd/ovpn-panel-helper.service" /etc/systemd/system/
install -m 0644 "${OVPN_PANEL_DIR}/scripts/systemd/ovpn-panel-agent.service" /etc/systemd/system/
ovpn_panel_render_unit_port "$(ovpn_panel_get_port)"

ovpn_log "Refreshing NAT helper"
ovpn_install_nat_helper
ovpn_panel_install_cli

ovpn_log "Restarting services"
ovpn_panel_restart_services
ovpn_apply_nat

ovpn_log "Update complete."
