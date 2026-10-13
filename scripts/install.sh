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
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/ovpn-core.sh
. "${SCRIPT_DIR}/lib/ovpn-core.sh"
# shellcheck source=scripts/lib/panel-core.sh
. "${SCRIPT_DIR}/lib/panel-core.sh"

# Surface the exact source location and command on any unhandled failure.
trap 'ovpn_on_err' ERR

# First install (no prior install marker) => full rollback of units/files this
# run created on fatal failure. Re-runs after a previous install never disable
# pre-existing running services, only clean up what the re-run itself created.
if [[ -f "${OVPN_SYSCONF}" || -f "${OVPN_PANEL_CONFIG_DIR}/panel.env" ]]; then
  _OVPN_PANEL_FIRST_RUN=0
else
  _OVPN_PANEL_FIRST_RUN=1
fi
_OVPN_ROLLBACK_UNITS=()
_OVPN_ROLLBACK_FILES=()

ovpn_install_rollback() {
  local rc=$?
  if [[ ${rc} -ne 0 ]]; then
    if [[ ${#_OVPN_ROLLBACK_UNITS[@]} -gt 0 || ${#_OVPN_ROLLBACK_FILES[@]} -gt 0 ]]; then
      ovpn_log "install failed (exit ${rc}) — rolling back what was created"
      ovpn_rollback_execute
    else
      ovpn_log "install failed (exit ${rc}) — nothing had been created yet, nothing to roll back"
    fi
  fi
  return "${rc}"
}
trap 'ovpn_install_rollback' EXIT

SRC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- defaults ----------------------------------------------------------------
ASSUME_YES=0
INSTALL_OPENVPN=1
INSTANCES_SPEC=""
ADMIN_USER=""
ADMIN_PASSWORD=""
ADMIN_PASSWORD_FLAG=0
ADMIN_PASSWORD_GENERATED=0
PANEL_PORT=""
PANEL_HOST=""
DEFAULT_DNS="1.1.1.1"
DEFAULT_MTU="1500"

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
    --admin-password) ADMIN_PASSWORD="${2:-}"; ADMIN_PASSWORD_FLAG=1; shift 2 ;;
    --panel-port) PANEL_PORT="${2:-}"; shift 2 ;;
    --host) PANEL_HOST="${2:-}"; shift 2 ;;
    --dns) DEFAULT_DNS="${2:-}"; shift 2 ;;
    --mtu) DEFAULT_MTU="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) ovpn_die "unknown option: $1 (see --help)" ;;
  esac
done

if [[ "${OVPN_INSTALL_DRY_RUN:-0}" -eq 1 ]]; then
  # Test seam: resolve configuration only, then stop (no root, no changes).
  OVPN_OS_ID="dry-run"; OVPN_OS_VER=""; OVPN_PKG=""
else
  ovpn_require_root
  ovpn_detect_os
  ovpn_log "Detected ${OVPN_OS_ID} ${OVPN_OS_VER} (package manager: ${OVPN_PKG:-none})"
fi

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
      prompt_secret ADMIN_PASSWORD "Admin password (empty = strong random)"
    fi
    # Empty input (interactive Enter, --password "", or omitted) => generate a
    # strong random password instead of rejecting the install.
    if [[ -z "${ADMIN_PASSWORD}" ]]; then
      ADMIN_PASSWORD="$(ovpn_strong_password 24)"
      ADMIN_PASSWORD_GENERATED=1
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

if [[ "${OVPN_INSTALL_DRY_RUN:-0}" -eq 1 ]]; then
  printf 'DRY_RUN admin_user=%s\n' "${ADMIN_USER}"
  printf 'DRY_RUN admin_password=%s\n' "${ADMIN_PASSWORD}"
  printf 'DRY_RUN password_generated=%s\n' "${ADMIN_PASSWORD_GENERATED}"
  exit 0
fi

