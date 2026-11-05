#!/bin/sh
set -e

cd /app

echo "Preparing database..."
python -c "import asyncio; from app.db.schema import prepare_database; asyncio.run(prepare_database())"

# Demo accounts have public passwords (README): only on explicit opt-in for local work.
if [ "${SEED_DEMO_DATA}" = "true" ]; then
  echo "Seeding demo accounts (SEED_DEMO_DATA=true)..."
  python scripts/seed.py
fi

echo "Starting API..."
exec "$@"
