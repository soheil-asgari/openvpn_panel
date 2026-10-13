#!/usr/bin/env bash
#
# @SLUG@ one-line installer  (version @VERSION@)
#
# Usage (fresh, bare server):
#   bash <(curl -fsSL https://github.com/@REPO@/releases/latest/download/install.sh)
#   curl -fsSL https://github.com/@REPO@/releases/latest/download/install.sh \
#     | sudo bash -s -- --yes                                   # non-interactive
#
# Subcommands:
#   install | update | uninstall | repair | status | --version
#
# Flags/env (non-interactive):
#   --user NAME --password PW --port N --path /admin --domain host --email a@b
#   --yes   (accept defaults / skip confirmation)
#
# This file is a TEMPLATE. CI substitutes @VERSION@, @TAG@, @SLUG@ and @REPO@ at
# release time; never edit the released copy. It only ever downloads code from
# the tagged release, never from a mutable branch.
set -Eeuo pipefail

# --- metadata (substituted at release time) ----------------------------------
SLUG="@SLUG@"
VERSION="@VERSION@"
TAG="@TAG@"
REPO="@REPO@"
RELEASE_BASE="https://github.com/${REPO}/releases/download/${TAG}"
TARBALL="${SLUG}-${VERSION}.tar.gz"

INSTALL_DIR="/opt/${SLUG}"
CONFIG_DIR="/etc/${SLUG}"
LOG_FILE="/var/log/${SLUG}-install.log"
SUMMARY_FILE="/root/${SLUG}-install-info.txt"
SERVICE_USER="${SLUG}"

# --- output ------------------------------------------------------------------
if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_BLUE=$'\033[1;34m'; C_GREEN=$'\033[1;32m'
  C_YELLOW=$'\033[1;33m'; C_RED=$'\033[1;31m'
else
  C_RESET=''; C_BLUE=''; C_GREEN=''; C_YELLOW=''; C_RED=''
fi
log()  { printf '%s==>%s %s\n' "${C_BLUE}" "${C_RESET}" "$*" | tee -a "${LOG_FILE}" >&2; }
ok()   { printf '%s  ok%s %s\n' "${C_GREEN}" "${C_RESET}" "$*" | tee -a "${LOG_FILE}" >&2; }
warn() { printf '%swarn:%s %s\n' "${C_YELLOW}" "${C_RESET}" "$*" | tee -a "${LOG_FILE}" >&2; }
die()  { printf '%serror:%s %s\n' "${C_RED}" "${C_RESET}" "$*" | tee -a "${LOG_FILE}" >&2; exit 1; }

# --- cleanup trap ------------------------------------------------------------
TMP_DIR=""
cleanup() {
  local rc=$?
  if [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]]; then
    rm -rf "${TMP_DIR}"
  fi
  if [[ ${rc} -ne 0 ]]; then
    warn "installer exited with status ${rc}; see ${LOG_FILE}"
  fi
}
trap cleanup EXIT

# --- defaults ----------------------------------------------------------------
ASSUME_YES=0
ADMIN_USER=""
ADMIN_PASSWORD=""
PANEL_PORT=""
ADMIN_PATH=""
DOMAIN=""
EMAIL=""
SUBCMD=""

