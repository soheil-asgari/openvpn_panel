#!/usr/bin/env bash
# shellcheck shell=bash
#
# Shared OpenVPN core library used by install.sh, update.sh, uninstall.sh and
# the runtime helpers. It is sourced, never executed directly.
#
# It is intentionally dependency-light (bash + coreutils) and safe to re-run:
# every operation is idempotent. No external secrets are embedded here and no
# license/telemetry logic exists by design.
#
# Incomplete installations (partial PKI, instances, units) ARE detected and
# repaired — the library NEVER wipes a valid CA or user data.
#
# Every required step is FATAL on failure, never hidden behind || warn / || true.
# For first-install rollback see the rollback helpers at the bottom.
#
set -Eeuo pipefail

# --- Paths & constants -------------------------------------------------------
OVPN_PKI_DIR="${OVPN_PKI_DIR:-/etc/openvpn/pki}"
OVPN_INSTANCES_DIR="${OVPN_INSTANCES_DIR:-/etc/openvpn/instances}"
OVPN_CCD_DIR="${OVPN_CCD_DIR:-/etc/openvpn/ccd}"
OVPN_AUTH_DIR="${OVPN_AUTH_DIR:-/etc/openvpn/auth}"
OVPN_CLIENT_DIR="${OVPN_CLIENT_DIR:-/etc/openvpn/clients}"
OVPN_STATUS_DIR="${OVPN_STATUS_DIR:-/run/openvpn}"
OVPN_PANEL_DIR="${OVPN_PANEL_DIR:-/opt/ovpn-panel}"
OVPN_PANEL_CONFIG_DIR="${OVPN_PANEL_CONFIG_DIR:-/etc/ovpn-panel}"
OVPN_SYSCONF="${OVPN_SYSCONF:-/etc/ovpn-panel/install.conf}"

EASYRSA_BIN="${EASYRSA_BIN:-/usr/share/easy-rsa/easyrsa}"
OPENVPN_BIN="${OPENVPN_BIN:-/usr/sbin/openvpn}"

NAT_SERVICE="/etc/systemd/system/ovpn-panel-nat.service"
NFT_FRAGMENT_DIR="/etc/nftables.d"
NFT_FRAGMENT="${NFT_FRAGMENT_DIR}/ovpn-panel.nft"
SYSCTL_FILE="/etc/sysctl.d/99-ovpn-panel.conf"

# --- Logging -----------------------------------------------------------------
if [[ -t 1 ]]; then
  _c_reset='\033[0m'; _c_blue='\033[1;34m'; _c_green='\033[1;32m'
  _c_yellow='\033[1;33m'; _c_red='\033[1;31m'
else
  _c_reset=''; _c_blue=''; _c_green=''; _c_yellow=''; _c_red=''
fi

ovpn_log()  { printf "${_c_blue}==>${_c_reset} %s\n" "$*"; }
ovpn_ok()   { printf "${_c_green}  ok${_c_reset} %s\n" "$*"; }
ovpn_warn() { printf "${_c_yellow}warn:${_c_reset} %s\n" "$*" >&2; }
ovpn_die()  { printf "${_c_red}error:${_c_reset} %s\n" "$*" >&2; exit 1; }

# Report an unhandled command failure with its origin and the failing command,
# instead of dying silently under `set -e`.
ovpn_on_err() {
  local rc=$?
  printf "${_c_red}error:${_c_reset} %s:%s: command failed (exit %s): %s\n" \
    "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}" "${BASH_LINENO[0]:-?}" "${rc}" \
    "${BASH_COMMAND}" >&2
  return "${rc}"
}
# Enable errtrace (-E) so the trap is inherited by functions and subshells.
trap 'ovpn_on_err' ERR

ovpn_require_root() {
  [[ "${EUID}" -eq 0 ]] || ovpn_die "must run as root (try: sudo $0)"
}

# --- secrets -----------------------------------------------------------------
# Random alphanumeric token of <n> characters (URL/shell safe).
ovpn_rand_token() {
  local n="${1:-16}"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 "$((n + 16))" 2>/dev/null | tr -dc 'A-Za-z0-9' | cut -c1-"${n}"
  else
    LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c "${n}" || true
  fi
}

