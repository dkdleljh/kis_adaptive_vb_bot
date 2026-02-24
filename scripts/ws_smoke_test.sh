#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# load env
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$ROOT/.env"
  set +a
fi

if [[ -z "${KIS_WS_URL:-}" ]]; then
  echo "ERROR: KIS_WS_URL not set in .env" >&2
  exit 2
fi

# ensure venv
if [[ ! -x "$ROOT/venv/bin/python" ]]; then
  bash "$ROOT/scripts/bootstrap.sh" >/dev/null 2>&1
fi

mkdir -p "$ROOT/logs"
RAW_LOG="$ROOT/logs/ws_raw_sample_$(date +%Y%m%d_%H%M%S).log"

# Run a short WS session and print parsed prices.
# Also tee raw messages to file for debugging.

timeout ${WS_SMOKE_TIMEOUT_SEC:-25} "$ROOT/venv/bin/python" - <<'PY' | tee "$RAW_LOG"
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path('.env'))

from kis_auth import KISAuth
from kis_ws_marketdata import KISWSMarketData, WSConfig
import logging

logging.basicConfig(level=logging.INFO)
log=logging.getLogger('ws_smoke')

base=os.getenv('KIS_BASE_URL','https://openapi.koreainvestment.com:9443')
app=os.getenv('KIS_APP_KEY','')
sec=os.getenv('KIS_APP_SECRET','')
ws_url=os.getenv('KIS_WS_URL','')

symbols=['122630','252670','233740','251340']

async def main():
    auth=KISAuth(base, app, sec, log)
    ap=auth.fetch_approval_key(force=True)
    log.info('approval_key fetched (len=%s)', len(ap.approval_key))

    ws=KISWSMarketData(WSConfig(url=ws_url, approval_key=ap.approval_key), symbols, log)

    seen=set()
    async for sym, px in ws.stream_prices():
        print(f'PARSED price: {sym} {px}')
        seen.add(sym)
        if len(seen) >= 2:
            # enough to confirm parsing works; exit early
            return

asyncio.run(main())
PY

echo "OK: raw+parsed saved to $RAW_LOG" >&2
