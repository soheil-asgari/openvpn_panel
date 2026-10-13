#!/usr/bin/env bash
#
# Migrate an existing SQLite database to PostgreSQL.
# Run as root with the panel stopped:  sudo ./scripts/migrate-to-postgres.sh <postgres-url>
#
set -euo pipefail

CONFIG_DIR="/etc/ovpn-panel"
VENV_DIR="/opt/ovpn-panel/.venv"
SQLITE_DB="${OVPN_SQLITE_DB:-/var/lib/ovpn-panel/panel.sqlite3}"
STAMP="$(date +%Y%m%d%H%M%S)"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

PG_URL="${1:-}"
[[ -n "${PG_URL}" ]] || die "usage: $0 postgresql+psycopg://user:pass@host/dbname"
[[ "${EUID}" -eq 0 ]] || die "must run as root"


log "Stopping panel"
systemctl stop ovpn-panel.service || true

log "Backing up SQLite database"
cp -a "${SQLITE_DB}" "/var/backups/ovpn-panel/panel-sqlite-${STAMP}.sqlite3" || true

log "Writing target schema"
OVPN_PANEL_CONFIG_DIR="${CONFIG_DIR}" OVPN_DATABASE_URL="${PG_URL}" \
  "${VENV_DIR}/bin/python" -m scripts.cli init-db

log "Copying rows"
"${VENV_DIR}/bin/python" - "${SQLITE_DB}" "${PG_URL}" <<'PY'
import sys

from sqlalchemy import create_engine, insert, select, text

from app.config import get_settings
from app.models import Base

src_url, dst_url = sys.argv[1], sys.argv[2]
src = create_engine(f"sqlite:///{src_url}", future=True)
dst = create_engine(dst_url, future=True)

# Insert parents before children to satisfy foreign keys.
order = ["users", "nodes", "vpn_users", "sessions", "api_keys", "audit_logs", "usage_samples"]

with src.connect() as s, dst.begin() as d:
    for table_name in order:
        table = Base.metadata.tables[table_name]
        rows = [dict(r) for r in s.execute(select(table)).mappings()]
        if rows:
            d.execute(insert(table), rows)
        print(f"{table_name}: {len(rows)} rows")
PY

log "Updating configuration"
if ! grep -q '^OVPN_DATABASE_URL=' "${CONFIG_DIR}/panel.env"; then
  echo "OVPN_DATABASE_URL=${PG_URL}" >> "${CONFIG_DIR}/panel.env"
else
  sed -i "s|^OVPN_DATABASE_URL=.*|OVPN_DATABASE_URL=${PG_URL}|" "${CONFIG_DIR}/panel.env"
fi
chmod 600 "${CONFIG_DIR}/panel.env"

log "Starting panel"
systemctl start ovpn-panel.service
log "Migration complete."
