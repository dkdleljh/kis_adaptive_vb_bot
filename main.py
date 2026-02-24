from __future__ import annotations

"""main.py

08:50 데이터 갱신 -> 09:00 감시 -> 15:15 일괄 청산 사이클을 제어.

[전략 타임라인]
- 08:50: 일봉/지표 갱신
- 09:00~13:00: 목표가 돌파 진입(가짜돌파 필터)
- 진입 후: 샹들리에(ATR*1.5) 트레일링 스탑으로 즉시 청산
- 15:15: 오버나잇 금지, 무조건 전량 시장가 청산 후 종료

실전 안정장치(kis 봇과 동일):
- KIS_LIVE_ENABLED=1
- KIS_LIVE_CONFIRM=YES
- KIS_KILL_SWITCH=0
- STOP_TRADING.flag 없음
위 조건이 모두 만족할 때만 주문 전송
"""

import os
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Dict, Optional

from dotenv import load_dotenv

from data_handler import KISDataHandler
from strategy_engine import StrategyEngine, GroupDecision
from risk_manager import RiskManager, Position
from execution_handler import ExecutionHandler
from utils_holiday import is_market_open, get_holiday_name

KST = ZoneInfo("Asia/Seoul")

# Universe
KOSPI_LEV = "122630"
KOSPI_INV = "252670"
KOSDAQ_LEV = "233740"
KOSDAQ_INV = "251340"


@dataclass
class GroupCtx:
    name: str
    lev: str
    inv: str
    decision: Optional[GroupDecision] = None
    chosen_open: float = 0.0
    atr20: float = 0.0
    entered: bool = False
    position: Optional[Position] = None


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def ts(t: dtime) -> dtime:
    return t.replace(tzinfo=KST)


def in_window(now: datetime, start: dtime, end: dtime) -> bool:
    return ts(start) <= now.timetz() <= ts(end)


async def sleep_until(target: dtime) -> None:
    while True:
        n = now_kst()
        if n.timetz() >= ts(target):
            return
        await asyncio.sleep(0.5)