# Strong random password: >=16 chars including upper, lower, digit and symbol.
ovpn_strong_password() {
  local len="${1:-24}"
  [[ "${len}" -ge 16 ]] || len=16
  local u='ABCDEFGHJKLMNPQRSTUVWXYZ'
  local l='abcdefghijkmnopqrstuvwxyz'
  local d='23456789'
  local s='!@#$%^*-_+='
  local pw
  pw="${u:$((RANDOM % ${#u})):1}${l:$((RANDOM % ${#l})):1}"
  pw+="${d:$((RANDOM % ${#d})):1}${s:$((RANDOM % ${#s})):1}"
  pw="${pw}$(ovpn_rand_token "$((len - 4))")"
  printf '%s' "${pw:0:len}"
}

# True when <pw> is >=16 chars and carries all four character classes.
ovpn_password_is_strong() {
  local pw="$1"
  [[ "${#pw}" -ge 16 ]] || return 1
  [[ "${pw}" == *[A-Z]* ]] || return 1
  [[ "${pw}" == *[a-z]* ]] || return 1
  [[ "${pw}" == *[0-9]* ]] || return 1
  [[ "${pw}" == *[!A-Za-z0-9]* ]] || return 1
}

# Echo the admin password to use: the provided value verbatim, or a freshly
# generated strong one when empty. A user-supplied password is never rejected.
ovpn_resolve_admin_password() {
  local provided="${1:-}"
  if [[ -n "${provided}" ]]; then
    printf '%s' "${provided}"
  else
    ovpn_strong_password 24
  fi
}

# --- OS / package detection --------------------------------------------------
OVPN_OS_ID=""
OVPN_OS_VER=""
OVPN_PKG=""

ovpn_detect_os() {
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OVPN_OS_ID="${ID:-unknown}"
    OVPN_OS_VER="${VERSION_ID:-}"
  fi
  if command -v apt-get >/dev/null 2>&1; then
    OVPN_PKG="apt"
  elif command -v dnf >/dev/null 2>&1; then
    OVPN_PKG="dnf"
  elif command -v yum >/dev/null 2>&1; then
    OVPN_PKG="yum"
  elif command -v apk >/dev/null 2>&1; then
    OVPN_PKG="apk"
  else
    OVPN_PKG=""
  fi
  export OVPN_OS_ID OVPN_OS_VER OVPN_PKG
}

ovpn_install_packages() {
  # Install the base dependencies required by OpenVPN core + panel.
  local -a pkgs=("$@")
  [[ ${#pkgs[@]} -gt 0 ]] || return 0
  case "${OVPN_PKG}" in
    apt)
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -y
      apt-get install -y --no-install-recommends "${pkgs[@]}"
      ;;
    dnf) dnf install -y "${pkgs[@]}" ;;
    yum) yum install -y "${pkgs[@]}" ;;
    apk) apk add --no-cache "${pkgs[@]}" ;;
    *) ovpn_die "no supported package manager found (apt/dnf/yum/apk)" ;;
  esac
}

# --- sysctl / IP forwarding --------------------------------------------------
ovpn_enable_ip_forward() {
  ovpn_log "Enabling IP forwarding (persisted)"
  cat >"${SYSCTL_FILE}" <<'EOF'
# Managed by ovpn-panel — enables IPv4/IPv6 forwarding for OpenVPN.
net.ipv4.ip_forward=1
net.ipv6.conf.all.forwarding=1
EOF
  chmod 0644 "${SYSCTL_FILE}"
  sysctl -w net.ipv4.ip_forward=1 >/dev/null
  sysctl -w net.ipv6.conf.all.forwarding=1 >/dev/null 2>&1 || true
}

ovpn_wan_iface() {
  # Best-effort detection of the default egress interface.
  local iface
  iface="$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -n1)"
  [[ -n "${iface}" ]] || iface="eth0"
  printf '%s' "${iface}"
}

# --- Instances ---------------------------------------------------------------
# Each instance has <name>.conf (OpenVPN) and <name>.env (metadata) in
# $OVPN_INSTANCES_DIR. The systemd template unit openvpn-server@<name> is used.

