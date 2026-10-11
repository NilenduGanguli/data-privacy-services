#!/bin/sh
# M0: nothing to prepare (no database). Later milestones add `python -m dps.migrate` here under
# DPS_MIGRATIONS_MODE=auto, exactly like the sibling services.
set -e
exec "$@"
