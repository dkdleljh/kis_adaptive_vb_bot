from __future__ import annotations

"""data_handler.py

KIS API 연동(토큰 발급), 과거 일봉 데이터(OHLCV) 수집, 보조지표(MA, ATR, Noise) 사전 연산.

[지표 수학적 근거]
- ATR(20): 최근 20일 변동성의 대표값. (TR의 20일 평균)
  TR = max(H-L, |H-prevC|, |L-prevC|)

- Noise ratio(day): 1 - |C-O|/(H-L)
  - 몸통(|C-O|)이 크면 추세성(노이즈↓)
  - 꼬리(H-L)가 크면 변동/노이즈↑
  -> 최근 20일 평균을 사용하여 '노이즈 장'일수록 K를 보수적으로(작게) 만드는 데 사용.

- MA5: 단기 추세 필터(모순 필터)용.
"""

import os
import logging
from dataclasses import dataclass
from typing import Any, Dict, List

import requests

from kis_auth import KISAuth


@dataclass(frozen=True)
class OHLCV:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class KISDataHandler:
    def __init__(self, logger: logging.Logger) -> None:
        self.log = logger
        self.base_url = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443").strip()
        self.app_key = os.getenv("KIS_APP_KEY", "").strip()
        self.app_secret = os.getenv("KIS_APP_SECRET", "").strip()

        # 계좌 정보는 주문에 주로 쓰지만, 데이터 조회에서도 일부 엔드포인트가 요구할 수 있어 같이 검증
        self.cano = (os.getenv("KIS_ACCOUNT_NO") or os.getenv("KIS_CANO") or "").strip()
        self.acnt_prdt_cd = (os.getenv("KIS_ACCOUNT_PRODUCT_CODE") or os.getenv("KIS_ACNT_PRDT_CD") or "").strip()

        if not self.app_key or not self.app_secret:
            raise RuntimeError("KIS_APP_KEY / KIS_APP_SECRET 누락")
        if not self.cano or not self.acnt_prdt_cd:
            raise RuntimeError("KIS_ACCOUNT_NO/KIS_ACCOUNT_PRODUCT_CODE (또는 KIS_CANO/KIS_ACNT_PRDT_CD) 누락")

        # kis 봇과 동일: 토큰/레이트리밋/캐시 관리
        self.auth = KISAuth(self.base_url, self.app_key, self.app_secret, logger)

    def _auth_headers(self, tr_id: str) -> Dict[str, str]:
        return {"content-type": "application/json", "tr_id": tr_id, **self.auth.auth_headers()}

    def fetch_daily_ohlcv(self, symbol: str, *, days: int = 80) -> List[OHLCV]:
        """국내(ETF 포함) 일봉 조회.

        NOTE:
        - TR_ID/필드명은 KIS 문서/계정 설정에 따라 변경될 수 있습니다.
        - 이 구현은 흔히 쓰이는 일봉 API를 기준으로 best-effort 파싱합니다.
        """
        tr_id = "FHKST01010400"  # 일봉
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        resp = requests.get(url, headers=self._auth_headers(tr_id), params=params, timeout=10)
        resp.raise_for_status()
        data: Dict[str, Any] = resp.json()

        out = data.get("output2") or data.get("output") or []
        rows: List[OHLCV] = []
        for r in out[:days]:
            date = str(r.get("stck_bsop_date") or r.get("date") or "")
            o = float(r.get("stck_oprc") or r.get("open") or 0)
            h = float(r.get("stck_hgpr") or r.get("high") or 0)
            l = float(r.get("stck_lwpr") or r.get("low") or 0)
            c = float(r.get("stck_clpr") or r.get("close") or 0)
            v = float(r.get("acml_vol") or r.get("volume") or 0)
            if date:
                rows.append(OHLCV(date=date, open=o, high=h, low=l, close=c, volume=v))

        if len(rows) < 30:
            self.log.warning("OHLCV rows small symbol=%s n=%s", symbol, len(rows))
        return list(reversed(rows))  # old -> new

    def fetch_open_price_0900(self, symbol: str) -> float:
        """09:00 시가 조회.

        실전에서는 '당일 시가'를 정확히 사용해야 하므로 현재가/시가 API를 호출합니다.

        NOTE: TR_ID/키는 환경에 따라 다를 수 있습니다.
        """
        tr_id = "FHKST01010100"  # 현재가(예시)
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol}
        resp = requests.get(url, headers=self._auth_headers(tr_id), params=params, timeout=10)
        resp.raise_for_status()
        data: Dict[str, Any] = resp.json()
        out = data.get("output") or {}
        # 시가는 stck_oprc가 제공되는 경우가 많습니다.
        v = out.get("stck_oprc") or out.get("open")
        if v is None:
            # fallback: 현재가를 시가로 대신하지 않도록 예외
            raise RuntimeError(f"open price missing for {symbol}: keys={list(out.keys())[:20]}")
        return float(v)

    @staticmethod
    def calc_ma5(ohlcv: List[OHLCV]) -> float:
        if len(ohlcv) < 5:
            raise ValueError("MA5 requires >=5 bars")
        closes = [b.close for b in ohlcv[-5:]]
        return sum(closes) / 5.0

    @staticmethod
    def calc_atr20(ohlcv: List[OHLCV]) -> float:
        if len(ohlcv) < 21:
            raise ValueError("ATR20 requires >=21 bars")
        trs: List[float] = []
        for i in range(1, len(ohlcv)):
            h, l, prev_c = ohlcv[i].high, ohlcv[i].low, ohlcv[i - 1].close
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)
        return sum(trs[-20:]) / 20.0

    @staticmethod
    def calc_noise20avg(ohlcv: List[OHLCV]) -> float:
        if len(ohlcv) < 20:
            raise ValueError("Noise20 requires >=20 bars")
        noises: List[float] = []
        for b in ohlcv[-20:]:
            denom = (b.high - b.low)
            if denom <= 0:
                continue
            noise = 1.0 - (abs(b.close - b.open) / denom)
            noises.append(max(0.0, min(1.0, noise)))
        return (sum(noises) / len(noises)) if noises else 0.5