usage() {
  cat <<EOF
${SLUG} installer (version ${VERSION})

Usage: install.sh [subcommand] [options]

Subcommands:
  install (default)   install or repair the panel on this server
  update              update to this release, keeping data
  uninstall           remove services; add --purge to delete data too
  repair              re-apply units, permissions and firewall
  status              print service and panel status
  --version           print the installer version and exit

Options:
  -y, --yes           non-interactive; accept defaults / provided values
      --user NAME     admin username
      --password PW   admin password (empty = strong random)
      --port N        panel port
      --path PATH     custom admin path prefix (default /)
      --domain HOST   public hostname (enables Let's Encrypt when --email given)
      --email ADDR    contact email for Let's Encrypt
  -h, --help          show this help
EOF
}

require_root() {
  [[ "${EUID}" -eq 0 ]] || die "must run as root (try: sudo bash -s -- ...)"
}

# --- preflight ---------------------------------------------------------------
free_mb() { df -Pm "$1" 2>/dev/null | awk 'NR==2{print $4}'; }

preflight() {
  log "Preflight checks"
  [[ "$(uname -m)" =~ ^(x86_64|aarch64|armv7l)$ ]] || die "unsupported architecture: $(uname -m)"
  ok "architecture: $(uname -m)"

  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-}" in
      ubuntu) [[ "${VERSION_ID:-}" =~ ^(22\.04|24\.04)$ ]] || warn "untested Ubuntu ${VERSION_ID:-}; continuing" ;;
      debian) [[ "${VERSION_ID:-}" == 12 ]] || warn "untested Debian ${VERSION_ID:-}; continuing" ;;
      *) warn "untested OS '${ID:-unknown}'; continuing" ;;
    esac
    ok "OS: ${PRETTY_NAME:-unknown}"
  else
    warn "/etc/os-release missing; cannot verify OS"
  fi

  local mem disk
  mem="$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)"
  [[ "${mem}" -ge 450 ]] || die "at least 512 MB RAM required (found ${mem} MB)"
  ok "RAM: ${mem} MB"

  disk="$(free_mb /)"
  [[ "${disk:-0}" -ge 1500 ]] || die "at least 2 GB free disk required (found ${disk:-0} MB)"
  ok "disk free: ${disk} MB"

  log "Checking network, DNS and time"
  command -v curl >/dev/null 2>&1 || { apt-get update -y >/dev/null 2>&1 || true; apt-get install -y curl >/dev/null 2>&1 || true; }
  curl -fsS --max-time 15 -o /dev/null "https://github.com" || die "no internet access (cannot reach github.com)"
  ok "internet reachable"
  getent hosts github.com >/dev/null 2>&1 || die "DNS resolution failed for github.com"
  ok "DNS resolves"
  if command -v timedatectl >/dev/null 2>&1; then
    timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes \
      || { warn "system clock is not NTP-synchronised; enabling NTP"; timedatectl set-ntp true 2>/dev/null || true; }
  fi
}

# --- legal disclaimer --------------------------------------------------------
disclaimer() {
  cat <<EOF
==============================================================================
  ${SLUG} — Legal notice
==============================================================================
This software is provided "AS IS", under the MIT license, WITHOUT WARRANTY OF
ANY KIND. It is intended for LAWFUL USE ONLY. You are solely responsible for
complying with the laws and the hosting provider's terms in the country where
this server is located and where it is used. The authors are not responsible
for any misuse.

Third-party tools installed or downloaded at install time (OpenVPN, WireGuard,
Backhaul, sing-box, ocserv, ...) are governed by their own licenses — see
THIRD_PARTY_LICENSES.md in the release.
==============================================================================
EOF
  if [[ "${ASSUME_YES}" -eq 1 ]]; then
    ok "disclaimer accepted via --yes"
    return 0
  fi
  local answer=""
  read -r -p 'Type "yes" to accept and continue: ' answer || true
  [[ "${answer}" == "yes" ]] || die "aborted: disclaimer not accepted"
}

# --- interactive prompts -----------------------------------------------------
prompt() {
  local __var="$1" __q="$2" __def="$3" __ans=""
  if [[ "${ASSUME_YES}" -eq 1 || ! -t 0 ]]; then printf -v "${__var}" '%s' "${__def}"; return 0; fi
  read -r -p "${__q} [${__def}]: " __ans || true
  printf -v "${__var}" '%s' "${__ans:-${__def}}"
}

