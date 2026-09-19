#!/bin/sh
set -e

cd /app

echo "Preparing database..."
python -c "import asyncio; from app.db.schema import prepare_database; asyncio.run(prepare_database())"

if [ "${APP_ENV}" = "development" ]; then
  echo "Seeding development accounts..."
  python scripts/seed.py
fi

echo "Starting API..."
exec "$@"
