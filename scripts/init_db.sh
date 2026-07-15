#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}/backend"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://bidpilot:change_me_postgres@localhost:5432/bidpilot}"
alembic upgrade head
echo "Database migrated to head using DATABASE_URL"
