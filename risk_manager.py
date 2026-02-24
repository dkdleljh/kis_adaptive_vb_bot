from __future__ import annotations

"""risk_manager.py

변동성 타겟팅(Volatility Targeting) 기반 주문 수량 산출 및 트레일링 스탑 계산.

[리스크 예산]
- 하루 최대 허용 리스크: 총 자본금의 1%

[진입 수량]
Shares = floor((총자본 * 0.01) / ATR20)

수학적 의미:
- ATR을 '손실 1단위'로 보고, 최대 손실 예산(1%) 안에서 수량을 제한.

[샹들리에(Chandelier) 트레일링 스탑]
Stop = 장중 최고가 - ATR20*1.5
- 변동성(ATR)에 비례한 스탑으로, 변동성이 커지면 스탑 폭도 넓어짐.
"""

import math
import logging
from dataclasses import dataclass


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: int
    entry_price: float
    highest_price: float
    trailing_stop: float


class RiskManager:
    def __init__(self, logger: logging.Logger, *, capital_krw: float) -> None:
        self.log = logger
        self.capital = float(capital_krw)

    def calc_entry_qty(self, atr20: float) -> int:
        if atr20 <= 0:
            return 0
        budget = self.capital * 0.01
        qty = int(math.floor(budget / atr20))
        return max(0, qty)

    def init_position(self, *, symbol: str, qty: int, entry_price: float, atr20: float) -> Position:
        highest = float(entry_price)
        stop = float(highest - atr20 * 1.5)
        return Position(symbol=symbol, qty=qty, entry_price=float(entry_price), highest_price=highest, trailing_stop=stop)

    def update(self, pos: Position, *, current_price: float, atr20: float) -> Position:
        highest = max(pos.highest_price, float(current_price))
        stop = float(highest - atr20 * 1.5)
        return Position(
            symbol=pos.symbol,
            qty=pos.qty,
            entry_price=pos.entry_price,
            highest_price=highest,
            trailing_stop=stop,
        )