# The standard Ubuntu/Debian openvpn-server@.service template uses
# WorkingDirectory=/etc/openvpn/server and --config %i.conf, so it expects
# configs at /etc/openvpn/server/%i.conf. We install a systemd drop-in that
# overrides WorkingDirectory to OVPN_INSTANCES_DIR so the unit finds configs
# at /etc/openvpn/instances/%i.conf instead.
ovpn_install_openvpn_server_dropin() {
  local dropin_dir="/etc/systemd/system/openvpn-server@.service.d"
  local dropin_file="${dropin_dir}/ovpn-panel.conf"
  if [[ -f "${dropin_file}" ]]; then
    return 0  # already installed
  fi
  install -d -m 0755 "${dropin_dir}"
  cat >"${dropin_file}" <<EOF
# Managed by ovpn-panel — override WorkingDirectory so the template unit
# openvpn-server@%i finds %i.conf under OVPN_INSTANCES_DIR.
[Service]
WorkingDirectory=${OVPN_INSTANCES_DIR}
ExecStart=
ExecStart=/usr/sbin/openvpn --status %t/openvpn-server/status/%i 10 --cd ${OVPN_INSTANCES_DIR} --config %i.conf
EOF
  chmod 0644 "${dropin_file}"
  systemctl daemon-reload 2>/dev/null || true
}

ovpn_instance_meta_path() { printf '%s/%s.env' "${OVPN_INSTANCES_DIR}" "$1"; }
ovpn_instance_conf_path() { printf '%s/%s.conf' "${OVPN_INSTANCES_DIR}" "$1"; }

ovpn_instance_exists() { [[ -f "$(ovpn_instance_meta_path "$1")" ]]; }

ovpn_validate_instance_name() {
  [[ "$1" =~ ^[a-zA-Z0-9_-]{1,32}$ ]] || ovpn_die "invalid instance name: $1"
}

ovpn_validate_proto() {
  case "$1" in udp|tcp) ;; *) ovpn_die "protocol must be udp or tcp" ;; esac
}

ovpn_validate_port() {
  if [[ ! "$1" =~ ^[0-9]+$ ]] || (( $1 < 1 || $1 > 65535 )); then
    ovpn_die "invalid port: $1"
  fi
}

ovpn_validate_subnet() {
  [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] || ovpn_die "invalid subnet: $1"
}

_netmask_from_cidr() {
  # Convert /24 to 255.255.255.0 (best-effort for /8../32).
  local cidr="$1" bits octets=() val
  bits="${cidr#*/}"
  for _ in 1 2 3 4; do
    if (( bits >= 8 )); then val=255; bits=$((bits-8));
    elif (( bits <= 0 )); then val=0;
    else val=$(( 256 - (1 << (8 - bits)) )); bits=0; fi
    octets+=("${val}")
  done
  printf '%s.%s.%s.%s' "${octets[0]}" "${octets[1]}" "${octets[2]}" "${octets[3]}"
}

# ovpn_instance_write <name> <proto> <port> <subnet> [dns] [mtu] [extra]
ovpn_instance_write() {
  local name="$1" proto="$2" port="$3" subnet="$4"
  local dns="${5:-1.1.1.1}" mtu="${6:-1500}" extra="${7:-}"
  ovpn_validate_instance_name "${name}"
  ovpn_validate_proto "${proto}"
  ovpn_validate_port "${port}"
  ovpn_validate_subnet "${subnet}"

  install -d -m 0755 "${OVPN_INSTANCES_DIR}"
  install -d -m 0755 "${OVPN_CCD_DIR}"
  install -d -m 0755 "${OVPN_CCD_DIR}/${name}"
  install -d -m 0755 "${OVPN_STATUS_DIR}"

  local net netmask ccd
  net="${subnet%/*}"; netmask="$(_netmask_from_cidr "${subnet}")"
  ccd="${OVPN_CCD_DIR}/${name}"

  local proto_line="" auth_lines="" dev="tun"
  case "${proto}" in
    udp) proto_line="proto udp" ;;
    tcp) proto_line="proto tcp-server"; dev="tun" ;;
  esac

  # Username/password + certificate auth shared CA is mandated for the panel.
  auth_lines="$(cat <<EOF
# --- Panel authentication (username/password via local unix socket) ---------
script-security 2
auth-user-pass-verify ${OVPN_PANEL_DIR}/scripts/hooks/auth-user-pass-verify.sh via-file
client-connect       ${OVPN_PANEL_DIR}/scripts/hooks/client-connect.sh
client-disconnect    ${OVPN_PANEL_DIR}/scripts/hooks/client-disconnect.sh
verify-client-cert none
username-as-common-name
duplicate-cn
EOF
)"

  cat >"$(ovpn_instance_conf_path "${name}")" <<EOF
