#!/usr/bin/env bash
# shellcheck shell=bash
#
# Panel-specific helpers shared by install.sh and the ovpn-panel CLI.
# Sourced after scripts/lib/ovpn-core.sh.
#
# Guard against being executed directly (it is meant to be sourced after
# ovpn-core.sh). Must NOT `exit`, as that would kill the sourcing shell.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  printf 'error: %s is a library; source it from install.sh/update.sh\n' "$0" >&2
fi

set -euo pipefail

OVPN_PANEL_VENV="${OVPN_PANEL_VENV:-${OVPN_PANEL_DIR}/.venv}"
OVPN_PANEL_SERVICE="/etc/systemd/system/ovpn-panel.service"

# --- panel.env key/value helpers --------------------------------------------
ovpn_env_get() {
  local key="$1" file="${2:-${OVPN_PANEL_CONFIG_DIR}/panel.env}"
  [[ -f "${file}" ]] || return 1
  grep -E "^${key}=" "${file}" | tail -n1 | cut -d= -f2-
}

ovpn_env_set() {
  local key="$1" value="$2" file="${3:-${OVPN_PANEL_CONFIG_DIR}/panel.env}"
  install -d -m 0750 "${OVPN_PANEL_CONFIG_DIR}"
  touch "${file}"
  if grep -qE "^${key}=" "${file}"; then
    # Delimiters unlikely to collide with tokens/paths.
    sed -i "s|^${key}=.*|${key}=${value}|" "${file}"
  else
    printf '%s=%s\n' "${key}" "${value}" >>"${file}"
  fi
  chmod 0600 "${file}"
}

ovpn_panel_get_port() {
  local v
  v="$(ovpn_env_get OVPN_PANEL_PORT 2>/dev/null || true)"
  printf '%s' "${v:-8080}"
}

ovpn_panel_get_admin() {
  ovpn_env_get OVPN_ADMIN_USER 2>/dev/null || printf '%s' "admin"
}