async def run() -> None:
    project_root = Path(__file__).resolve().parent

    load_dotenv(project_root / ".env")

    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("adaptive_vb")

    capital = float(os.getenv("CAPITAL_KRW", "10000000"))
    poll_interval = float(os.getenv("PRICE_POLL_INTERVAL_SEC", "1.0"))

    # 휴장일/주말 자동 스킵
    today = now_kst().date()
    if not is_market_open(today):
        log.info("Market closed today (%s): %s. Exiting.", today.isoformat(), get_holiday_name(today))
        return

    data = KISDataHandler(log)
    strat = StrategyEngine(log)
    risk = RiskManager(log, capital_krw=capital)
    execu = ExecutionHandler(log, project_root=project_root)

    groups: Dict[str, GroupCtx] = {
        "KOSPI": GroupCtx("KOSPI", KOSPI_LEV, KOSPI_INV),
        "KOSDAQ": GroupCtx("KOSDAQ", KOSDAQ_LEV, KOSDAQ_INV),
    }

    # 08:50 데이터 갱신
    await sleep_until(dtime(8, 50))
    log.info("[08:50] refresh start")

    cache: Dict[str, dict] = {}
    for g in groups.values():
        ohlc_lev = data.fetch_daily_ohlcv(g.lev, days=90)
        ohlc_inv = data.fetch_daily_ohlcv(g.inv, days=90)

        cache[g.name] = {
            "ohlc_lev": ohlc_lev,
            "ohlc_inv": ohlc_inv,
            "ma5_lev": data.calc_ma5(ohlc_lev),
            "ma5_inv": data.calc_ma5(ohlc_inv),
            "atr20_lev": data.calc_atr20(ohlc_lev),
            "atr20_inv": data.calc_atr20(ohlc_inv),
            "noise20_lev": data.calc_noise20avg(ohlc_lev),
            "noise20_inv": data.calc_noise20avg(ohlc_inv),
            "yday_lev": ohlc_lev[-2],
            "yday_inv": ohlc_inv[-2],
        }

    log.info("[08:50] refresh done")

    # 09:00 의사결정
    await sleep_until(dtime(9, 0))
    log.info("[09:00] decision start")

    for g in groups.values():
        c = cache[g.name]

        # 09:00 시가(실전 현재가 API에서 시가 필드 사용)
        open_lev = data.fetch_open_price_0900(g.lev)
        open_inv = data.fetch_open_price_0900(g.inv)

        # chosen 종목 기준으로 atr/noise/yday/open를 선택해서 목표가를 계산
        lev_above = open_lev > float(c["ma5_lev"])
        inv_above = open_inv > float(c["ma5_inv"])
        if lev_above == inv_above:
            g.decision = GroupDecision(False, None, f"SKIP contradiction lev_above={lev_above} inv_above={inv_above}")
            continue

        if lev_above:
            atr20 = float(c["atr20_lev"])
            noise = float(c["noise20_lev"])
            yday = c["yday_lev"]
            open_chosen = float(open_lev)
        else:
            atr20 = float(c["atr20_inv"])
            noise = float(c["noise20_inv"])
            yday = c["yday_inv"]
            open_chosen = float(open_inv)

        decision = strat.decide(
            lev_symbol=g.lev,
            inv_symbol=g.inv,
            open_lev=float(open_lev),
            open_inv=float(open_inv),
            ma5_lev=float(c["ma5_lev"]),
            ma5_inv=float(c["ma5_inv"]),
            atr20=atr20,
            noise20avg=noise,
            yday_bar=yday,
            open_price_chosen=open_chosen,
        )

        g.decision = decision
        g.chosen_open = open_chosen
        g.atr20 = atr20
        log.info("[%s] decision=%s", g.name, decision)

    # 감시 대상(승인된 종목만)
    symbols = [g.decision.chosen_symbol for g in groups.values() if g.decision and g.decision.approved and g.decision.chosen_symbol]
    symbols = [s for s in symbols if s]

    if not symbols:
        log.info("No approved symbols today. Will just wait until 15:15 and exit.")
        await sleep_until(dtime(15, 15))
        return

    entry_start = dtime(9, 0)
    entry_end = dtime(13, 0)
    force_exit = dtime(15, 15)

    log.info("monitor start symbols=%s", symbols)

    # 실시간 가격 스트림(웹소켓 우선, 실패 시 REST 폴링)
    async for sym, px in execu.price_stream(symbols, poll_interval_sec=poll_interval):
        n = now_kst()

        # 15:15 강제 청산
        if n.timetz() >= ts(force_exit):
            log.info("[15:15] force exit")
            break

        # 그룹 매핑
        for g in groups.values():
            if not g.decision or not g.decision.approved or not g.decision.chosen_symbol:
                continue
            if g.decision.chosen_symbol != sym:
                continue

            # 진입(09:00~13:00만)
            if (not g.entered) and in_window(n, entry_start, entry_end):
                # 목표가 돌파 매수
                if px >= float(g.decision.target_price):
                    qty = risk.calc_entry_qty(g.atr20)
                    if qty <= 0:
                        log.warning("[%s] qty=0 skip", g.name)
                        g.entered = True
                        continue

                    res = execu.buy_market(sym, qty)
                    log.info("[%s] BUY %s qty=%s px=%.2f target=%.2f => %s", g.name, sym, qty, px, g.decision.target_price, res)
                    if res.ok:
                        g.entered = True
                        g.position = risk.init_position(symbol=sym, qty=qty, entry_price=px, atr20=g.atr20)
                    else:
                        # 주문 실패 시에도 무한 재시도는 위험 -> 해당 그룹은 오늘 종료
                        g.entered = True

            # 트레일링 스탑
            if g.position:
                g.position = risk.update(g.position, current_price=px, atr20=g.atr20)
                if px <= g.position.trailing_stop:
                    res = execu.sell_market(sym, g.position.qty)
                    log.info("[%s] STOP SELL %s qty=%s px=%.2f stop=%.2f => %s", g.name, sym, g.position.qty, px, g.position.trailing_stop, res)
                    # 어떤 결과든 포지션 종료 처리(재시도는 별도 운영)
                    g.position = None

    # 강제 청산
    for g in groups.values():
        if g.position:
            res = execu.sell_market(g.position.symbol, g.position.qty)
            log.info("[%s] FORCE SELL %s qty=%s => %s", g.name, g.position.symbol, g.position.qty, res)
            g.position = None


if __name__ == "__main__":
    asyncio.run(run())
