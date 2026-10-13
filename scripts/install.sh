#!/usr/bin/env bash
#
# OpenVPN core + Web Panel installer.
#
# Supports Ubuntu/Debian (apt), RHEL family (dnf/yum) and Alpine (apk).
# Interactive by default; pass --yes for a fully non-interactive install.
#
# Examples:
#   sudo ./scripts/install.sh
#   sudo ./scripts/install.sh --yes \
#        --instances "udp1:udp:1194:10.8.0.0/24,tcp1:tcp:443:10.9.0.0/24" \
#        --admin-user admin --admin-password 'S3cret-pw' --panel-port 8080
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/ovpn-core.sh
. "${SCRIPT_DIR}/lib/ovpn-core.sh"
# shellcheck source=lib/panel-core.sh
. "${SCRIPT_DIR}/lib/panel-core.sh"

SRC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- defaults ----------------------------------------------------------------
ASSUME_YES=0
INSTALL_OPENVPN=1
INSTANCES_SPEC=""
ADMIN_USER=""
ADMIN_PASSWORD=""
PANEL_PORT=""
PANEL_HOST=""
DEFAULT_DNS="1.1.1.1"
DEFAULT_MTU="1500"
ADD_TCP_INSTANCE=""

usage() {
  cat <<'EOF'
Usage: install.sh [options]

Options:
  -y, --yes                 non-interactive; accept defaults / provided values
      --no-openvpn          install the panel only (skip OpenVPN core/PKI)
      --instances SPEC      comma-separated name:proto:port:subnet list
                            e.g. "udp1:udp:1194:10.8.0.0/24,tcp1:tcp:443:10.9.0.0/24"
      --admin-user NAME     panel admin username (default: admin)
      --admin-password PW   panel admin password (default: random)
      --panel-port PORT     panel listen port (default: 8080)
      --host HOST           public host/IP used in the client template
      --dns IP              pushed DNS server (default: 1.1.1.1)
      --mtu N               instance MTU (default: 1500)
  -h, --help                show this help
EOF
}

# --- argument parsing --------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes) ASSUME_YES=1; shift ;;
    --no-openvpn) INSTALL_OPENVPN=0; shift ;;
    --instances) INSTANCES_SPEC="${2:-}"; shift 2 ;;
    --admin-user) ADMIN_USER="${2:-}"; shift 2 ;;
    --admin-password) ADMIN_PASSWORD="${2:-}"; shift 2 ;;
    --panel-port) PANEL_PORT="${2:-}"; shift 2 ;;
    --host) PANEL_HOST="${2:-}"; shift 2 ;;
    --dns) DEFAULT_DNS="${2:-}"; shift 2 ;;
    --mtu) DEFAULT_MTU="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) ovpn_die "unknown option: $1 (see --help)" ;;
  esac
done

ovpn_require_root
ovpn_detect_os
ovpn_log "Detected ${OVPN_OS_ID} ${OVPN_OS_VER} (package manager: ${OVPN_PKG:-none})"

# --- interactive prompts -----------------------------------------------------
prompt() {
  # prompt <var-name> <question> <default>
  local __var="$1" __q="$2" __def="$3" __ans=""
  if [[ "${ASSUME_YES}" -eq 1 || ! -t 0 ]]; then
    printf -v "${__var}" '%s' "${__def}"
    return 0
  fi
  read -r -p "${__q} [${__def}]: " __ans || true
  printf -v "${__var}" '%s' "${__ans:-${__def}}"
}

prompt_secret() {
  local __var="$1" __q="$2" __ans=""
  if [[ "${ASSUME_YES}" -eq 1 || ! -t 0 ]]; then
    printf -v "${__var}" '%s' ""
    return 0
  fi
  read -r -s -p "${__q}: " __ans || true
  printf '\n'
  printf -v "${__var}" '%s' "${__ans}"
}

collect_config() {
  [[ -n "${PANEL_PORT}" ]] || prompt PANEL_PORT "Panel listen port" "8080"
  [[ -n "${ADMIN_USER}" ]] || prompt ADMIN_USER "Panel admin username" "admin"
  if [[ -z "${ADMIN_PASSWORD}" ]]; then
    if [[ "${ASSUME_YES}" -eq 0 && -t 0 ]]; then
      prompt_secret ADMIN_PASSWORD "Panel admin password (empty = random)"
    fi
  fi

  if [[ "${INSTALL_OPENVPN}" -eq 1 && -z "${INSTANCES_SPEC}" ]]; then
    prompt INSTANCES_SPEC "OpenVPN instances (name:proto:port:subnet, comma-separated)" \
      "server:udp:1194:10.8.0.0/24"
  fi
  [[ -n "${PANEL_HOST}" ]] || prompt PANEL_HOST "Public host/IP for client configs (empty = auto)" ""
  ovpn_validate_port "${PANEL_PORT}" 2>/dev/null || ovpn_die "invalid panel port: ${PANEL_PORT}"
}

collect_config

