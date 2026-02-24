#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# load .env (same style as existing KIS bot)
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$ROOT/.env"
  set +a
fi

# Align with KIS bot conventions
export KIS_CONFIG_PATH="${KIS_CONFIG_PATH:-config.kr.json}"
export KIS_LOCK_FILE="${KIS_LOCK_FILE:-.kis_pairbot.lock}"

mkdir -p "$ROOT/logs"

PID_FILE="$ROOT/.adaptive_vb_pid"
LOG_FILE="$ROOT/logs/nohup_adaptive_vb.log"

# If already running, exit quietly
if [[ -f "$PID_FILE" ]]; then
  PID=$(cat "$PID_FILE" 2>/dev/null || true)
  if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "already running pid=$PID" >&2
    exit 0
  fi
fi

# Ensure venv exists
if [[ ! -x "$ROOT/venv/bin/python" ]]; then
  echo "venv missing -> bootstrapping" >&2
  bash "$ROOT/scripts/bootstrap.sh"
fi

nohup "$ROOT/venv/bin/python" "$ROOT/main.py" >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "started pid=$(cat $PID_FILE) log=$LOG_FILE" >&2
