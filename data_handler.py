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

    def fetch_open_price_0900(self, symbol: str, *, retries: int = 12, sleep_sec: float = 0.5) -> float:
        """09:00 시가 조회(실전 안정형).

        문제
        - 장 시작 직후(09:00:00~)에는 시가 필드가 0 또는 None으로 내려오는 경우가 있습니다.
        - 기존 구현은 0을 그대로 반환해 모순필터가 "둘 다 MA5 아래"로 판정되며 당일 스킵이 나올 수 있습니다.

        해결
        - 짧게 재시도(retries) 후에도 시가가 0이면, 마지막으로 현재가(stck_prpr)를 대체값으로 사용합니다.
          (정확한 '시가'가 아닐 수 있으나, "0"보다 훨씬 안전하며 실전에서 거래가 멈추는 문제를 방지)

        NOTE
        - TR_ID/필드명은 KIS 문서/계정 설정에 따라 다를 수 있습니다.
        """
        tr_id = "FHKST01010100"  # 현재가(예시)
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol}

        last_out: Dict[str, Any] = {}
        for i in range(max(1, int(retries))):
            resp = requests.get(url, headers=self._auth_headers(tr_id), params=params, timeout=10)
            resp.raise_for_status()
            data: Dict[str, Any] = resp.json()
            out = data.get("output") or {}
            last_out = out

            v = out.get("stck_oprc") or out.get("open")
            try:
                if v is not None and float(v) > 0:
                    return float(v)
            except Exception:
                pass

            # 잠깐 대기 후 재시도
            if i < retries - 1:
                import time
                time.sleep(float(sleep_sec))

        # 최종 fallback: 현재가로 대체(0 방지)
        v2 = last_out.get("stck_prpr") or last_out.get("last") or last_out.get("stck_clpr")
        if v2 is None:
            raise RuntimeError(f"open price missing for {symbol}: keys={list(last_out.keys())[:20]}")
        try:
            if float(v2) <= 0:
                raise RuntimeError(f"open price invalid/zero for {symbol}: out={ {k:last_out.get(k) for k in ['stck_oprc','stck_prpr','stck_clpr']} }")
        except Exception as e:
            raise RuntimeError(f"open price invalid for {symbol}: {e}")

        self.log.warning("open price fallback to stck_prpr for %s (oprc was 0/None)", symbol)
        return float(v2)

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
