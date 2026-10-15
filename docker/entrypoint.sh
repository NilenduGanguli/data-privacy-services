#!/bin/sh
# App containers run pending migrations first when DPS_MIGRATIONS_MODE=auto (single-container runs);
# workers and the compose stack rely on the `migrate` one-shot instead (contract §11.1): compose sets
# DPS_MIGRATIONS_MODE=verify for the app and the worker, so this never runs there.
set -e
# Files the process creates are group-writable (M5 Task 24): the artefact and export volumes are
# shared by every UID of group 0 that runs this image (compose's 10001, OpenShift's namespace UID,
# the next UID after a redeploy). Keep this ENTRYPOINT on OpenShift (set `args`, not `command`).
umask 0002
if [ "${DPS_MIGRATIONS_MODE:-auto}" = "auto" ] && [ -n "${DPS_PG_HOST:-}" ] && [ "$1" = "uvicorn" ]; then
  python -m dps.migrate
fi
exec "$@"