# --- environment auto-heal ----------------------------------------------------
ovpn_log "Auto-healing environment issues"
ovpn_ensure_ntp
ovpn_load_kernel_modules
if [[ "${OVPN_PKG}" == "apt" ]]; then
  ovpn_wait_apt_locks
fi

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

# --- OpenVPN core: prepare (PKI + instance configs) --------------------------
# Services are NOT started here — they start only after the panel files are
# staged (hooks like auth-user-pass-verify must exist before OpenVPN starts).
if [[ "${INSTALL_OPENVPN}" -eq 1 ]]; then
  install -d -m 0755 /etc/openvpn /etc/openvpn/server
  install -d -m 0755 /etc/systemd/system/openvpn-server@.service.d
  # Track the PKI dir for first-install rollback — but only when no valid CA
  # exists yet, so a pre-existing CA is NEVER wiped.
  if [[ ! -f "${OVPN_PKI_DIR}/ca.crt" ]]; then
    ovpn_rollback_mark_dir "${OVPN_PKI_DIR}"
  fi
  ovpn_generate_pki
  # tls-crypt is REQUIRED by the generated configs — fatal on failure.
  ovpn_generate_tls_crypt
  ovpn_rollback_mark_file "${OVPN_PKI_DIR}/tls-crypt.key"

  ovpn_log "Configuring OpenVPN instances"
  # First pass: create/reuse every instance. Missing *incomplete* parts of an
  # existing instance are rebuilt; existing configs are reused.
  IFS=',' read -ra _instances <<<"${INSTANCES_SPEC:-server:udp:1194:10.8.0.0/24}"
  for spec in "${_instances[@]}"; do
    [[ -n "${spec}" ]] || continue
    IFS=':' read -r iname iproto iport isubnet <<<"${spec}"
    iname="${iname:-server}"; iproto="${iproto:-udp}"
    iport="${iport:-1194}"; isubnet="${isubnet:-10.8.0.0/24}"
    if [[ -f "$(ovpn_instance_conf_path "${iname}")" && -f "$(ovpn_instance_meta_path "${iname}")" ]]; then
      ovpn_ok "instance ${iname} already present — reusing"
    else
      ovpn_instance_write "${iname}" "${iproto}" "${iport}" "${isubnet}" \
        "${DEFAULT_DNS}" "${DEFAULT_MTU}" ""
      ovpn_rollback_mark_file "$(ovpn_instance_conf_path "${iname}")"
      ovpn_rollback_mark_file "$(ovpn_instance_meta_path "${iname}")"
    fi
  done

  ovpn_enable_ip_forward
  ovpn_install_nat_helper
  ovpn_rollback_mark_service ovpn-panel-nat.service
fi

# --- Stage panel files BEFORE any service is started -------------------------
# The generated OpenVPN configs reference
# $OVPN_PANEL_DIR/scripts/hooks/auth-user-pass-verify.sh (plus client-connect /
# client-disconnect). Those files must exist and be executable before
# openvpn-server@<name> is enabled below, otherwise OpenVPN aborts at startup.
ovpn_panel_stage_files "${SRC_DIR}"
if [[ -d "${OVPN_PANEL_DIR}/.venv" ]]; then
  ovpn_ok "panel app already installed at ${OVPN_PANEL_DIR} — reusing"
else
  ovpn_rollback_mark_dir "${OVPN_PANEL_DIR}"
fi

# --- OpenVPN core: start services (after hooks are staged) -------------------
if [[ "${INSTALL_OPENVPN}" -eq 1 ]]; then
  # Fatal if any hook referenced by a generated config is missing/not executable.
  ovpn_panel_verify_hooks

  # Second pass: start services now that hooks + NAT helper + PKI exist.
  for spec in "${_instances[@]}"; do
    [[ -n "${spec}" ]] || continue
    IFS=':' read -r iname _ _ _ <<<"${spec}"
    ovpn_rollback_mark_service "openvpn-server@${iname:-server}"
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

