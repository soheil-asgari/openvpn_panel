#!/usr/bin/env bash
#
# Smoke test for admin-password handling in scripts/install.sh.
#
# Covers:
#   * ovpn_strong_password / ovpn_password_is_strong (>=16 chars, upper, lower,
#     digit, symbol)
#   * ovpn_resolve_admin_password: empty -> random, provided -> verbatim
#   * the non-interactive install.sh path: `--admin-password ""` and an omitted
#     password both yield a strong random password (via the DRY_RUN seam).
#
# Runs without root and without touching any real system path.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# shellcheck source=../../scripts/lib/ovpn-core.sh
. "${ROOT}/scripts/lib/ovpn-core.sh"

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

# --- helpers -----------------------------------------------------------------
# A generated password must be >=16 chars and cover all four classes.
assert_strong() {
  local pw="$1" label="$2"
  [[ ${#pw} -ge 16 ]] || fail "${label}: too short (${#pw})"
  [[ "${pw}" == *[A-Z]* ]] || fail "${label}: no uppercase"
  [[ "${pw}" == *[a-z]* ]] || fail "${label}: no lowercase"
  [[ "${pw}" == *[0-9]* ]] || fail "${label}: no digit"
  [[ "${pw}" == *[!A-Za-z0-9]* ]] || fail "${label}: no symbol"
}

# --- ovpn_strong_password ----------------------------------------------------
pw1="$(ovpn_strong_password 24)"
pw2="$(ovpn_strong_password 24)"
assert_strong "${pw1}" "ovpn_strong_password #1"
assert_strong "${pw2}" "ovpn_strong_password #2"
[[ "${pw1}" != "${pw2}" ]] || fail "two generated passwords were identical"
# A too-small request is clamped up to the 16-char floor.
assert_strong "$(ovpn_strong_password 4)" "ovpn_strong_password clamp"

# --- ovpn_password_is_strong -------------------------------------------------
ovpn_password_is_strong "${pw1}" || fail "generated password reported weak"
for weak in "" "short" "alllowercase1234567" "NOSYMBOLS12345678" "nosym1ABCDEFGHIJ"; do
  if ovpn_password_is_strong "${weak}"; then
    fail "weak password accepted: '${weak}'"
  fi
done

# --- ovpn_resolve_admin_password --------------------------------------------
assert_strong "$(ovpn_resolve_admin_password "")" "resolve empty -> random"
assert_eq() { [[ "$1" == "$2" ]] || fail "expected '$2', got '$1'"; }
# A user-chosen password is returned verbatim and never rejected.
assert_eq "$(ovpn_resolve_admin_password 'x')" "x"

# --- non-interactive install.sh path (DRY_RUN) -------------------------------
run_dry() {
  OVPN_INSTALL_DRY_RUN=1 bash "${ROOT}/scripts/install.sh" \
    --yes --no-openvpn --admin-user admin "$@"
}

# --admin-password "" must generate a strong random password.
out="$(run_dry --admin-password "")"
gen="$(printf '%s\n' "${out}" | sed -n 's/^DRY_RUN admin_password=//p')"
printf '%s\n' "${out}" | grep -q '^DRY_RUN password_generated=1$' \
  || fail "--admin-password '' did not flag generation"
assert_strong "${gen}" "--admin-password ''"

# An omitted password must behave identically.
out="$(run_dry)"
gen="$(printf '%s\n' "${out}" | sed -n 's/^DRY_RUN admin_password=//p')"
printf '%s\n' "${out}" | grep -q '^DRY_RUN password_generated=1$' \
  || fail "omitted --admin-password did not flag generation"
assert_strong "${gen}" "omitted --admin-password"

# A provided password is used verbatim and not flagged as generated.
out="$(run_dry --admin-password 'MyChosen-Pass1')"
gen="$(printf '%s\n' "${out}" | sed -n 's/^DRY_RUN admin_password=//p')"
assert_eq "${gen}" "MyChosen-Pass1"
printf '%s\n' "${out}" | grep -q '^DRY_RUN password_generated=0$' \
  || fail "provided password wrongly flagged as generated"

printf 'install.sh admin-password smoke test: OK\n'
