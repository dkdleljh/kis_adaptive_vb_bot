from __future__ import annotations

"""kis_ws_marketdata.py (PairBot)

KIS WebSocket 시세 수신 + "가격 파싱" 구현.

목표: kis_orb_vwap_bot의 실전 파싱 로직을 최대한 그대로 가져와,
- JSON 메시지(PINGPONG/응답/일부 데이터)
- 텍스트 메시지(0|H0STCNT0|...|005930^090336^170800^...)
를 모두 처리합니다.

이 모듈은 PairBot에서 필요한 최소 출력만 제공합니다:
- (symbol, price)

NOTE
- 실시간 메시지 포맷은 시장/계정/TR에 따라 달라질 수 있어,
  파싱 실패 시 예외를 던져 상위에서 REST 폴링으로 폴백하도록 설계했습니다.
"""

import asyncio
import json
import time
from dataclasses import dataclass
from typing import AsyncIterator, Iterable, Optional, Tuple

import websockets


@dataclass
class WSConfig:
    url: str
    approval_key: str
    timezone: str = "Asia/Seoul"


class KISWSMarketData:
    def __init__(self, cfg: WSConfig, symbols: Iterable[str], logger) -> None:
        self.cfg = cfg
        self.symbols = list(symbols)
        self.logger = logger

    async def stream_prices(self) -> AsyncIterator[Tuple[str, float]]:
        async with websockets.connect(self.cfg.url, ping_interval=None) as ws:
            await self._subscribe(ws)
            self.logger.info("ws subscribed: %s", self.symbols)

            async for raw in ws:
                msg = self._parse_message(str(raw))
                mtype = msg.get("type") if isinstance(msg, dict) else None

                if mtype == "ping":
                    # 가장 안전: 받은 ping을 그대로 echo
                    await self._send_pong(ws, raw=str(raw))
                    continue

                if mtype == "trade":
                    sym = str(msg.get("symbol") or "")
                    px = msg.get("price")
                    if sym and px is not None:
                        yield sym, float(px)

    async def _subscribe(self, ws) -> None:
        for symbol in self.symbols:
            # 체결 구독 (H0STCNT0) - PairBot은 price만 필요
            trade_msg = {
                "header": {
                    "approval_key": self.cfg.approval_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8",
                },
                "body": {"input": {"tr_id": "H0STCNT0", "tr_key": symbol}},
            }
            await ws.send(json.dumps(trade_msg))
            await asyncio.sleep(0.05)

    def _get_message_type(self, data: dict) -> str:
        tr_id = (data.get("body") or {}).get("tr_id", "")
        if tr_id == "PINGPONG" or (data.get("header") or {}).get("tr_id") == "PINGPONG":
            return "ping"
        if tr_id == "H0STCNT0":
            return "trade"
        if tr_id == "H0STASP0":
            return "book"
        return "unknown"

    def _parse_message(self, raw: str) -> dict:
        """kis_orb_vwap_bot 스타일 파서(축약).

        - JSON이면: ping/응답/일부 output 포맷 처리
        - JSON이 아니면: "|" + "^" 기반 텍스트 포맷 처리
        """
        try:
            data = json.loads(raw)

            # ping
            if (data.get("header") or {}).get("tr_id") == "PINGPONG":
                return {"type": "ping"}
            if self._get_message_type(data) == "ping":
                return {"type": "ping"}

            # subscribe response message (msg1)
            if isinstance(data, dict) and "body" in data and isinstance(data["body"], dict) and "msg1" in data["body"]:
                return {"type": "response", "data": data}

            # some JSON data may include body.output
            if isinstance(data, dict) and "body" in data:
                output = (data.get("body") or {}).get("output") or {}
                if not output:
                    return {"type": "unknown_json", "raw": raw}

                # try the common keys
                symbol = output.get("mksc_shrn_iscd") or output.get("tr_key") or ""
                price = output.get("stck_prpr") or 0
                return {
                    "type": self._get_message_type(data),
                    "symbol": str(symbol),
                    "price": float(price),
                    "timestamp": output.get("symd") or "",
                }

            return {"type": "unknown_json", "raw": raw}

        except json.JSONDecodeError:
            return self._parse_text_message(raw)
        except Exception:
            return {"type": "unknown", "raw": raw}

    def _parse_text_message(self, raw: str) -> dict:
        parts = raw.split("|")
        if len(parts) < 4:
            return {"type": "unknown", "raw": raw}

        tr_id = parts[1]
        payload_raw = parts[3]
        payload = payload_raw.split("^")
        if not payload:
            return {"type": "unknown", "raw": raw}

        symbol = payload[0]

        if tr_id == "H0STCNT0":
            # [0] symbol, [1] HHMMSS, [2] price, [4] volume(추정)
            try:
                price = float(payload[2]) if len(payload) > 2 else 0.0
                vol = int(float(payload[4])) if len(payload) > 4 else 0
                ts = payload[1] if len(payload) > 1 else ""
                return {"type": "trade", "symbol": symbol, "price": price, "volume": vol, "timestamp": ts}
            except Exception:
                return {"type": "parse_error", "raw": raw}

        # PairBot은 orderbook은 사용 안 하지만, 파서 호환을 위해 남김
        if tr_id == "H0STASP0":
            try:
                ask1 = float(payload[3]) if len(payload) > 3 else 0.0
                bid1 = float(payload[13]) if len(payload) > 13 else 0.0
                ts = payload[1] if len(payload) > 1 else ""
                return {"type": "book", "symbol": symbol, "ask": ask1, "bid": bid1, "timestamp": ts}
            except Exception:
                return {"type": "parse_error", "raw": raw}

        return {"type": "unknown", "raw": raw}

    async def _send_pong(self, ws, raw: Optional[str] = None) -> None:
        try:
            if raw:
                await ws.send(raw)
            else:
                await ws.send("PINGPONG")
        except Exception:
            return
