#!/usr/bin/env bash
#
# OpenVPN auth-user-pass-verify hook.
#
# OpenVPN writes "username\npassword\n" to a temp file whose path is passed as
# the first argument (script_type=via-file). The credentials are forwarded to
# the panel's local unix socket as a single JSON line.
#
# Exit 0 to allow, non-zero to deny. Nothing is written to stdout/stderr so the
# credentials never end up in the OpenVPN log.
#
set -euo pipefail

AUTH_SOCKET="${OVPN_PANEL_AUTH_SOCKET:-/run/ovpn-panel/auth.sock}"
PYTHON_BIN="${OVPN_PANEL_PYTHON:-/opt/ovpn-panel/.venv/bin/python}"

CRED_FILE="${1:-}"
[[ -n "${CRED_FILE}" && -f "${CRED_FILE}" ]] || exit 1

USERNAME="$(sed -n '1p' "${CRED_FILE}")"
PASSWORD="$(sed -n '2p' "${CRED_FILE}")"
[[ -n "${USERNAME}" ]] || exit 1

# Prefer the panel's python (json + socket from the stdlib); fall back to a
# shell nc/socat implementation when it is unavailable.
if [[ -x "${PYTHON_BIN}" ]]; then
  exec "${PYTHON_BIN}" - "${AUTH_SOCKET}" "${USERNAME}" "${PASSWORD}" <<'PY'
import json
import socket
import sys

socket_path, username, password = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(10)
        sock.connect(socket_path)
        sock.sendall(json.dumps({"username": username, "password": password}).encode() + b"\n")
        data = sock.recv(65536).decode("utf-8", "replace").strip()
    ok = bool(json.loads(data).get("ok")) if data else False
except Exception:
    ok = False
sys.exit(0 if ok else 1)
PY
fi

# Fallback: expect nc with unix socket support (OpenBSD netcat).
command -v nc >/dev/null 2>&1 || exit 1
json_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
REQ="$(printf '{"username":"%s","password":"%s"}' \
  "$(json_escape "${USERNAME}")" "$(json_escape "${PASSWORD}")")"
RESP="$(printf '%s\n' "${REQ}" | nc -U -w 5 "${AUTH_SOCKET}" 2>/dev/null || true)"
case "${RESP}" in
  *'"ok": true'*|*'"ok":true'*) exit 0 ;;
  *) exit 1 ;;
esac