# Strong random password: >=16 chars covering upper, lower, digit and symbol.
strong_password() {
  local len=24
  local u='ABCDEFGHJKLMNPQRSTUVWXYZ' l='abcdefghijkmnopqrstuvwxyz'
  local d='23456789' s='!@#$%^*-_+='
  local pw tail=""
  pw="${u:$((RANDOM % ${#u})):1}${l:$((RANDOM % ${#l})):1}"
  pw+="${d:$((RANDOM % ${#d})):1}${s:$((RANDOM % ${#s})):1}"
  if command -v openssl >/dev/null 2>&1; then
    tail="$(openssl rand -base64 32 2>/dev/null | tr -dc 'A-Za-z0-9' | cut -c1-40)"
  fi
  while [[ ${#tail} -lt $((len - 4)) ]]; do
    tail+="${u:$((RANDOM % ${#u})):1}${l:$((RANDOM % ${#l})):1}${d:$((RANDOM % ${#d})):1}"
  done
  pw+="${tail}"
  printf '%s' "${pw:0:len}"
}

is_strong() {
  local pw="$1"
  [[ ${#pw} -ge 16 ]] || return 1
  [[ "${pw}" == *[A-Z]* ]] || return 1
  [[ "${pw}" == *[a-z]* ]] || return 1
  [[ "${pw}" == *[0-9]* ]] || return 1
  [[ "${pw}" == *[!A-Za-z0-9]* ]] || return 1
}

collect_config() {
  [[ -n "${PANEL_PORT}" ]] || prompt PANEL_PORT "Panel port" "8080"
  [[ "${PANEL_PORT}" =~ ^[0-9]+$ && "${PANEL_PORT}" -ge 1 && "${PANEL_PORT}" -le 65535 ]] || die "invalid panel port: ${PANEL_PORT}"
  [[ -n "${ADMIN_USER}" ]] || prompt ADMIN_USER "Admin username" "admin"
  [[ "${ADMIN_USER}" =~ ^[A-Za-z0-9_.-]{3,32}$ ]] || die "invalid admin username: ${ADMIN_USER}"
  [[ -n "${ADMIN_PATH}" ]] || prompt ADMIN_PATH "Custom admin path" "/"

  ADMIN_PASSWORD_GENERATED=0
  if [[ -z "${ADMIN_PASSWORD}" && "${ASSUME_YES}" -eq 0 && -t 0 ]]; then
    local pw=""
    read -r -s -p "Admin password (empty = strong random): " pw; printf '\n'
    ADMIN_PASSWORD="${pw}"
  fi
  # Empty input (Enter, --password "" or omitted) => strong random password.
  if [[ -z "${ADMIN_PASSWORD}" ]]; then
    ADMIN_PASSWORD="$(strong_password)"
    ADMIN_PASSWORD_GENERATED=1
  fi
  # Only user-typed / flag-provided passwords are checked, and only as advice:
  # any password the operator chooses is accepted.
  if [[ "${ADMIN_PASSWORD_GENERATED}" -eq 0 ]]; then
    is_strong "${ADMIN_PASSWORD}" \
      || warn "admin password is weak (want >=16 chars, upper, lower, digit, symbol); accepting it anyway"
  fi

  [[ -n "${DOMAIN}" ]] || prompt DOMAIN "Public domain (empty = none)" ""
  if [[ -n "${DOMAIN}" && -z "${EMAIL}" ]]; then
    prompt EMAIL "Contact email for Let's Encrypt (empty = skip TLS)" ""
  fi
}

# --- package installation ----------------------------------------------------
apt_retry() {
  local n=0
  while true; do
    n=$((n+1))
    if DEBIAN_FRONTEND=noninteractive apt-get "$@"; then return 0; fi
    if [[ ${n} -ge 5 ]]; then die "apt-get $* failed after ${n} attempts"; fi
    warn "apt-get $* failed (attempt ${n}); waiting for locks and retrying"
    # Wait for other apt/dpkg locks to be released.
    for _ in 1 2 3 4 5 6; do
      if ! fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; then break; fi
      sleep 5
    done
    sleep 3
  done
}

install_prereqs() {
  log "Installing prerequisites (non-interactive)"
  export DEBIAN_FRONTEND=noninteractive
  apt_retry update -y
  apt_retry install -y --no-install-recommends \
    ca-certificates curl openssl iproute2 python3 python3-venv python3-pip \
    sqlite3 rsync iptables nftables openvpn easy-rsa wireguard-tools \
    logrotate cron xz-utils
  # certbot is optional; a failure must not abort the install.
  apt_retry install -y --no-install-recommends certbot || warn "certbot not installed (optional)"
  ok "prerequisites installed"
}

tune_kernel() {
  log "Applying kernel/sysctl tuning"
  modprobe wireguard 2>/dev/null || warn "wireguard module not available (optional)"
  cat >"/etc/sysctl.d/99-${SLUG}.conf" <<EOF
net.ipv4.ip_forward=1
net.ipv6.conf.all.forwarding=1
EOF
  # BBR is optional; only enable when the module exists.
  if modprobe tcp_bbr 2>/dev/null; then
    {
      printf 'net.core.default_qdisc=fq\n'
      printf 'net.ipv4.tcp_congestion_control=bbr\n'
    } >>"/etc/sysctl.d/99-${SLUG}.conf"
    ok "BBR congestion control enabled"
  else
    warn "tcp_bbr module unavailable; leaving default congestion control"
  fi
  sysctl --system >/dev/null 2>&1 || warn "sysctl --system reported errors"
  ok "IP forwarding enabled"
}

# --- release download + verification -----------------------------------------
download_and_verify() {
  log "Downloading ${TARBALL} and verifying SHA256"
  TMP_DIR="$(mktemp -d)"
  local base="${OVPN_RELEASE_BASE:-${RELEASE_BASE}}"
  # Guard against a mutated branch URL sneaking in.
  case "${base}" in
    */main/*|*/master/*) die "refusing to install from a mutable branch: ${base}" ;;
  esac

  if [[ "${base}" == file://* ]]; then
    # TEST SEAM (env-gated, OVPN_RELEASE_BASE only): serve the release from a
    # local directory. Used by CI to test the unmodified released installer
    # against freshly built artifacts; never reachable in production.
    local dir="${base#file://}"
    [[ -d "${dir}" ]] || die "local release dir missing: ${dir}"
    local real_dir; real_dir="$(cd "${dir}" && pwd)"
    cp -f "${real_dir}/${TARBALL}" "${TMP_DIR}/${TARBALL}" \
      || die "local tarball missing: ${real_dir}/${TARBALL}"
    cp -f "${real_dir}/SHA256SUMS" "${TMP_DIR}/SHA256SUMS" \
      || die "local checksums missing: ${real_dir}/SHA256SUMS"
  else
    curl -fsSL --max-time 120 -o "${TMP_DIR}/${TARBALL}" "${base}/${TARBALL}" \
      || die "download failed: ${base}/${TARBALL}"
    curl -fsSL --max-time 60  -o "${TMP_DIR}/SHA256SUMS" "${base}/SHA256SUMS" \
      || die "download failed: ${base}/SHA256SUMS"
  fi

  local expected actual
  expected="$(grep -E "[[:space:]]${TARBALL}\$" "${TMP_DIR}/SHA256SUMS" | awk '{print $1}')"
  [[ -n "${expected}" ]] || die "no checksum found for ${TARBALL} in SHA256SUMS"
  actual="$(sha256sum "${TMP_DIR}/${TARBALL}" | awk '{print $1}')"
  [[ "${actual}" == "${expected}" ]] || die "SHA256 mismatch for ${TARBALL} (expected ${expected}, got ${actual}) — aborting"
  ok "checksum verified"

  mkdir -p "${TMP_DIR}/src"
  tar -xzf "${TMP_DIR}/${TARBALL}" -C "${TMP_DIR}/src"
  [[ -f "${TMP_DIR}/src/scripts/install.sh" ]] || die "tarball does not contain scripts/install.sh"
  ok "release unpacked (tag ${TAG})"
}

# --- delegate core install to the released scripts ---------------------------
core_install() {
  local src="${TMP_DIR}/src"
  local -a args=(--yes
    --admin-user "${ADMIN_USER}"
    --admin-password "${ADMIN_PASSWORD}"
    --panel-port "${PANEL_PORT}")
  [[ -n "${DOMAIN}" ]] && args+=(--host "${DOMAIN}")
  log "Running core installer from the verified release"
  # Run from the extracted release; install.sh resolves its own libs. Capture
  # combined output so the *inner* installer's error lines (not just "see log")
  # can be surfaced to the caller on failure.
  local out rc
  out="$( cd "${src}" && bash ./scripts/install.sh "${args[@]}" 2>&1 )" && rc=0 || rc=$?
  printf '%s\n' "${out}" | tee -a "${LOG_FILE}" >&2
  if [[ "${rc}" -ne 0 ]]; then
    warn "core installer failed (exit ${rc})"
    printf 'error: inner installer output (last 30 lines):\n' >&2
    printf '%s\n' "${out}" | tail -n 30 | tee -a "${LOG_FILE}" >&2
    die "core installer failed; see the error lines above and ${LOG_FILE}"
  fi
  ok "core installer finished"
}

apply_extras() {
  log "Applying extras (admin path, logrotate, backups, self-test)"
  # Persist the custom admin path.
  if [[ -n "${ADMIN_PATH}" && "${ADMIN_PATH}" != "/" ]]; then
    if [[ -f "${CONFIG_DIR}/panel.env" ]] && ! grep -q '^OVPN_ADMIN_PREFIX=' "${CONFIG_DIR}/panel.env"; then
      printf 'OVPN_ADMIN_PREFIX=%s\n' "${ADMIN_PATH}" >>"${CONFIG_DIR}/panel.env"
      chmod 0640 "${CONFIG_DIR}/panel.env" 2>/dev/null || true
    fi
  fi

  # logrotate for the panel log.
  cat >"/etc/logrotate.d/${SLUG}" <<EOF
/var/log/${SLUG}*.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
EOF

  # Daily encrypted backup via a cron drop-in (uses the CLI; requires the
  # backup password to be provided by the operator — no secret is stored here).
  install -d -m 0750 -o root -g root "/etc/${SLUG}"
  cat >"/etc/cron.d/${SLUG}-backup" <<EOF
# Daily backup hook. Set OVPN_BACKUP_PASSWORD in /etc/${SLUG}/backup.env first.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
30 3 * * * root [ -f /etc/${SLUG}/backup.env ] && . /etc/${SLUG}/backup.env && ${INSTALL_DIR}/.venv/bin/${SLUG} backup --output /var/backups/${SLUG}/ 2>/dev/null || true
EOF
  chmod 0644 "/etc/cron.d/${SLUG}-backup"
  install -d -m 0700 "/var/backups/${SLUG}"

  # Ensure the installed CLI is on PATH and points at the shared libs.
  if [[ -x "${INSTALL_DIR}/scripts/ovpn-panel-menu" ]]; then
    install -m 0755 "${INSTALL_DIR}/scripts/ovpn-panel-menu" "/usr/local/bin/${SLUG}"
  fi

  systemctl daemon-reload 2>/dev/null || true
  ok "extras applied"
}

self_test() {
  log "Post-install self-test"
  local failures=0
  if systemctl is-active --quiet "${SLUG}.service"; then ok "panel service active"; else warn "panel service not active"; failures=$((failures+1)); fi
  if systemctl is-active --quiet "${SLUG}-helper.service"; then ok "helper service active"; else warn "helper service not active"; failures=$((failures+1)); fi
  if curl -fsS --max-time 5 "http://127.0.0.1:${PANEL_PORT}/" >/dev/null 2>&1; then ok "panel responds on port ${PANEL_PORT}"; else warn "panel did not respond on 127.0.0.1:${PANEL_PORT}"; failures=$((failures+1)); fi
  if [[ ${failures} -eq 0 ]]; then ok "self-test passed"; else warn "self-test reported ${failures} issue(s); check ${LOG_FILE}"; fi
}

write_summary() {
  log "Writing access summary to ${SUMMARY_FILE}"
  local host="${DOMAIN:-<server-ip>}"
  {
    printf '%s installation summary\n' "${SLUG}"
    printf 'Version       : %s\n' "${VERSION}"
    printf 'Panel URL     : http://%s:%s%s\n' "${host}" "${PANEL_PORT}" "${ADMIN_PATH:-/}"
    printf 'Admin user    : %s\n' "${ADMIN_USER}"
    printf 'Admin password: %s\n' "${ADMIN_PASSWORD}"
    printf 'Config/env    : %s/panel.env\n' "${CONFIG_DIR}"
    printf 'Install dir   : %s\n' "${INSTALL_DIR}"
    printf 'Log           : %s\n' "${LOG_FILE}"
    printf 'CLI           : sudo %s\n' "${SLUG}"
    printf '\nNext steps: open the panel URL, log in, then create a VPN user\n'
    printf 'and (optionally) add a second server as a node. See docs/.\n'
  } >"${SUMMARY_FILE}"
  chmod 0600 "${SUMMARY_FILE}"
  # Show every access detail once, at the very end of the install.
  printf '\n'
  printf '================================================================\n'
  cat "${SUMMARY_FILE}" >&2
  printf '================================================================\n'
  if [[ "${ADMIN_PASSWORD_GENERATED:-0}" -eq 1 ]]; then
    warn "The admin password above was randomly generated; it is shown ONCE."
  fi
  ok "summary saved to ${SUMMARY_FILE} (mode 600)"
}

do_install() {
  require_root
  preflight
  disclaimer
  collect_config
  install_prereqs
  tune_kernel
  download_and_verify
  core_install
  PANEL_PORT="${PANEL_PORT:-8080}"
  apply_extras
  self_test
  write_summary
  ok "Installation complete. Read ${SUMMARY_FILE} for credentials."
}

do_repair() {
  require_root
  log "Repair: re-applying permissions, units and firewall"
  if [[ ! -d "${INSTALL_DIR}" ]]; then die "not installed (missing ${INSTALL_DIR})"; fi
  chmod 0640 "${CONFIG_DIR}/panel.env" 2>/dev/null || true
  systemctl daemon-reload 2>/dev/null || true
  systemctl restart "${SLUG}-helper.service" 2>/dev/null || true
  systemctl restart "${SLUG}.service" 2>/dev/null || true
  self_test
  ok "repair finished"
}

do_update() {
  require_root
  disclaimer
  download_and_verify
  log "Updating files and dependencies (data preserved)"
  ( cd "${TMP_DIR}/src" && bash ./scripts/update.sh ) 2>&1 | tee -a "${LOG_FILE}" || die "update step failed"
  ok "update finished"
}

do_uninstall() {
  require_root
  local purge=0
  [[ "${1:-}" == "--purge" ]] && purge=1
  if [[ "${purge}" -eq 0 && "${ASSUME_YES}" -eq 0 && -t 0 ]]; then
    local ans=""; read -r -p "Also delete ALL data and config? [y/N]: " ans || true
    [[ "${ans}" =~ ^[yY]$ ]] && purge=1
  fi
  log "Stopping and removing ${SLUG} services"
  for u in "${SLUG}.service" "${SLUG}-helper.service" "${SLUG}-agent.service" "${SLUG}-agent@.service" "${SLUG}-nat.service"; do
    systemctl disable --now "${u}" 2>/dev/null || true
  done
  # Stop and disable every OpenVPN instance created by the installer.
  local _meta _inst
  for _meta in /etc/openvpn/instances/*.env; do
    [[ -f "${_meta}" ]] || continue
    # shellcheck disable=SC1090
    _inst="$( . "${_meta}"; printf '%s' "${OVPN_INSTANCE_NAME:-}" )"
    if [[ -n "${_inst}" ]]; then
      systemctl disable --now "openvpn-server@${_inst}" >/dev/null 2>&1 || true
    fi
  done
  rm -f "/etc/systemd/system/${SLUG}.service" \
        "/etc/systemd/system/${SLUG}-helper.service" \
        "/etc/systemd/system/${SLUG}-agent.service" \
        "/etc/systemd/system/${SLUG}-agent@.service" \
        "/etc/systemd/system/${SLUG}-nat.service" \
        "/etc/logrotate.d/${SLUG}" "/etc/cron.d/${SLUG}-backup"
  systemctl daemon-reload 2>/dev/null || true
  rm -rf "${INSTALL_DIR}" "/run/${SLUG}"
  if [[ "${purge}" -eq 1 ]]; then
    warn "purging data and configuration"
    rm -f /etc/systemd/system/openvpn-server@.service.d/ovpn-panel.conf 2>/dev/null || true
    rmdir /etc/systemd/system/openvpn-server@.service.d 2>/dev/null || true
    rm -rf "${CONFIG_DIR}" "/var/lib/${SLUG}" "/var/backups/${SLUG}"
    rm -rf /etc/openvpn/pki /etc/openvpn/instances /etc/openvpn/ccd /etc/openvpn/clients
    rm -f /etc/sysctl.d/99-${SLUG}.conf 2>/dev/null || true
    userdel "${SERVICE_USER}" 2>/dev/null || true
  else
    warn "kept ${CONFIG_DIR} and /var/lib/${SLUG} (use --purge to delete)"
  fi
  ok "uninstall complete"
}

do_status() {
  printf '%s status\n' "${SLUG}"
  printf '  version     : %s\n' "${VERSION}"
  printf '  panel       : %s\n' "$(systemctl is-active "${SLUG}.service" 2>/dev/null || echo unknown)"
  printf '  helper      : %s\n' "$(systemctl is-active "${SLUG}-helper.service" 2>/dev/null || echo unknown)"
  if [[ -f "${CONFIG_DIR}/panel.env" ]]; then
    local port; port="$(grep -E '^OVPN_PANEL_PORT=' "${CONFIG_DIR}/panel.env" | cut -d= -f2- || true)"
    printf '  panel port  : %s\n' "${port:-8080}"
  fi
}

# --- argument parsing --------------------------------------------------------
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      install|update|uninstall|repair|status) SUBCMD="$1"; shift ;;
      -y|--yes) ASSUME_YES=1; shift ;;
      --user|--admin-user) ADMIN_USER="${2:-}"; shift 2 ;;
      --password|--admin-password) ADMIN_PASSWORD="${2:-}"; shift 2 ;;
      --port|--panel-port) PANEL_PORT="${2:-}"; shift 2 ;;
      --path) ADMIN_PATH="${2:-}"; shift 2 ;;
      --domain) DOMAIN="${2:-}"; shift 2 ;;
      --email) EMAIL="${2:-}"; shift 2 ;;
      --version) printf '%s installer %s\n' "${SLUG}" "${VERSION}"; exit 0 ;;
      -h|--help) usage; exit 0 ;;
      --purge) PURGE_FLAG="--purge"; shift ;;
      *) die "unknown option: $1 (see --help)" ;;
    esac
  done
}

main() {
  PURGE_FLAG=""
  parse_args "$@"
  # Allow env overrides for non-interactive use.
  ADMIN_USER="${ADMIN_USER:-${OVPN_ADMIN_USER:-}}"
  PANEL_PORT="${PANEL_PORT:-${OVPN_PANEL_PORT:-}}"
  case "${SUBCMD:-install}" in
    install)   do_install ;;
    repair)    do_repair ;;
    update)    do_update ;;
    uninstall) do_uninstall "${PURGE_FLAG}" ;;
    status)    do_status ;;
    *)         die "unknown subcommand: ${SUBCMD}" ;;
  esac
}

main "$@"
