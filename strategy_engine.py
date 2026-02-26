from __future__ import annotations

"""strategy_engine.py

모순 필터링 및 동적 K값 기반 타겟 프라이스(목표가) 산출.

[모순 필터]
- 같은 그룹(레버리지/인버스)에서
  - 오직 1개만 MA5 위(09:00 시가 기준)일 때만 거래 승인
  - 둘 다 위/둘 다 아래면 당일 Skip

[동적 K]
K = (Noise20avg) * (전일 고저 변동폭 / ATR20)

[목표가]
Target = 당일 시가 + (전일 고저 변동폭 * K)
"""

import logging
from dataclasses import dataclass
from typing import Optional

from data_handler import OHLCV


@dataclass(frozen=True)
class GroupDecision:
    approved: bool
    chosen_symbol: Optional[str]
    reason: str
    k_value: float = 0.0
    target_price: float = 0.0


class StrategyEngine:
    def __init__(self, logger: logging.Logger) -> None:
        self.log = logger
        # 실전 운영용 캡(과도한 목표가 방지). 0이면 비활성.
        # 예) K_MAX=0.60, TARGET_GAP_PCT_MAX=0.02 (시가 대비 +2% 상한)
        import os
        self.k_max = float(os.getenv("PAIRBOT_K_MAX", "0") or 0)
        self.target_gap_pct_max = float(os.getenv("PAIRBOT_TARGET_GAP_PCT_MAX", "0") or 0)

    def decide(
        self,
        *,
        lev_symbol: str,
        inv_symbol: str,
        open_lev: float,
        open_inv: float,
        ma5_lev: float,
        ma5_inv: float,
        atr20: float,
        noise20avg: float,
        yday_bar: OHLCV,
        open_price_chosen: float,
    ) -> GroupDecision:
        lev_above = open_lev > ma5_lev
        inv_above = open_inv > ma5_inv

        if lev_above == inv_above:
            return GroupDecision(False, None, f"SKIP: contradiction (lev_above={lev_above}, inv_above={inv_above})")

        chosen = lev_symbol if lev_above else inv_symbol

        y_range = max(0.0, float(yday_bar.high - yday_bar.low))
        if atr20 <= 0 or y_range <= 0:
            return GroupDecision(False, None, f"SKIP: invalid atr/range (atr20={atr20:.4f}, y_range={y_range:.4f})")

        # 수학적 근거:
        # - noise20avg: 노이즈 장(휩쏘)일수록 K를 줄여(보수적으로) 가짜돌파를 줄이는 방향.
        # - y_range/atr20: 전일 변동성이 최근 평균(ATR) 대비 얼마나 큰지. 큰 날은 목표가가 더 멀어짐.
        k_raw = float(noise20avg) * float(y_range / atr20)
        k = k_raw

        # K 캡(옵션)
        if self.k_max and self.k_max > 0:
            if k > self.k_max:
                self.log.info("K capped: raw=%.4f -> cap=%.4f", k_raw, self.k_max)
                k = self.k_max

        # 참고 경고(비정상적으로 큰 값)
        if k_raw > 2.0:
            self.log.warning("K unusually high: %.4f (noise=%.4f, y_range=%.4f, atr=%.4f)", k_raw, noise20avg, y_range, atr20)

        target_raw = float(open_price_chosen + (y_range * k))
        target = target_raw

        # 목표가 거리(%) 캡(옵션): Target <= Open*(1+gap)
        if self.target_gap_pct_max and self.target_gap_pct_max > 0:
            cap = float(open_price_chosen) * (1.0 + float(self.target_gap_pct_max))
            if target > cap:
                self.log.info("Target capped: raw=%.2f -> cap=%.2f (gap_cap=%.3f)", target_raw, cap, self.target_gap_pct_max)
                target = cap

        return GroupDecision(True, chosen, "APPROVED", k_value=k, target_price=target)
