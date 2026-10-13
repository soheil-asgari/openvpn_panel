#!/usr/bin/env bash
#
# Integration test for the easy-rsa PKI sequence used by the installer.
#
# Reproduces the easy-rsa 3.1.x failure where the space-separated form
# "--pki-dir <dir>" is rejected ("Missing value to option: --pki-dir"). The
# fix passes the PKI directory via EASYRSA_PKI (see ovpn_easyrsa in
# scripts/lib/ovpn-core.sh). The full sequence is run twice to prove it is
# idempotent. Skipped (not failed) when easy-rsa is not installed.
#
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Allow EASYRSA_BIN to point at an out-of-tree copy; default to the packaged one.
EASYRSA_BIN="${EASYRSA_BIN:-/usr/share/easy-rsa/easyrsa}"
if [[ ! -x "${EASYRSA_BIN}" ]] && ! command -v "${EASYRSA_BIN}" >/dev/null 2>&1; then
  printf 'SKIP: easy-rsa not installed (%s missing)\n' "${EASYRSA_BIN}"
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

export EASYRSA_BIN
export OVPN_PKI_DIR="${TMP}/pki"

# shellcheck source=../../scripts/lib/ovpn-core.sh
. "${ROOT}/scripts/lib/ovpn-core.sh"

run_sequence() {
  local label="$1"
  ovpn_log "PKI sequence: ${label}"
  ovpn_easyrsa init-pki
  EASYRSA_REQ_CN="ovpn-panel-ca" ovpn_easyrsa build-ca nopass
  ovpn_easyrsa gen-req server nopass
  ovpn_easyrsa sign-req server server
  ovpn_easyrsa gen-crl
}

# First run creates the PKI from scratch.
run_sequence "run 1 (fresh)"
[[ -f "${OVPN_PKI_DIR}/ca.crt" ]] || { printf 'FAIL: ca.crt missing\n' >&2; exit 1; }
[[ -f "${OVPN_PKI_DIR}/issued/server.crt" ]] || { printf 'FAIL: server.crt missing\n' >&2; exit 1; }
[[ -f "${OVPN_PKI_DIR}/crl.pem" ]] || { printf 'FAIL: crl.pem missing\n' >&2; exit 1; }

# Second run must remain green (idempotent) on the existing directory.
run_sequence "run 2 (idempotent)"

printf 'easyrsa PKI sequence test: OK\n'
