#!/bin/sh
set -e

cd /app

# Started as root: make the uploads volume writable for "app", then re-run this
# script as that user. Files created by older root containers are taken over once.
if [ "$(id -u)" = "0" ]; then
  UPLOAD_DIR="${LOCAL_UPLOAD_DIR:-/app/uploads}"
  mkdir -p "$UPLOAD_DIR"
  if [ "$(stat -c %u "$UPLOAD_DIR")" != "10001" ]; then
    echo "Giving $UPLOAD_DIR to the app user..."
    chown -R app:app "$UPLOAD_DIR"
  fi
  exec gosu app "$0" "$@"
fi

echo "Preparing database..."
python -c "import asyncio; from app.db.schema import prepare_database; asyncio.run(prepare_database())"

# Demo accounts have public passwords (README): only on explicit opt-in for local work.
if [ "${SEED_DEMO_DATA}" = "true" ]; then
  echo "Seeding demo accounts (SEED_DEMO_DATA=true)..."
  python scripts/seed.py
fi

echo "Starting API..."
exec "$@"