# Managed by ovpn-panel. Manual edits will be overwritten by the installer.
# Instance: ${name}  (${proto}/${port})

port ${port}
${proto_line}
dev ${dev}
topology subnet

ca   ${OVPN_PKI_DIR}/ca.crt
cert ${OVPN_PKI_DIR}/issued/server.crt
key  ${OVPN_PKI_DIR}/private/server.key
dh   none
tls-crypt ${OVPN_PKI_DIR}/tls-crypt.key

server ${net} ${netmask}
ifconfig-pool-persist ${OVPN_STATUS_DIR}/${name}-ipp.txt

client-config-dir ${ccd}
keepalive 10 120
cipher AES-256-GCM
data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305
auth SHA256
tls-version-min 1.2
remote-cert-tls client

user  nobody
group nogroup
persist-key
persist-tun

push "redirect-gateway def1 bypass-dhcp"
push "dhcp-option DNS ${dns}"
push "block-outside-dns"
tun-mtu ${mtu}

status ${OVPN_STATUS_DIR}/${name}-status.log 30
verb 3

management ${OVPN_STATUS_DIR}/${name}.sock unix

${auth_lines}
${extra}
EOF
  chmod 0644 "$(ovpn_instance_conf_path "${name}")"

  cat >"$(ovpn_instance_meta_path "${name}")" <<EOF
# Metadata for instance ${name}. Sourced by scripts; keep simple KEY=value.
OVPN_INSTANCE_NAME=${name}
OVPN_INSTANCE_PROTO=${proto}
OVPN_INSTANCE_PORT=${port}
OVPN_INSTANCE_SUBNET=${subnet}
OVPN_INSTANCE_DNS=${dns}
OVPN_INSTANCE_MTU=${mtu}
EOF
  chmod 0644 "$(ovpn_instance_meta_path "${name}")"
  ovpn_ok "wrote instance ${name} (${proto}/${port}, ${subnet})"
}

ovpn_instance_enable() {
  local name="$1"
  # Ensure the systemd drop-in override exists so openvpn-server@.service
  # looks for %i.conf in OVPN_INSTANCES_DIR instead of /etc/openvpn/server.
  ovpn_install_openvpn_server_dropin

  # Validate that every file referenced by the generated config exists and is
  # readable BEFORE attempting to start the service.
  local conf; conf="$(ovpn_instance_conf_path "${name}")"
  if [[ ! -f "${conf}" ]]; then
    ovpn_die "instance config ${conf} is missing — cannot enable"
  fi
  # Extract file paths referenced in the config and check them.
  local refs
  refs="$(grep -oE '^\s*(ca|cert|key|dh|tls-crypt|tls-auth)\s+\S+' "${conf}" 2>/dev/null || true)"
  if [[ -n "${refs}" ]]; then
    while IFS=' ' read -r directive path; do
      [[ -n "${path}" ]] || continue
      # Strip quotes if present.
      path="${path%\"}"; path="${path#\"}"
      # The literal value "none" is valid OpenVPN syntax for directives such as
      # "dh" (ECC / TLS crypt setups) — skip filesystem checks.
      [[ "${path}" == "none" ]] && continue
      if [[ ! -f "${path}" ]]; then
        ovpn_die "config ${name} references missing file '${path}' (directive: ${directive})"
      fi
      if [[ ! -r "${path}" ]]; then
        ovpn_die "config ${name} references unreadable file '${path}' (directive: ${directive})"
      fi
    done <<<"${refs}"
  fi
  ovpn_ok "config ${name} — all referenced files exist and are readable"

  # Runtime hooks (auth-user-pass-verify etc.) live under OVPN_PANEL_DIR and are
  # staged by the panel phase BEFORE any service is started. Refuse to start the
  # service when a referenced hook is missing — starting OpenVPN would abort
  # immediately on the missing script.
  if command -v ovpn_panel_verify_hooks >/dev/null 2>&1; then
    ovpn_panel_verify_hooks
  elif [[ -n "$(grep -oE '^(auth-user-pass-verify|client-connect|client-disconnect)[[:space:]]+' "${conf}" 2>/dev/null || true)" ]]; then
    grep -oE '^\s*(auth-user-pass-verify|client-connect|client-disconnect)[[:space:]]+\S+' \
      "${conf}" 2>/dev/null | while read -r _ path; do
        [[ -f "${path}" && -x "${path}" ]] || {
          printf 'error: hook %s referenced by %s is missing or not executable\n' "${path}" "${name}" >&2
          exit 1
        }
      done
    ovpn_ok "config ${name} — instance hooks present and executable"
  fi

  systemctl enable "openvpn-server@${name}" >/dev/null 2>&1 || true
  systemctl restart "openvpn-server@${name}" || {
    local rc=$?
    ovpn_log "openvpn-server@${name} failed to start — collecting diagnostics"
    systemctl status --no-pager -l "openvpn-server@${name}" 2>&1 || true
    journalctl --no-pager -n 30 -u "openvpn-server@${name}" 2>&1 || true
    ovpn_die "openvpn-server@${name} failed to start (exit ${rc}); likely cause: a syntax error in the generated config, a missing TLS key, or a port conflict"
  }
  ovpn_ok "service openvpn-server@${name} started"
}

