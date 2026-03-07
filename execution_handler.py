from __future__ import annotations

"""execution_handler.py

KIS WebSocket 실시간 체결가 수신, 매수/매도 API 호출 및 에러 핸들링.

요구사항:
- REST: requests
- 실시간 감시: websockets + asyncio

실전 운영 안전장치:
- KIS_LIVE_ENABLED=1, KIS_LIVE_CONFIRM=YES, KIS_KILL_SWITCH=0일 때만 주문 전송
- 프로젝트 루트에 STOP_TRADING.flag가 존재하면 주문 전송 금지

NOTE:
- KIS WebSocket 승인키/구독 포맷은 KIS 문서에 따라 맞춰야 합니다.
- 본 파일은 '실전 안전 운영' 관점의 예외처리/로깅/폴백 구조까지 포함합니다.
"""

import os
import time
import asyncio
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, AsyncIterator, Tuple

import requests

from kis_auth import KISAuth
from kis_ws_marketdata import KISWSMarketData, WSConfig


@dataclass
class OrderResponse:
    ok: bool
    msg: str
    order_no: Optional[str] = None


class ExecutionHandler:
    def __init__(self, logger: logging.Logger, *, project_root: Path) -> None:
        self.log = logger
        self.root = project_root
        self.base_url = os.getenv(
            "KIS_BASE_URL", "https://openapi.koreainvestment.com:9443"
        ).strip()
        self.app_key = os.getenv("KIS_APP_KEY", "").strip()
        self.app_secret = os.getenv("KIS_APP_SECRET", "").strip()
        # Match existing KIS bot naming first, fallback to CANO/ACNT_PRDT_CD.
        self.cano = (os.getenv("KIS_ACCOUNT_NO") or os.getenv("KIS_CANO") or "").strip()
        self.acnt_prdt_cd = (
            os.getenv("KIS_ACCOUNT_PRODUCT_CODE") or os.getenv("KIS_ACNT_PRDT_CD") or ""
        ).strip()
        # kis_orb_vwap_bot 스타일 안전 플래그
        self.live_enabled = os.getenv("KIS_LIVE_ENABLED", "0") == "1"
        self.live_confirm = os.getenv("KIS_LIVE_CONFIRM", "NO").strip().upper() == "YES"
        self.kill_switch = os.getenv("KIS_KILL_SWITCH", "1") != "0"

        # Auth helper (token/approval/hashkey)
        self.auth = KISAuth(self.base_url, self.app_key, self.app_secret, logger)

        # REST-only fallback polling interval
        self._rest_poll_interval_sec = float(
            os.getenv("PRICE_POLL_INTERVAL_SEC", "1.0")
        )

        # (2) price feed error notification (optional, model 0)
        self._alert_openclaw = os.getenv("PAIRBOT_ALERT_OPENCLAW", "0") == "1"
        self._alert_cooldown_sec = float(os.getenv("PAIRBOT_ALERT_COOLDOWN_SEC", "600"))
        self._last_alert_ts = 0.0

    def _notify_openclaw(self, text: str) -> None:
        if not self._alert_openclaw:
            return
        now = time.time()
        if now - self._last_alert_ts < self._alert_cooldown_sec:
            return
        self._last_alert_ts = now

        oc = (
            os.getenv("OPENCLAW_BIN")
            or "/home/zenith/.nvm/versions/node/v25.4.0/bin/openclaw"
        )
        try:
            subprocess.run(
                [oc, "system", "event", "--text", text, "--mode", "now"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    # ---------- safety ----------
    def trading_blocked(self) -> Tuple[bool, str]:
        # KIS 봇과 동일한 3중 게이트
        if not self.live_enabled:
            return True, "KIS_LIVE_ENABLED!=1"
        if not self.live_confirm:
            return True, "KIS_LIVE_CONFIRM!=YES"
        if self.kill_switch:
            return True, "KIS_KILL_SWITCH!=0"
        if (self.root / "STOP_TRADING.flag").exists():
            return True, "STOP_TRADING.flag present"
        return False, ""

    # Token/approval/hashkey는 kis_auth.KISAuth에서 관리합니다.

    # ---------- REST 주문 ----------
    def buy_market(self, symbol: str, qty: int) -> OrderResponse:
        blocked, reason = self.trading_blocked()
        if blocked:
            return OrderResponse(False, f"blocked: {reason}")
        if qty <= 0:
            return OrderResponse(False, "qty<=0")

        # NOTE: KIS 국내주식 현금매수 TR_ID(실전) 예시
        tr_id = "TTTC0802U"
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": symbol,
            "ORD_DVSN": "01",  # 시장가
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
        }

        try:
            headers = {
                "content-type": "application/json",
                "tr_id": tr_id,
                **self.auth.auth_headers(),
            }
            # hashkey required by many trading endpoints
            headers["hashkey"] = self.auth.hashkey(body)
            resp = requests.post(url, headers=headers, json=body, timeout=10)
            resp.raise_for_status()
            data: Dict[str, Any] = resp.json()
            if str(data.get("rt_cd", "1")) != "0":
                return OrderResponse(False, f"order failed: {data}")
            order_no = ((data.get("output") or {}) or {}).get("ODNO")
            return OrderResponse(
                True, "OK", order_no=str(order_no) if order_no else None
            )
        except Exception as e:
            self.log.exception("buy_market failed: %s", e)
            return OrderResponse(False, f"exception: {type(e).__name__}: {e}")

    def sell_market(self, symbol: str, qty: int) -> OrderResponse:
        blocked, reason = self.trading_blocked()
        if blocked:
            return OrderResponse(False, f"blocked: {reason}")
        if qty <= 0:
            return OrderResponse(False, "qty<=0")

        tr_id = "TTTC0801U"  # 현금매도(예시)
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": symbol,
            "ORD_DVSN": "01",
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
        }

        try:
            headers = {
                "content-type": "application/json",
                "tr_id": tr_id,
                **self.auth.auth_headers(),
            }
            headers["hashkey"] = self.auth.hashkey(body)
            resp = requests.post(url, headers=headers, json=body, timeout=10)
            resp.raise_for_status()
            data: Dict[str, Any] = resp.json()
            if str(data.get("rt_cd", "1")) != "0":
                return OrderResponse(False, f"order failed: {data}")
            order_no = ((data.get("output") or {}) or {}).get("ODNO")
            return OrderResponse(
                True, "OK", order_no=str(order_no) if order_no else None
            )
        except Exception as e:
            self.log.exception("sell_market failed: %s", e)
            return OrderResponse(False, f"exception: {type(e).__name__}: {e}")

    # ---------- price feed ----------
    def rest_get_last_price(self, symbol: str) -> float:
        """REST 현재가 조회(웹소켓 폴백)."""
        tr_id = "FHKST01010100"
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol}
        headers = {
            "content-type": "application/json",
            "tr_id": tr_id,
            **self.auth.auth_headers(),
        }
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data: Dict[str, Any] = resp.json()
        out = data.get("output") or {}
        # 현재가 키는 stck_prpr가 흔합니다.
        v = out.get("stck_prpr") or out.get("last")
        if v is None:
            raise RuntimeError(f"price missing for {symbol}")
        return float(v)

    async def price_stream(
        self, symbols: list[str], *, poll_interval_sec: float = 1.0
    ) -> AsyncIterator[Tuple[str, float]]:
        """웹소켓 우선 연결, 실패 시 REST 폴링으로 자동 폴백.

        kis 봇처럼:
        - ws_url이 있으면 approval_key를 자동 발급(fetch_approval_key)
        - 구독/수신은 KISWSMarketData로 위임
        - 파싱 실패/차단 시 REST 폴링
        """
        ws_url = os.getenv("KIS_WS_URL", "").strip()

        if ws_url:
            try:
                approval = self.auth.fetch_approval_key(force=False)
                ws = KISWSMarketData(
                    WSConfig(url=ws_url, approval_key=approval.approval_key),
                    symbols,
                    self.log,
                )
                async for item in ws.stream_prices():
                    yield item
                return
            except Exception as e:
                self.log.warning("ws stream failed -> fallback to REST polling: %s", e)

        # REST polling fallback
        while True:
            for sym in symbols:
                try:
                    px = self.rest_get_last_price(sym)
                    yield sym, px
                except Exception as e:
                    msg = f"[페어봇] REST price feed error sym={sym}: {type(e).__name__}: {e}"
                    self.log.warning("rest_get_last_price failed sym=%s: %s", sym, e)
                    self._notify_openclaw(msg)
                await asyncio.sleep(poll_interval_sec)
