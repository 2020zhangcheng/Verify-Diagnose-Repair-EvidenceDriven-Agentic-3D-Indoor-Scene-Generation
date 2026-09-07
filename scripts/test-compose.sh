#!/bin/sh
set -eu
# Dedicated test DB only; never drop the application DB or its event log.
docker compose exec -T db sh -c 'psql -U roomscout -tAc "SELECT 1 FROM pg_database WHERE datname = '\''roomscout_test'\''" | grep -q 1 || createdb -U roomscout roomscout_test'
docker compose run --rm --no-deps \
  -e DATABASE_URL=postgresql+psycopg://roomscout:roomscout@db:5432/roomscout_test \
  -e CHECKPOINT_URL='postgresql://roomscout:roomscout@db:5432/roomscout_test?options=-csearch_path%3Dcheckpoints' \
  -e TEST_DATABASE_URL=postgresql+psycopg://roomscout:roomscout@db:5432/roomscout_test \
  -e TEST_CHECKPOINT_URL='postgresql://roomscout:roomscout@db:5432/roomscout_test?options=-csearch_path%3Dcheckpoints' \
  -e ROOMSCOUT_INTEGRATION=1 \
  api sh -c 'alembic upgrade head && python -m app.db.bootstrap && python -m pytest -q'