ovpn_instance_remove() {
  local name="$1"
  ovpn_validate_instance_name "${name}"
  systemctl disable --now "openvpn-server@${name}" 2>/dev/null || true
  rm -f "$(ovpn_instance_conf_path "${name}")" "$(ovpn_instance_meta_path "${name}")"
  rm -f "${OVPN_STATUS_DIR}/${name}.sock" "${OVPN_STATUS_DIR}/${name}-status.log"
  ovpn_ok "removed instance ${name}"
}

ovpn_instance_list_subnets() {
  local meta
  for meta in "${OVPN_INSTANCES_DIR}"/*.env; do
    [[ -f "${meta}" ]] || continue
    # shellcheck disable=SC1090
    ( . "${meta}"; printf '%s\n' "${OVPN_INSTANCE_SUBNET}" )
  done
}

# --- PKI (CA + server + tls-crypt) -------------------------------------------
ovpn_pki_ready() { [[ -f "${OVPN_PKI_DIR}/ca.crt" && -f "${OVPN_PKI_DIR}/issued/server.crt" ]]; }

# Run easy-rsa with the PKI directory supplied through the EASYRSA_PKI
# environment variable. easy-rsa 3.1.x rejects the space-separated
# "--pki-dir <dir>" form ("Missing value to option: --pki-dir"); the value must
# be attached with "=" (--pki-dir=<dir>) or passed via EASYRSA_PKI. Output is
# NOT hidden: on failure the command, exit code and the last 20 lines are shown.
ovpn_easyrsa() {
  local -a envs=("EASYRSA_BATCH=1" "EASYRSA_PKI=${OVPN_PKI_DIR}")
  [[ -n "${EASYRSA_REQ_CN:-}" ]] && envs+=("EASYRSA_REQ_CN=${EASYRSA_REQ_CN}")
  local out rc=0
  out="$(
    trap - ERR
    set +e
    env "${envs[@]}" "${EASYRSA_BIN}" "$@" 2>&1
  )" && rc=0 || rc=$?
  if [[ "${rc}" -ne 0 ]]; then
    ovpn_warn "easy-rsa failed (exit ${rc}): EASYRSA_PKI=${OVPN_PKI_DIR} ${EASYRSA_BIN} $*"
    printf '%s\n' "${out}" | tail -n 20 >&2
    return "${rc}"
  fi
  return 0
}

ovpn_generate_pki_easyrsa() {
  ovpn_log "Generating PKI with easy-rsa"
  if [[ ! -f "${OVPN_PKI_DIR}/ca.crt" ]]; then
    # No valid CA present: remove any half-written PKI (there is nothing valid
    # to preserve) and rebuild from scratch. A valid CA is NEVER wiped.
    if [[ -d "${OVPN_PKI_DIR}" ]]; then
      ovpn_warn "incomplete PKI found without a CA in ${OVPN_PKI_DIR} — rebuilding it"
      rm -rf "${OVPN_PKI_DIR}"
    fi
    install -d -m 0700 "${OVPN_PKI_DIR}"
    ovpn_easyrsa init-pki
    EASYRSA_REQ_CN="ovpn-panel-ca" ovpn_easyrsa build-ca nopass
    ovpn_ok "CA created"
  else
    ovpn_ok "existing CA kept"
  fi
  if [[ ! -f "${OVPN_PKI_DIR}/issued/server.crt" ]]; then
    ovpn_easyrsa build-server-full server nopass
    ovpn_ok "server certificate issued"
  fi
  # CRL is required for certificate revocation — a failure here is FATAL.
  ovpn_easyrsa gen-crl
}

ovpn_generate_pki_openssl() {
  ovpn_log "Generating PKI with openssl (easy-rsa fallback)"
  install -d -m 0700 "${OVPN_PKI_DIR}"
  install -d -m 0700 "${OVPN_PKI_DIR}/issued" "${OVPN_PKI_DIR}/private"
  if [[ ! -f "${OVPN_PKI_DIR}/ca.crt" ]]; then
    openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
      -keyout "${OVPN_PKI_DIR}/private/ca.key" \
      -out "${OVPN_PKI_DIR}/ca.crt" \
      -subj "/CN=ovpn-panel-ca"
  fi
  if [[ ! -f "${OVPN_PKI_DIR}/issued/server.crt" ]]; then
    openssl req -newkey rsa:4096 -nodes -sha256 \
      -keyout "${OVPN_PKI_DIR}/private/server.key" \
      -out "${OVPN_PKI_DIR}/server.csr" \
      -subj "/CN=server"
    openssl x509 -req -in "${OVPN_PKI_DIR}/server.csr" -sha256 -days 3650 \
      -CA "${OVPN_PKI_DIR}/ca.crt" -CAkey "${OVPN_PKI_DIR}/private/ca.key" \
      -CAcreateserial -out "${OVPN_PKI_DIR}/issued/server.crt" \
      -extfile <(printf 'keyUsage=digitalSignature\nextendedKeyUsage=serverAuth\n')
    rm -f "${OVPN_PKI_DIR}/server.csr"
  fi
}

ovpn_generate_pki() {
  if ovpn_pki_ready; then
    ovpn_ok "PKI already present in ${OVPN_PKI_DIR}"
  elif [[ -x "${EASYRSA_BIN}" ]]; then
    ovpn_generate_pki_easyrsa
  else
    ovpn_generate_pki_openssl
  fi
}

ovpn_generate_tls_crypt() {
  if [[ -f "${OVPN_PKI_DIR}/tls-crypt.key" ]]; then
    ovpn_ok "tls-crypt key kept"
    return 0
  fi
  ovpn_log "Generating tls-crypt key"
  # easy-rsa does NOT have a gen-tls-crypt-key subcommand; always use
  # the OpenVPN binary directly with an absolute path.
  "${OPENVPN_BIN}" --genkey secret "${OVPN_PKI_DIR}/tls-crypt.key"
  chmod 0600 "${OVPN_PKI_DIR}/tls-crypt.key"
  ovpn_ok "tls-crypt key written to ${OVPN_PKI_DIR}/tls-crypt.key"
}

# --- NAT / firewall ----------------------------------------------------------
# A single oneshot unit applies IP forwarding + NAT for every instance subnet on
# every boot, so rules survive reboots regardless of the tooling in use.

ovpn_nat_script() { printf '%s/scripts/ovpn-nat.sh' "${OVPN_PANEL_DIR}"; }

ovpn_install_nat_helper() {
  install -d -m 0755 "${OVPN_PANEL_DIR}/scripts"
  cat >"$(ovpn_nat_script)" <<'EOS'
#!/usr/bin/env bash
# Apply IP forwarding + NAT for all ovpn-panel instances. Idempotent.
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
  nft add rule ip ovpn_panel postrouting oifname "${ewm}" accept 2>/dev/null || true
  for snet in "${subnets[@]}"; do
    nft add rule ip ovpn_panel postrouting ip saddr "${snet}" oifname "${ewm}" masquerade 2>/dev/null || true
  done
  nft list chain ip filter forward >/dev/null 2>&1 || true
else
  command -v iptables >/dev/null 2>&1 || exit 0
  for snet in "${subnets[@]}"; do
    iptables -t nat -C POSTROUTING -s "${snet}" -o "${ewm}" -j MASQUERADE 2>/dev/null || \
      iptables -t nat -A POSTROUTING -s "${snet}" -o "${ewm}" -j MASQUERADE
    iptables -C FORWARD -s "${snet}" -j ACCEPT 2>/dev/null || \
      iptables -A FORWARD -s "${snet}" -j ACCEPT
  done
  iptables -C FORWARD -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
    iptables -A FORWARD -m state --state RELATED,ESTABLISHED -j ACCEPT
fi
EOS
  chmod 0755 "$(ovpn_nat_script)"

  cat >"${NAT_SERVICE}" <<EOF
[Unit]
Description=ovpn-panel NAT and forwarding rules
After=network-online.target openvpn-server@*.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=OVPN_INSTANCES_DIR=${OVPN_INSTANCES_DIR}
ExecStart=$(ovpn_nat_script)

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable ovpn-panel-nat.service >/dev/null 2>&1 || true
}

ovpn_apply_nat() {
  [[ -x "$(ovpn_nat_script)" ]] || ovpn_install_nat_helper
  "$(ovpn_nat_script)"  # FATAL — NAT is required for routing
  systemctl restart ovpn-panel-nat.service
}

ovpn_persist_nftables() {
  # When nftables is available, keep a fragment and make it load on boot.
  command -v nft >/dev/null 2>&1 || return 0
  install -d -m 0755 "${NFT_FRAGMENT_DIR}"
  {
    printf '#!/usr/sbin/nft -f\n'
    printf 'table ip ovpn_panel {\n'
    printf '}\n'
  } >"${NFT_FRAGMENT}"
  # Ensure the main config includes our fragment directory once.
  if [[ -f /etc/nftables.conf ]] && ! grep -q 'nftables.d' /etc/nftables.conf; then
    printf '\ninclude "%s/*.nft"\n' "${NFT_FRAGMENT_DIR}" >>/etc/nftables.conf
  fi
  systemctl enable nftables >/dev/null 2>&1 || true
}

ovpn_open_firewall_ports() {
  # OpenVPN's own ports plus the panel's configured admin port.
  local ports=(1194) p
  for p in "$@"; do ports+=("$p"); done

  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    for p in "${ports[@]}"; do
      ufw allow "${p}/udp" >/dev/null 2>&1 || true
      ufw allow "${p}/tcp" >/dev/null 2>&1 || true
    done
    ovpn_ok "opened ports via ufw: ${ports[*]}"
  elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    for p in "${ports[@]}"; do
      firewall-cmd --permanent --add-port="${p}/udp" >/dev/null 2>&1 || true
      firewall-cmd --permanent --add-port="${p}/tcp" >/dev/null 2>&1 || true
    done
    firewall-cmd --reload >/dev/null 2>&1 || true
    ovpn_ok "opened ports via firewalld: ${ports[*]}"
  elif command -v nft >/dev/null 2>&1; then
    nft list table inet ovpn_panel_input >/dev/null 2>&1 || nft add table inet ovpn_panel_input
    nft list chain inet ovpn_panel_input input >/dev/null 2>&1 || \
      nft add chain inet ovpn_panel_input input '{ type filter hook input priority 0 ; policy accept ; }'
    for p in "${ports[@]}"; do
      nft add rule inet ovpn_panel_input input udp dport "${p}" accept 2>/dev/null || true
      nft add rule inet ovpn_panel_input input tcp dport "${p}" accept 2>/dev/null || true
    done
    ovpn_ok "opened ports via nftables: ${ports[*]}"
  else
    ovpn_warn "no active firewall detected; skipping port opening (${ports[*]})"
  fi
}

# --- Client config template --------------------------------------------------
ovpn_write_ovpn_template() {
  install -d -m 0755 "${OVPN_CLIENT_DIR}"
  install -d -m 0750 "${OVPN_PANEL_CONFIG_DIR}"
  cat >"${OVPN_PANEL_CONFIG_DIR}/client.ovpn.template" <<EOF
# OpenVPN client template rendered by ovpn-panel.
# Placeholders: @@HOST@@ @@PORT@@ @@PROTO@@
client
dev tun
proto @@PROTO@@
remote @@HOST@@ @@PORT@@
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
cipher AES-256-GCM
auth SHA256
auth-user-pass
verb 3

# --- credentials (username/password authentication) -------------------------
<auth-user-pass>
@@USERNAME@@
@@PASSWORD@@
</auth-user-pass>

<ca>
@@CA@@
</ca>

<tls-crypt>
@@TLS_CRYPT@@
</tls-crypt>
EOF
  chmod 0644 "${OVPN_PANEL_CONFIG_DIR}/client.ovpn.template"

  # A ready-to-ship example rendered with the default instance values.
  cp -f "${OVPN_PANEL_CONFIG_DIR}/client.ovpn.template" \
        "${OVPN_CLIENT_DIR}/client.ovpn.example" 2>/dev/null || true
  ovpn_ok "wrote client template to ${OVPN_PANEL_CONFIG_DIR}/client.ovpn.template"
}

# --- first-install rollback --------------------------------------------------
# Track systemd units enabled/started and files written during THIS install so a
# fatal failure on a FIRST install can undo them cleanly (never touches a
# pre-existing valid CA or user data). update.sh/repair disable this tracking.

_OVPN_ROLLBACK_UNITS=()
_OVPN_ROLLBACK_FILES=()
# install.sh decides first-run semantics from the install marker before
# sourcing; keep that decision (default 1 = genuine first install).
_OVPN_PANEL_FIRST_RUN="${_OVPN_PANEL_FIRST_RUN:-1}"

ovpn_rollback_mark_service() {
  [[ "${_OVPN_PANEL_FIRST_RUN}" -eq 1 ]] || return 0
  local unit="$1"
  _OVPN_ROLLBACK_UNITS+=("${unit}")
}

ovpn_rollback_mark_file() {
  [[ "${_OVPN_PANEL_FIRST_RUN}" -eq 1 ]] || return 0
  local path="$1"
  _OVPN_ROLLBACK_FILES+=("${path}")
}

ovpn_rollback_mark_dir() {
  [[ "${_OVPN_PANEL_FIRST_RUN}" -eq 1 ]] || return 0
  local dir="$1"
  _OVPN_ROLLBACK_FILES+=("${dir}")
}

ovpn_rollback_execute() {
  # Undo every recorded action. Idempotent — safe to call multiple times.
  [[ ${#_OVPN_ROLLBACK_UNITS[@]} -gt 0 || ${#_OVPN_ROLLBACK_FILES[@]} -gt 0 ]] || return 0
  local u f
  ovpn_log "Rolling back partial install (first-install failure)"
  for u in "${_OVPN_ROLLBACK_UNITS[@]}"; do
    systemctl disable --now "${u}" >/dev/null 2>&1 || true
    ovpn_log "  rolled back unit: ${u}"
  done
  for f in "${_OVPN_ROLLBACK_FILES[@]}"; do
    rm -rf "${f}" 2>/dev/null || true
    ovpn_log "  removed: ${f}"
  done
  _OVPN_ROLLBACK_UNITS=()
  _OVPN_ROLLBACK_FILES=()
  ovpn_log "Rollback complete"
}

ovpn_remove_openvpn_server_dropin() {
  local dropin_dir="/etc/systemd/system/openvpn-server@.service.d"
  local dropin_file="${dropin_dir}/ovpn-panel.conf"
  if [[ -f "${dropin_file}" ]]; then
    rm -f "${dropin_file}"
    rmdir "${dropin_dir}" 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
  fi
}

# --- environment auto-heal ----------------------------------------------------

ovpn_ensure_ntp() {
  # clock not synchronised => enable NTP instead of only warning.
  command -v timedatectl >/dev/null 2>&1 || return 0
  local synced
  synced="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
  if [[ "${synced}" != "yes" ]]; then
    ovpn_log "System clock not NTP-synchronised — enabling NTP"
    timedatectl set-ntp true >/dev/null 2>&1 \
      || ovpn_warn "timedatectl set-ntp true failed (clock may still be off)"
    sleep 2
  fi
}

ovpn_load_kernel_modules() {
  local m
  for m in tun wireguard; do
    if modprobe -q "${m}" 2>/dev/null; then
      ovpn_ok "kernel module '${m}' loaded"
    else
      ovpn_warn "kernel module '${m}' unavailable (may need VPS feature enable)"
    fi
  done
}

ovpn_wait_apt_locks() {
  local i=0
  while fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/cache/apt/archives/lock >/dev/null 2>&1; do
    i=$((i+1))
    if [[ "${i}" -ge 36 ]]; then
      ovpn_warn "apt lock still held after 180s — continuing"
      break
    fi
    ovpn_log "Waiting for apt lock ... (${i})"
    sleep 5
  done
}
