#!/usr/bin/env bash
#
# Build the release artifacts for a given tag, reproducibly where practical.
#
# Produces (in dist/):
#   <slug>-<version>.tar.gz   the source tarball for that tag
#   SHA256SUMS                checksums of every artifact
#   install.sh                the one-line installer with the version injected
#
# Usage:
#   scripts/release/build-release.sh v1.2.3
#
# The caller is expected to be at the exact tagged commit (CI checks this out).
set -Eeuo pipefail

SLUG="${SLUG:-ovpn-panel}"
# Full GitHub "owner/repo" used to build the release download URL. In CI this is
# GITHUB_REPOSITORY; locally it can be exported or left as the placeholder.
REPO="${REPO:-${GITHUB_REPOSITORY:-<org>/<repo>}}"

usage() {
  printf 'usage: %s vX.Y.Z\n' "$0" >&2
  exit 2
}

TAG="${1:-}"
[[ -n "${TAG}" ]] || usage
VERSION="${TAG#v}"
[[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+].*)?$ ]] || {
  printf 'error: tag must look like vX.Y.Z (got %s)\n' "${TAG}" >&2
  usage
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

DIST="${ROOT}/dist"
mkdir -p "${DIST}"
rm -f "${DIST}/${SLUG}-${VERSION}.tar.gz" "${DIST}/SHA256SUMS" "${DIST}/install.sh"

echo "==> Ensuring prebuilt front-end assets are present"
# The panel must not require Node.js on user servers, so the vendored minified
# libraries are fetched here (with internet) and shipped inside the tarball.
if [[ ! -s "${ROOT}/app/static/js/htmx.min.js" || ! -s "${ROOT}/app/static/js/chart.min.js" ]]; then
  if command -v curl >/dev/null 2>&1; then
    bash "${ROOT}/scripts/fetch-assets.sh" || {
      echo "error: could not fetch front-end assets; aborting so the release is complete" >&2
      exit 1
    }
  else
    echo "error: curl is required to fetch front-end assets" >&2
    exit 1
  fi
fi

echo "==> Building source tarball ${SLUG}-${VERSION}.tar.gz"
# Deterministic-ish tarball: fixed owner/group, sorted names, no VCS metadata.
tar \
  --exclude-vcs \
  --exclude='./dist' \
  --exclude='./.venv' \
  --exclude='./node_modules' \
  --exclude='./.git' \
  --exclude='./__pycache__' \
  --exclude='*.pyc' \
  --exclude='./.pytest_cache' \
  --exclude='./.ruff_cache' \
  --owner=0 --group=0 --numeric-owner \
  --sort=name \
  --mtime='UTC 2020-01-01' \
  -czf "${DIST}/${SLUG}-${VERSION}.tar.gz" .

echo "==> Injecting version ${VERSION} into install.sh"
TEMPLATE="${ROOT}/scripts/release/install-template.sh"
[[ -f "${TEMPLATE}" ]] || { echo "error: ${TEMPLATE} missing" >&2; exit 1; }
sed -e "s|@VERSION@|${VERSION}|g" \
    -e "s|@TAG@|${TAG}|g" \
    -e "s|@SLUG@|${SLUG}|g" \
    -e "s|@REPO@|${REPO}|g" \
    "${TEMPLATE}" > "${DIST}/install.sh"
chmod 0755 "${DIST}/install.sh"
bash -n "${DIST}/install.sh"

echo "==> Writing SHA256SUMS"
(
  cd "${DIST}"
  sha256sum "${SLUG}-${VERSION}.tar.gz" install.sh > SHA256SUMS
)

echo "==> Done:"
ls -l "${DIST}"
cat "${DIST}/SHA256SUMS"
