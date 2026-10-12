#!/usr/bin/env bash
# Homebrew PostgreSQL 16 scratch clusters for host-side tests (no Docker).
#   tools/scratch_pg.sh start [meta|source|all]   meta = ${DPS_DB_PORT:-5441} (dps roles + DB),
#                                                 source = ${DPS_SOURCE_PG_PORT:-5442} (bank)
#   tools/scratch_pg.sh stop  [meta|source|all]
#   tools/scratch_pg.sh status
# Trust auth, superuser "postgres", timezone UTC, no unix sockets (the scratch path can be too long).
# Lane-safe (contract §0, amendment 2026-10-12): ports come from the same DPS_*_PORT variables as
# compose, and each cluster's data directory is keyed by its port, so two lanes never share one.
# The tests find the clusters through DPS_TEST_PG_DSN / DPS_TEST_SOURCE_PG_DSN or those variables.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="${DPS_SCRATCH_DIR:-$HOME/.cache/dps-scratch-pg}"
BIN="$(brew --prefix postgresql@16 2>/dev/null || true)/bin"
[ -x "$BIN/pg_ctl" ] || BIN="$(dirname "$(command -v pg_ctl)")"

port_of() {
  case "$1" in
    meta) echo "${DPS_DB_PORT:-5441}" ;;
    source) echo "${DPS_SOURCE_PG_PORT:-5442}" ;;
    *) echo "unknown cluster: $1" >&2; exit 2 ;;
  esac
}

data_of() { echo "$BASE/$1-$(port_of "$1")"; }

start_one() {
  local name="$1" port data fresh
  port="$(port_of "$name")"
  data="$(data_of "$name")"
  if [ ! -f "$data/PG_VERSION" ]; then
    mkdir -p "$data"
    "$BIN/initdb" -D "$data" -U postgres --auth=trust -E UTF8 --locale=C >/dev/null
    echo "timezone = 'UTC'" >> "$data/postgresql.conf"
    fresh=1
  else
    fresh=0
  fi
  if ! "$BIN/pg_ctl" -D "$data" status >/dev/null 2>&1; then
    "$BIN/pg_ctl" -D "$data" -l "$data/server.log" -w start \
      -o "-p $port -c listen_addresses=127.0.0.1 -c unix_socket_directories='' -c max_connections=300"
  fi
  if [ "$fresh" = 1 ]; then
    case "$name" in
      meta)
        "$BIN/psql" -h 127.0.0.1 -p "$port" -U postgres -d postgres -v ON_ERROR_STOP=1 -q \
          -f "$ROOT/docker/dps-db/initdb/01_roles.sql"
        ;;
      source)
        for f in "$ROOT"/docker/source-pg/initdb/*.sql; do
          [ -f "$f" ] || continue
          "$BIN/psql" -h 127.0.0.1 -p "$port" -U postgres -d postgres -v ON_ERROR_STOP=1 -q -f "$f"
        done
        ;;
    esac
  fi
  echo "$name: 127.0.0.1:$port ($data)"
}

stop_one() {
  local data
  data="$(data_of "$1")"
  if [ -f "$data/PG_VERSION" ]; then
    "$BIN/pg_ctl" -D "$data" -m fast stop >/dev/null 2>&1 || true
  fi
  echo "$1: stopped"
}

cmd="${1:-status}"
which="${2:-all}"
if [ "$which" = all ]; then targets=(meta source); else targets=("$which"); fi
case "$cmd" in
  start) for t in "${targets[@]}"; do start_one "$t"; done ;;
  stop) for t in "${targets[@]}"; do stop_one "$t"; done ;;
  status)
    for t in meta source; do
      if "$BIN/pg_ctl" -D "$(data_of "$t")" status >/dev/null 2>&1; then
        echo "$t: running on 127.0.0.1:$(port_of "$t")"
      else
        echo "$t: stopped"
      fi
    done
    ;;
  *) echo "usage: $0 start|stop|status [meta|source|all]" >&2; exit 2 ;;
esac
