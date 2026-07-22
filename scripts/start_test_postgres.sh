#!/usr/bin/env bash
# Start the dedicated BidPilot test PostgreSQL and print the env export.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="$ROOT/infra/docker-compose.test.yml"

echo "Starting test PostgreSQL via $COMPOSE_FILE ..."
docker compose -f "$COMPOSE_FILE" up -d

echo "Waiting for health..."
for i in $(seq 1 40); do
  if docker exec bidpilot-postgres-test pg_isready -U bidpilot -d bidpilot_test >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

export TEST_DATABASE_URL='postgresql+psycopg://bidpilot:bidpilot_test@127.0.0.1:5433/bidpilot_test'
echo "export TEST_DATABASE_URL='$TEST_DATABASE_URL'"
echo "Ready. Example:"
echo "  cd backend && alembic upgrade head && pytest -q"
