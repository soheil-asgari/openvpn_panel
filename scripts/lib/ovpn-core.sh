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
set -euo pipefail

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

ovpn_require_root() {
  [[ "${EUID}" -eq 0 ]] || ovpn_die "must run as root (try: sudo $0)"
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
  [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 65535 )) || ovpn_die "invalid port: $1"
}

ovpn_validate_subnet() {
  [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] || ovpn_die "invalid subnet: $1"
}

_netmask_from_cidr() {
  # Convert /24 to 255.255.255.0 (best-effort for /8../32).
  local cidr="$1" bits octets=() i val
  bits="${cidr#*/}"
  for i in 1 2 3 4; do
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
  install -d -m 0755 "${OVPN_STATUS_DIR}"

  local net base netmask ccd
  net="${subnet%/*}"; netmask="$(_netmask_from_cidr "${subnet}")"
  ccd="${OVPN_CCD_DIR}/${name}"

  local proto_line="" tls_line="" auth_lines="" dev="tun"
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
  systemctl enable "openvpn-server@${name}" >/dev/null 2>&1 || true
  systemctl restart "openvpn-server@${name}"
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

ovpn_generate_pki_easyrsa() {
  ovpn_log "Generating PKI with easy-rsa"
  install -d -m 0700 "${OVPN_PKI_DIR}"
  if [[ ! -f "${OVPN_PKI_DIR}/ca.crt" ]]; then
    "${EASYRSA_BIN}" --batch --pki-dir "${OVPN_PKI_DIR}" init-pki >/dev/null
    EASYRSA_REQ_CN="ovpn-panel-ca" \
      "${EASYRSA_BIN}" --batch --pki-dir "${OVPN_PKI_DIR}" \
      build-ca nopass >/dev/null
  else
    ovpn_ok "existing CA kept"
  fi
  if [[ ! -f "${OVPN_PKI_DIR}/issued/server.crt" ]]; then
    "${EASYRSA_BIN}" --batch --pki-dir "${OVPN_PKI_DIR}" \
      build-server-full server nopass >/dev/null
  fi
  "${EASYRSA_BIN}" --batch --pki-dir "${OVPN_PKI_DIR}" gen-crl >/dev/null 2>&1 || true
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
  if [[ -x "${EASYRSA_BIN}" ]] && \
     "${EASYRSA_BIN}" --batch --pki-dir "${OVPN_PKI_DIR}" gen-tls-crypt-key >/dev/null 2>&1; then
    : # easy-rsa >= 3.1 writes the key itself
  else
    openvpn --genkey secret "${OVPN_PKI_DIR}/tls-crypt.key"
  fi
  chmod 0600 "${OVPN_PKI_DIR}/tls-crypt.key"
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
  "$(ovpn_nat_script)" || ovpn_warn "failed to apply NAT rules"
  systemctl restart ovpn-panel-nat.service 2>/dev/null || true
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
  local wan proto ports=(1194) p
  for p in "$@"; do ports+=("$p"); done
  wan="$(ovpn_wan_iface)"

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
  local host="${1:-VPN_HOST}" port="${2:-1194}" proto="${3:-udp}"
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