ovpn_panel_instance_names() {
  local meta
  for meta in "${OVPN_INSTANCES_DIR}"/*.env; do
    [[ -f "${meta}" ]] || continue
    # shellcheck disable=SC1090
    ( . "${meta}"; printf '%s\n' "${OVPN_INSTANCE_NAME}" )
  done
}

# --- CLI menu ----------------------------------------------------------------
ovpn_panel_install_cli() {
  ovpn_log "Installing ovpn-panel CLI menu"
  install -m 0755 "${OVPN_PANEL_DIR}/scripts/ovpn-panel-menu" /usr/local/bin/ovpn-panel
  install -d -m 0755 /usr/local/lib/ovpn-panel
  install -m 0644 "${OVPN_PANEL_DIR}/scripts/lib/ovpn-core.sh" /usr/local/lib/ovpn-panel/
  install -m 0644 "${OVPN_PANEL_DIR}/scripts/lib/panel-core.sh" /usr/local/lib/ovpn-panel/
  # Point the installed CLI at the shared libs.
  sed -i "s|^LIB_DIR=.*|LIB_DIR=/usr/local/lib/ovpn-panel|" /usr/local/bin/ovpn-panel
}

# --- service control ---------------------------------------------------------
ovpn_panel_restart_services() {
  command -v systemctl >/dev/null 2>&1 || return 0
  systemctl daemon-reload
  systemctl restart ovpn-panel-helper.service 2>/dev/null || true
  systemctl restart ovpn-panel.service 2>/dev/null || true
}

# --- rendering the port into the systemd unit --------------------------------
ovpn_panel_render_unit_port() {
  local port="$1"
  [[ -f "${OVPN_PANEL_SERVICE}" ]] || return 0
  sed -i -E "s|(--port )[0-9]+|\1${port}|" "${OVPN_PANEL_SERVICE}"
}

# --- access info -------------------------------------------------------------
# Print every piece of access information. When <password> is supplied it is
# shown once here; callers that only want status may omit it.
ovpn_panel_print_access() {
  local password="${1:-}" port host admin
  port="$(ovpn_panel_get_port)"
  admin="$(ovpn_panel_get_admin)"
  host="$(ovpn_env_get OVPN_PANEL_HOST 2>/dev/null || true)"

  printf '\n'
  printf '================================================================\n'
  printf '  OpenVPN Web Panel installed\n'
  printf '================================================================\n'
  printf '  Admin user     : %s\n' "${admin}"
  if [[ -n "${password}" ]]; then
    printf '  Admin password : %s  (shown once - store it now)\n' "${password}"
  fi
  printf '  Listening on   : http://127.0.0.1:%s (bind to localhost)\n' "${port}"
  if [[ -n "${host}" ]]; then
    printf '  Public URL     : http://%s:%s/  (put a TLS reverse proxy in front)\n' "${host}" "${port}"
  else
    printf '  Public URL     : http://<server-ip>:%s/  (put a TLS reverse proxy in front)\n' "${port}"
  fi
  printf '  Manage         : sudo ovpn-panel\n'
  printf '  Config/env     : %s/panel.env\n' "${OVPN_PANEL_CONFIG_DIR}"
  printf '  OpenVPN PKI    : %s\n' "${OVPN_PKI_DIR}"
  printf '  Instances      : %s\n' "$(ovpn_panel_instance_names | paste -sd', ' - || true)"
  printf '================================================================\n\n'
}

# Persist the credentials to a root-only file (mode 600).
ovpn_panel_write_credentials() {
  local user="$1" password="$2" port="$3" host="${4:-}"
  local file="${OVPN_PANEL_CONFIG_DIR}/credentials.txt"
  install -d -m 0750 "${OVPN_PANEL_CONFIG_DIR}"
  {
    printf '# OpenVPN Web Panel access credentials - keep private\n'
    printf 'OVPN_PANEL_URL=http://%s:%s/\n' "${host:-<server-ip>}" "${port}"
    printf 'OVPN_ADMIN_USER=%s\n' "${user}"
    printf 'OVPN_ADMIN_PASSWORD=%s\n' "${password}"
    printf 'OVPN_PANEL_CONFIG_DIR=%s\n' "${OVPN_PANEL_CONFIG_DIR}"
    printf 'OVPN_PKI_DIR=%s\n' "${OVPN_PKI_DIR}"
    printf 'OVPN_INSTANCES=%s\n' "$(ovpn_panel_instance_names | paste -sd', ' - 2>/dev/null || true)"
  } >"${file}"
  chmod 0600 "${file}"
  ovpn_ok "credentials saved to ${file} (mode 600)"
}

# --- panel app install -------------------------------------------------------
ovpn_panel_install_app() {
  local src_dir="$1"
  ovpn_log "Creating system user and directories"
  getent group ovpn-panel >/dev/null || groupadd --system ovpn-panel
  id -u ovpn-panel >/dev/null 2>&1 || \
    useradd --system --gid ovpn-panel --home-dir "${OVPN_PANEL_DIR}" \
      --shell /usr/sbin/nologin ovpn-panel

  install -d -m 0755 "${OVPN_PANEL_DIR}"
  install -d -m 0750 -o root -g ovpn-panel "${OVPN_PANEL_CONFIG_DIR}"
  install -d -m 0750 -o ovpn-panel -g ovpn-panel /var/lib/ovpn-panel
  install -d -m 0755 -o root -g ovpn-panel /run/ovpn-panel

  ovpn_log "Copying application files to ${OVPN_PANEL_DIR}"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
      "${src_dir}/app" "${src_dir}/agent" "${src_dir}/helper" \
      "${src_dir}/scripts" "${src_dir}/pyproject.toml" "${src_dir}/README.md" \
      "${src_dir}/LICENSE" "${OVPN_PANEL_DIR}/"
  else
    for item in app agent helper scripts pyproject.toml README.md LICENSE; do
      cp -a "${src_dir}/${item}" "${OVPN_PANEL_DIR}/"
    done
  fi
  chmod 0755 "${OVPN_PANEL_DIR}/scripts/hooks/"*.sh
  chmod 0755 "${OVPN_PANEL_DIR}/scripts/ovpn-nat.sh" 2>/dev/null || true

  ovpn_log "Creating virtual environment"
  local py="${PYTHON_BIN:-python3}"
  "${py}" -m venv "${OVPN_PANEL_VENV}"
  "${OVPN_PANEL_VENV}/bin/pip" install --upgrade pip wheel >/dev/null
  "${OVPN_PANEL_VENV}/bin/pip" install "${OVPN_PANEL_DIR}"
}

ovpn_panel_generate_secrets() {
  ovpn_log "Generating panel secrets"
  if [[ ! -f "${OVPN_PANEL_CONFIG_DIR}/panel.env" ]]; then
    "${OVPN_PANEL_VENV}/bin/python" -m scripts.cli gen-secrets \
      --output "${OVPN_PANEL_CONFIG_DIR}/panel.env" \
      --database-url "sqlite:////var/lib/ovpn-panel/panel.sqlite3"
    chown root:ovpn-panel "${OVPN_PANEL_CONFIG_DIR}/panel.env"
    chmod 0640 "${OVPN_PANEL_CONFIG_DIR}/panel.env"
  else
    ovpn_ok "keeping existing panel.env"
  fi
  ovpn_env_set OVPN_AUTH_SOCKET /run/ovpn-panel/auth.sock

  # Keep the hook wrapper token in sync with the panel.
  local token
  token="$(ovpn_env_get OVPN_HOOK_TOKEN 2>/dev/null || true)"
  if [[ -n "${token}" ]]; then
    ovpn_env_set OVPN_HOOK_TOKEN "${token}"
  fi
}

ovpn_panel_install_units() {
  ovpn_log "Installing systemd units"
  install -m 0644 "${OVPN_PANEL_DIR}/scripts/systemd/ovpn-panel.service" /etc/systemd/system/
  install -m 0644 "${OVPN_PANEL_DIR}/scripts/systemd/ovpn-panel-helper.service" /etc/systemd/system/
  install -m 0644 "${OVPN_PANEL_DIR}/scripts/systemd/ovpn-panel-agent.service" /etc/systemd/system/
  install -m 0644 "${OVPN_PANEL_DIR}/scripts/systemd/ovpn-panel-agent@.service" /etc/systemd/system/
  ovpn_panel_render_unit_port "$(ovpn_panel_get_port)"
  systemctl daemon-reload
  # Required steps — failing to start the panel is FATAL.
  systemctl enable --now ovpn-panel-helper.service || {
    local rc=$?
    systemctl status --no-pager -l ovpn-panel-helper.service 2>&1 || true
    journalctl --no-pager -n 30 -u ovpn-panel-helper.service 2>&1 || true
    ovpn_die "ovpn-panel-helper.service failed to start (exit ${rc})"
  }
  systemctl enable --now ovpn-panel.service || {
    local rc=$?
    systemctl status --no-pager -l ovpn-panel.service 2>&1 || true
    journalctl --no-pager -n 30 -u ovpn-panel.service 2>&1 || true
    ovpn_die "ovpn-panel.service failed to start (exit ${rc})"
  }
}
