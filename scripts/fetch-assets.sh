#!/usr/bin/env bash
# Download the vendored front-end libraries into app/static/js.
set -euo pipefail

HTMX_VERSION="${HTMX_VERSION:-1.9.12}"
CHART_VERSION="${CHART_VERSION:-4.4.3}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/app/static/js"
mkdir -p "$DEST"

echo "Fetching htmx ${HTMX_VERSION} ..."
curl -fsSL "https://unpkg.com/htmx.org@${HTMX_VERSION}/dist/htmx.min.js" -o "$DEST/htmx.min.js"

echo "Fetching Chart.js ${CHART_VERSION} ..."
curl -fsSL "https://cdn.jsdelivr.net/npm/chart.js@${CHART_VERSION}/dist/chart.umd.min.js" -o "$DEST/chart.min.js"

echo "Done. Assets written to $DEST"
