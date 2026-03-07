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
import time
import asyncio
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Dict, Optional, Any, TypedDict, cast

from reporting import ReportManager, TradeRecord

from data_handler import KISDataHandler, OHLCV
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


class CacheEntry(TypedDict):
    ohlc_lev: list[OHLCV]
    ohlc_inv: list[OHLCV]
    ma5_lev: float
    ma5_inv: float
    atr20_lev: float
    atr20_inv: float
    noise20_lev: float
    noise20_inv: float
    yday_lev: OHLCV
    yday_inv: OHLCV


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def ts(t: dtime) -> dtime:
    return t.replace(tzinfo=KST)


def in_window(now: datetime, start: dtime, end: dtime) -> bool:
    return ts(start) <= now.timetz() <= ts(end)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue

        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        os.environ[key] = value


async def sleep_until(target: dtime) -> None:
    while True:
        n = now_kst()
        if n.timetz() >= ts(target):
            return
        await asyncio.sleep(0.5)


async def run() -> None:
    project_root = Path(__file__).resolve().parent

    load_env_file(project_root / ".env")

    level = os.getenv("LOG_LEVEL", "INFO").upper()

    # ---- logging (console + file) ----
    # 운영 시 'nohup 로그'만 남기면 찾기/요약이 어렵습니다.
    # logs/에 날짜별 파일을 추가로 남깁니다.
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    day = now_kst().date().isoformat()
    file_path = log_dir / f"pairbot_{day}.log"

    log = logging.getLogger("adaptive_vb")
    log.setLevel(getattr(logging, level, logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    # 중복 핸들러 방지
    if not log.handlers:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        fh = logging.FileHandler(file_path, encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(sh)
        log.addHandler(fh)

    log.propagate = False

    capital = float(os.getenv("CAPITAL_KRW", "10000000"))
    max_position_notional_pct = float(
        os.getenv("PAIRBOT_MAX_POSITION_NOTIONAL_PCT", "0.5")
    )
    poll_interval = float(os.getenv("PRICE_POLL_INTERVAL_SEC", "1.0"))

    # ---- report manager ----
    report = ReportManager(project_root=project_root, run_ts=now_kst())
    report.write_meta(
        universe={
            "KOSPI": {"lev": KOSPI_LEV, "inv": KOSPI_INV},
            "KOSDAQ": {"lev": KOSDAQ_LEV, "inv": KOSDAQ_INV},
        },
        config={
            "capital_krw": capital,
            "max_position_notional_pct": max_position_notional_pct,
            "poll_interval_sec": poll_interval,
            "log_level": level,
            "live_gate": {
                "KIS_LIVE_ENABLED": os.getenv("KIS_LIVE_ENABLED", "0"),
                "KIS_LIVE_CONFIRM": os.getenv("KIS_LIVE_CONFIRM", "NO"),
                "KIS_KILL_SWITCH": os.getenv("KIS_KILL_SWITCH", "1"),
                "stop_flag": str((project_root / "STOP_TRADING.flag").exists()),
            },
        },
    )

    # 휴장일/주말 자동 스킵
    today = now_kst().date()
    if not is_market_open(today):
        msg = f"Market closed today ({today.isoformat()}): {get_holiday_name(today)}. Exiting."
        log.info(msg)
        report.event(
            "market_closed", date=today.isoformat(), holiday=get_holiday_name(today)
        )
        report.finalize(
            summary={
                "date": today.isoformat(),
                "market_open": False,
                "universe": {
                    "KOSPI": {"lev": KOSPI_LEV, "inv": KOSPI_INV},
                    "KOSDAQ": {"lev": KOSDAQ_LEV, "inv": KOSDAQ_INV},
                },
                "groups": {},
                "notes": [msg],
            }
        )
        return

    data = KISDataHandler(log)
    strat = StrategyEngine(log)
    risk = RiskManager(
        log, capital_krw=capital, max_position_notional_pct=max_position_notional_pct
    )
    execu = ExecutionHandler(log, project_root=project_root)

    groups: Dict[str, GroupCtx] = {
        "KOSPI": GroupCtx("KOSPI", KOSPI_LEV, KOSPI_INV),
        "KOSDAQ": GroupCtx("KOSDAQ", KOSDAQ_LEV, KOSDAQ_INV),
    }

    # 08:50 데이터 갱신
    await sleep_until(dtime(8, 50))
    log.info("[08:50] refresh start")

    cache: Dict[str, CacheEntry] = {}
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

    # 09:01 의사결정(장 시작 직후 시가=0 내려오는 케이스 완화)
    await sleep_until(dtime(9, 1))
    log.info("[09:01] decision start")

    for g in groups.values():
        c = cache[g.name]

        # 09:00 시가(실전 현재가 API에서 시가 필드 사용)
        open_lev = data.fetch_open_price_0900(g.lev)
        open_inv = data.fetch_open_price_0900(g.inv)

        # chosen 종목 기준으로 atr/noise/yday/open를 선택해서 목표가를 계산
        lev_above = open_lev > float(c["ma5_lev"])
        inv_above = open_inv > float(c["ma5_inv"])
        if lev_above == inv_above:
            g.decision = GroupDecision(
                False,
                None,
                f"SKIP contradiction lev_above={lev_above} inv_above={inv_above}",
            )
            log.info("[%s] %s", g.name, g.decision.reason)
            report.event(
                "decision_skip",
                group=g.name,
                reason=g.decision.reason,
                open_lev=float(open_lev),
                open_inv=float(open_inv),
                ma5_lev=float(c["ma5_lev"]),
                ma5_inv=float(c["ma5_inv"]),
            )
            continue

        if lev_above:
            atr20 = float(c["atr20_lev"])
            noise = float(c["noise20_lev"])
            yday = cast(OHLCV, c["yday_lev"])
            open_chosen = float(open_lev)
        else:
            atr20 = float(c["atr20_inv"])
            noise = float(c["noise20_inv"])
            yday = cast(OHLCV, c["yday_inv"])
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

        # 리포트 기록(결정 + 주요 입력)
        try:
            report.decision(
                g.name,
                decision,
                open_lev=float(open_lev),
                open_inv=float(open_inv),
                ma5_lev=float(c["ma5_lev"]),
                ma5_inv=float(c["ma5_inv"]),
                atr20=float(atr20),
                noise20avg=float(noise),
            )
        except Exception as e:
            log.warning("report.decision failed: %s", e)

    # 감시 대상(승인된 종목만)
    symbols = [
        g.decision.chosen_symbol
        for g in groups.values()
        if g.decision and g.decision.approved and g.decision.chosen_symbol
    ]
    symbols = [s for s in symbols if s]

    def finalize_run(summary: Dict[str, Any]) -> None:
        try:
            report.finalize(summary=summary)
            log.info("report written: %s", report.out_dir)

            if os.getenv("REPORT_NOTIFY_OPENCLAW", "0") == "1":
                text = report.build_notification_text(
                    summary=summary,
                    max_chars=int(os.getenv("REPORT_NOTIFY_MAXCHARS", "3500")),
                )
                _ = subprocess.run(
                    ["openclaw", "system", "event", "--text", text, "--mode", "now"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception as e:
            log.warning("report.finalize failed: %s", e)

    if not symbols:
        log.info("No approved symbols today. Will just wait until 15:15 and exit.")
        await sleep_until(dtime(15, 15))
        summary_groups = {}
        for g in groups.values():
            summary_groups[g.name] = {
                "decision": getattr(g.decision, "reason", None) if g.decision else None,
                "chosen": getattr(g.decision, "chosen_symbol", None)
                if g.decision
                else None,
                "target": float(getattr(g.decision, "target_price", 0.0))
                if (
                    g.decision and getattr(g.decision, "target_price", None) is not None
                )
                else None,
                "entered": bool(g.entered),
                "exit": None,
            }

        summary = {
            "date": now_kst().date().isoformat(),
            "market_open": True,
            "universe": {
                "KOSPI": {"lev": KOSPI_LEV, "inv": KOSPI_INV},
                "KOSDAQ": {"lev": KOSDAQ_LEV, "inv": KOSDAQ_INV},
            },
            "groups": summary_groups,
            "notes": [
                "No approved symbols today. Stayed flat until force-exit time.",
                "체결가는 주문 직전 관측가격(px)을 가정값으로 기록합니다.",
                "정확한 정산은 별도 체결조회/증권사 체결내역과 대조하세요.",
            ],
        }
        finalize_run(summary)
        return

    entry_start = dtime(9, 0)
    entry_end = dtime(13, 0)
    force_exit = dtime(15, 15)

    log.info("monitor start symbols=%s", symbols)

    # (1) px vs target periodic log
    pxlog_interval = float(os.getenv("PAIRBOT_PXLOG_INTERVAL_SEC", "30"))
    _last_pxlog: Dict[str, float] = {g.name: 0.0 for g in groups.values()}

    # 실시간 가격 스트림(웹소켓 우선, 실패 시 REST 폴링)
    async for sym, px in execu.price_stream(symbols, poll_interval_sec=poll_interval):
        n = now_kst()

        # 15:15 강제 청산
        if n.timetz() >= ts(force_exit):
            log.info("[15:15] force exit")
            break

        # 그룹 매핑
        for g in groups.values():
            if (
                not g.decision
                or not g.decision.approved
                or not g.decision.chosen_symbol
            ):
                continue
            if g.decision.chosen_symbol != sym:
                continue

            # (1) 주기 로그: 현재가 vs 목표가
            try:
                if (
                    pxlog_interval > 0
                    and (time.time() - _last_pxlog.get(g.name, 0.0)) >= pxlog_interval
                ):
                    _last_pxlog[g.name] = time.time()
                    tgt = float(g.decision.target_price)
                    gap = (tgt / px - 1.0) * 100.0 if px > 0 else float("nan")
                    log.info(
                        "[%s] px=%.2f target=%.2f gap=%.2f%% entered=%s",
                        g.name,
                        px,
                        tgt,
                        gap,
                        g.entered,
                    )
            except Exception:
                pass

            # 진입(09:00~13:00만)
            if (not g.entered) and in_window(n, entry_start, entry_end):
                # 목표가 돌파 매수
                if px >= float(g.decision.target_price):
                    qty = risk.calc_entry_qty(atr20=g.atr20, entry_price=px)
                    if qty <= 0:
                        log.warning("[%s] qty=0 skip", g.name)
                        g.entered = True
                        continue

                    res = execu.buy_market(sym, qty)
                    log.info(
                        "[%s] BUY %s qty=%s px=%.2f target=%.2f => %s",
                        g.name,
                        sym,
                        qty,
                        px,
                        g.decision.target_price,
                        res,
                    )
                    report.trade(
                        TradeRecord(
                            ts=now_kst().isoformat(timespec="seconds"),
                            group=g.name,
                            symbol=sym,
                            side="BUY",
                            qty=int(qty),
                            px_assumed=float(px),
                            reason="breakout>=target",
                            order_no=getattr(res, "order_no", None),
                            ok=bool(getattr(res, "ok", False)),
                            msg=str(getattr(res, "msg", "")),
                        )
                    )
                    if res.ok:
                        g.entered = True
                        g.position = risk.init_position(
                            symbol=sym, qty=qty, entry_price=px, atr20=g.atr20
                        )
                    else:
                        # 주문 실패 시에도 무한 재시도는 위험 -> 해당 그룹은 오늘 종료
                        g.entered = True

            # 트레일링 스탑
            if g.position:
                g.position = risk.update(g.position, current_price=px, atr20=g.atr20)
                if px <= g.position.trailing_stop:
                    res = execu.sell_market(sym, g.position.qty)
                    log.info(
                        "[%s] STOP SELL %s qty=%s px=%.2f stop=%.2f => %s",
                        g.name,
                        sym,
                        g.position.qty,
                        px,
                        g.position.trailing_stop,
                        res,
                    )
                    report.trade(
                        TradeRecord(
                            ts=now_kst().isoformat(timespec="seconds"),
                            group=g.name,
                            symbol=sym,
                            side="SELL",
                            qty=int(g.position.qty),
                            px_assumed=float(px),
                            reason=f"trailing_stop<=px (stop={g.position.trailing_stop:.2f})",
                            order_no=getattr(res, "order_no", None),
                            ok=bool(getattr(res, "ok", False)),
                            msg=str(getattr(res, "msg", "")),
                        )
                    )
                    # 어떤 결과든 포지션 종료 처리(재시도는 별도 운영)
                    g.position = None

    # 강제 청산
    for g in groups.values():
        if g.position:
            res = execu.sell_market(g.position.symbol, g.position.qty)
            log.info(
                "[%s] FORCE SELL %s qty=%s => %s",
                g.name,
                g.position.symbol,
                g.position.qty,
                res,
            )
            report.trade(
                TradeRecord(
                    ts=now_kst().isoformat(timespec="seconds"),
                    group=g.name,
                    symbol=g.position.symbol,
                    side="SELL",
                    qty=int(g.position.qty),
                    px_assumed=float("nan"),
                    reason="force_exit_15_15",
                    order_no=getattr(res, "order_no", None),
                    ok=bool(getattr(res, "ok", False)),
                    msg=str(getattr(res, "msg", "")),
                )
            )
            g.position = None

    # 리포트 마무리
    summary_groups = {}
    for g in groups.values():
        summary_groups[g.name] = {
            "decision": getattr(g.decision, "reason", None) if g.decision else None,
            "chosen": getattr(g.decision, "chosen_symbol", None)
            if g.decision
            else None,
            "target": float(getattr(g.decision, "target_price", 0.0))
            if (g.decision and getattr(g.decision, "target_price", None) is not None)
            else None,
            "entered": bool(g.entered),
            "exit": None,
        }

    summary = {
        "date": now_kst().date().isoformat(),
        "market_open": True,
        "universe": {
            "KOSPI": {"lev": KOSPI_LEV, "inv": KOSPI_INV},
            "KOSDAQ": {"lev": KOSDAQ_LEV, "inv": KOSDAQ_INV},
        },
        "groups": summary_groups,
        "notes": [
            "체결가는 주문 직전 관측가격(px)을 가정값으로 기록합니다.",
            "정확한 정산은 별도 체결조회/증권사 체결내역과 대조하세요.",
        ],
    }
    finalize_run(summary)


if __name__ == "__main__":
    asyncio.run(run())
