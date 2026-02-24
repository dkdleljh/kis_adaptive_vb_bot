#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PID_FILE="$ROOT/.adaptive_vb_pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "not running (no pid file)" >&2
  exit 0
fi

PID=$(cat "$PID_FILE" 2>/dev/null || true)
if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
  echo "stopping pid=$PID" >&2
  kill -TERM "$PID" 2>/dev/null || true
  sleep 3
  kill -KILL "$PID" 2>/dev/null || true
fi

rm -f "$PID_FILE"
echo "stopped" >&2