# --- system packages ---------------------------------------------------------
ovpn_log "Installing system packages"
BASE_PKGS=(curl ca-certificates openssl iproute2)
case "${OVPN_PKG}" in
  apt) BASE_PKGS+=(python3 python3-venv python3-pip sqlite3 rsync iptables) ;;
  dnf|yum) BASE_PKGS+=(python3 python3-pip sqlite rsync iptables) ;;
  apk) BASE_PKGS+=(python3 py3-pip sqlite rsync iptables) ;;
esac
if [[ "${INSTALL_OPENVPN}" -eq 1 ]]; then
  BASE_PKGS+=(openvpn easy-rsa nftables)
fi
ovpn_install_packages "${BASE_PKGS[@]}"

# --- OpenVPN core ------------------------------------------------------------
if [[ "${INSTALL_OPENVPN}" -eq 1 ]]; then
  install -d -m 0755 /etc/openvpn /etc/openvpn/server
  ovpn_generate_pki
  ovpn_generate_tls_crypt

  ovpn_log "Configuring OpenVPN instances"
  # First pass: create every instance.
  IFS=',' read -ra _instances <<<"${INSTANCES_SPEC:-server:udp:1194:10.8.0.0/24}"
  for spec in "${_instances[@]}"; do
    [[ -n "${spec}" ]] || continue
    IFS=':' read -r iname iproto iport isubnet <<<"${spec}"
    iname="${iname:-server}"; iproto="${iproto:-udp}"
    iport="${iport:-1194}"; isubnet="${isubnet:-10.8.0.0/24}"
    ovpn_instance_write "${iname}" "${iproto}" "${iport}" "${isubnet}" \
      "${DEFAULT_DNS}" "${DEFAULT_MTU}" ""
  done

  ovpn_enable_ip_forward
  ovpn_install_nat_helper

  # Second pass: start services now that NAT helper + PKI exist.
  for spec in "${_instances[@]}"; do
    [[ -n "${spec}" ]] || continue
    IFS=':' read -r iname _ _ _ <<<"${spec}"
    ovpn_instance_enable "${iname:-server}"
  done

  ovpn_apply_nat
  ovpn_persist_nftables

  # Open the OpenVPN ports plus the panel port.
  _ports=()
  for meta in "${OVPN_INSTANCES_DIR}"/*.env; do
    [[ -f "${meta}" ]] || continue
    # shellcheck disable=SC1090
    _ports+=("$( . "${meta}"; printf '%s' "${OVPN_INSTANCE_PORT}" )")
  done
  ovpn_open_firewall_ports "${_ports[@]}" "${PANEL_PORT}"

  ovpn_write_ovpn_template "${PANEL_HOST:-VPN_HOST}" "${_ports[0]:-1194}" "udp"
fi

# --- Panel -------------------------------------------------------------------
ovpn_panel_install_app "${SRC_DIR}"
ovpn_panel_generate_secrets
ovpn_log "Initialising database"
OVPN_PANEL_CONFIG_DIR="${OVPN_PANEL_CONFIG_DIR}" \
  "${OVPN_PANEL_VENV}/bin/python" -m scripts.cli init-db

ovpn_log "Recording install configuration"
ovpn_env_set OVPN_PANEL_PORT "${PANEL_PORT}"
ovpn_env_set OVPN_ADMIN_USER "${ADMIN_USER}"
[[ -n "${PANEL_HOST}" ]] && ovpn_env_set OVPN_PANEL_HOST "${PANEL_HOST}" || true
install -m 0640 /dev/null "${OVPN_SYSCONF}" 2>/dev/null || true
{
  printf 'OVPN_PANEL_PORT=%s\n' "${PANEL_PORT}"
  printf 'OVPN_ADMIN_USER=%s\n' "${ADMIN_USER}"
  printf 'OVPN_PANEL_HOST=%s\n' "${PANEL_HOST}"
  printf 'OVPN_DEFAULT_DNS=%s\n' "${DEFAULT_DNS}"
  printf 'OVPN_DEFAULT_MTU=%s\n' "${DEFAULT_MTU}"
  printf 'OVPN_INSTALL_OPENVPN=%s\n' "${INSTALL_OPENVPN}"
} >"${OVPN_SYSCONF}"
chmod 0640 "${OVPN_SYSCONF}"

ovpn_panel_install_units
ovpn_panel_install_cli

ovpn_log "Creating admin account"
if [[ -n "${ADMIN_PASSWORD}" ]]; then
  ADMIN_OUT="$(OVPN_PANEL_CONFIG_DIR="${OVPN_PANEL_CONFIG_DIR}" \
    "${OVPN_PANEL_VENV}/bin/python" -m scripts.cli create-admin "${ADMIN_USER}" \
    --password "${ADMIN_PASSWORD}" 2>&1 || true)"
else
  ADMIN_OUT="$(OVPN_PANEL_CONFIG_DIR="${OVPN_PANEL_CONFIG_DIR}" \
    "${OVPN_PANEL_VENV}/bin/python" -m scripts.cli create-admin "${ADMIN_USER}" 2>&1 || true)"
fi
printf '%s\n' "${ADMIN_OUT}"

ovpn_panel_print_access
ovpn_log "Installation complete."
