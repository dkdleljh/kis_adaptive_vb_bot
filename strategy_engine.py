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
        k = float(noise20avg) * float(y_range / atr20)
        # 과도한 K가 나오면 실전에서는 클램프 권장. (명세에 없으므로 경고만)
        if k > 2.0:
            self.log.warning("K unusually high: %.4f (noise=%.4f, y_range=%.4f, atr=%.4f)", k, noise20avg, y_range, atr20)

        target = float(open_price_chosen + (y_range * k))
        return GroupDecision(True, chosen, "APPROVED", k_value=k, target_price=target)
