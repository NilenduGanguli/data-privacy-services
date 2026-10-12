#!/bin/sh
# App containers run pending migrations first when DPS_MIGRATIONS_MODE=auto (single-container runs);
# workers and the compose stack rely on the `migrate` one-shot instead (contract §11.1): compose sets
# DPS_MIGRATIONS_MODE=verify for the app and the worker, so this never runs there.
set -e
if [ "${DPS_MIGRATIONS_MODE:-auto}" = "auto" ] && [ -n "${DPS_PG_HOST:-}" ] && [ "$1" = "uvicorn" ]; then
  python -m dps.migrate
fi
exec "$@"
