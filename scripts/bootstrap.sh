#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -d venv ]]; then
  python3 -m venv venv
fi

"$ROOT/venv/bin/python" -m pip install -U pip >/dev/null
"$ROOT/venv/bin/python" -m pip install -r requirements.txt

echo "OK: venv ready" >&2