# --- Panel: virtualenv + installed package -----------------------------------
if [[ ! -d "${OVPN_PANEL_DIR}/.venv/bin/python" ]]; then
  ovpn_panel_install_app "${SRC_DIR}"
  ovpn_rollback_mark_dir "${OVPN_PANEL_DIR}"
else
  ovpn_log "Refreshing panel dependencies"
  "${OVPN_PANEL_VENV}/bin/pip" install --upgrade "${OVPN_PANEL_DIR}"
fi
ovpn_panel_generate_secrets
ovpn_rollback_mark_file "${OVPN_PANEL_CONFIG_DIR}/panel.env"
ovpn_log "Initialising database"            # idempotent — FATAL on failure
OVPN_PANEL_CONFIG_DIR="${OVPN_PANEL_CONFIG_DIR}" \
  "${OVPN_PANEL_VENV}/bin/python" -m scripts.cli init-db

ovpn_log "Recording install configuration"
ovpn_env_set OVPN_PANEL_PORT "${PANEL_PORT}"
ovpn_env_set OVPN_ADMIN_USER "${ADMIN_USER}"
if [[ -n "${PANEL_HOST}" ]]; then
  ovpn_env_set OVPN_PANEL_HOST "${PANEL_HOST}"
fi
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
ovpn_rollback_mark_file "${OVPN_SYSCONF}"

ovpn_panel_install_units
ovpn_rollback_mark_service ovpn-panel-helper.service
ovpn_rollback_mark_service ovpn-panel.service
ovpn_rollback_mark_file /etc/systemd/system/ovpn-panel.service
ovpn_rollback_mark_file /etc/systemd/system/ovpn-panel-helper.service
ovpn_rollback_mark_file /etc/systemd/system/ovpn-panel-agent.service
ovpn_rollback_mark_file /etc/systemd/system/ovpn-panel-agent@.service
ovpn_panel_install_cli

ovpn_log "Creating admin account"
# create-admin fails (exit 1) if the user already exists (re-run) — reset the
# password instead so the re-run succeeds while keeping user data. The failure
# is captured explicitly (in a subshell, so neither `set -e` nor the ERR trap
# aborts the install before we handle it).
set +e
ADMIN_OUT="$(
  trap - ERR
  set +e
  OVPN_PANEL_CONFIG_DIR="${OVPN_PANEL_CONFIG_DIR}" \
    "${OVPN_PANEL_VENV}/bin/python" -m scripts.cli create-admin "${ADMIN_USER}" \
    --password "${ADMIN_PASSWORD}" 2>&1
)"
ADMIN_RC=$?
set -e
if [[ "${ADMIN_RC}" -ne 0 && "${ADMIN_OUT}" == *"already exists"* ]]; then
  ovpn_ok "admin '${ADMIN_USER}' already exists — resetting password"
  OVPN_PANEL_CONFIG_DIR="${OVPN_PANEL_CONFIG_DIR}" \
    "${OVPN_PANEL_VENV}/bin/python" -m scripts.cli reset-password "${ADMIN_USER}" \
    --password "${ADMIN_PASSWORD}"
else
  printf '%s\n' "${ADMIN_OUT}"
  [[ "${ADMIN_RC}" -eq 0 ]] || ovpn_die "create-admin failed (exit ${ADMIN_RC})"
fi

# Persist credentials (mode 600) and show every access detail once at the end.
ovpn_panel_write_credentials "${ADMIN_USER}" "${ADMIN_PASSWORD}" \
  "${PANEL_PORT}" "${PANEL_HOST}"
ovpn_rollback_mark_file "${OVPN_PANEL_CONFIG_DIR}/credentials.txt"
ovpn_panel_print_access "${ADMIN_PASSWORD}"
if [[ "${ADMIN_PASSWORD_GENERATED}" -eq 1 && "${ADMIN_PASSWORD_FLAG}" -eq 0 ]]; then
  ovpn_warn "A strong random admin password was generated; it is shown above ONCE."
fi
ovpn_log "Installation complete."
