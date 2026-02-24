from __future__ import annotations

"""reporting.py

페어봇(Adaptive Volatility Breakout)의 **운영 친화성**을 위한 로그/리포트 모듈.

목표
- 실행 중 이벤트를 구조화(JSONL)로 기록해 "나중에" 정리/분석하기 쉽게 한다.
- 실행 종료 시 사람이 바로 읽을 수 있는 Markdown 리포트를 생성한다.
- 전략/집행/예외를 한 파일(log)로만 남기지 않고, '요약'을 따로 만든다.

생성물(기본 경로: project_root/reports/YYYY-MM-DD/)
- run_meta.json                : 실행 메타(버전, 설정 일부, 유니버스 등)
- events.jsonl                 : 타임라인 이벤트(결정/진입/청산/에러)
- trades.csv                   : 체결(가정) 기반 거래 요약(백테스트용이 아님)
- report.md                    : 사람이 읽는 일일 리포트

주의
- 실제 체결가는 KIS 주문 API 응답에 체결가가 포함되지 않는 경우가 흔합니다.
  본 모듈은 "주문 직전 관측 가격(px)"을 **가정 체결가**로 기록할 수 있습니다.
  정확한 정산/체결 내역은 별도 체결조회(get_fills 등)와 대조하세요.
"""

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List


@dataclass
class TradeRecord:
    ts: str
    group: str
    symbol: str
    side: str  # BUY/SELL
    qty: int
    px_assumed: float
    reason: str
    order_no: Optional[str] = None
    ok: bool = True
    msg: str = ""


class ReportManager:
    def __init__(self, *, project_root: Path, run_ts: datetime, tz_name: str = "Asia/Seoul") -> None:
        self.root = project_root
        self.run_ts = run_ts
        self.tz_name = tz_name

        day = run_ts.date().isoformat()
        self.out_dir = self.root / "reports" / day
        self.out_dir.mkdir(parents=True, exist_ok=True)

        self.events_path = self.out_dir / "events.jsonl"
        self.meta_path = self.out_dir / "run_meta.json"
        self.report_path = self.out_dir / "report.md"
        self.trades_path = self.out_dir / "trades.csv"

        self._trades: List[TradeRecord] = []

    # ---------- low-level writers ----------
    def _write_jsonl(self, obj: Dict[str, Any]) -> None:
        line = json.dumps(obj, ensure_ascii=False)
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def event(self, kind: str, **fields: Any) -> None:
        payload: Dict[str, Any] = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "kind": kind,
            **fields,
        }
        self._write_jsonl(payload)

    # ---------- domain helpers ----------
    def write_meta(self, *, universe: Dict[str, Dict[str, str]], config: Dict[str, Any]) -> None:
        meta = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "timezone": self.tz_name,
            "universe": universe,
            "config": config,
        }
        self.meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def decision(self, group: str, decision: Any, *, open_lev: float, open_inv: float, ma5_lev: float, ma5_inv: float, atr20: float, noise20avg: float) -> None:
        # decision은 dataclass일 수도 있고, 일반 객체일 수도 있어 dict 변환을 시도.
        try:
            d = asdict(decision)
        except Exception:
            d = {k: getattr(decision, k) for k in dir(decision) if not k.startswith("_") and k in {"approved","chosen_symbol","target_price","k","reason"}}

        self.event(
            "decision",
            group=group,
            decision=d,
            open_lev=open_lev,
            open_inv=open_inv,
            ma5_lev=ma5_lev,
            ma5_inv=ma5_inv,
            atr20=atr20,
            noise20avg=noise20avg,
        )

    def trade(self, rec: TradeRecord) -> None:
        self._trades.append(rec)
        self.event("trade", **asdict(rec))

    def error(self, where: str, message: str, **fields: Any) -> None:
        self.event("error", where=where, message=message, **fields)

    # ---------- finalize ----------
    def _write_trades_csv(self) -> None:
        header = "ts,group,symbol,side,qty,px_assumed,reason,order_no,ok,msg\n"
        lines = [header]
        for t in self._trades:
            # csv minimal escaping
            def esc(s: Any) -> str:
                v = "" if s is None else str(s)
                v = v.replace('"', '""')
                if any(c in v for c in [",", "\n", "\r"]):
                    return f'"{v}"'
                return v

            lines.append(
                ",".join(
                    [
                        esc(t.ts),
                        esc(t.group),
                        esc(t.symbol),
                        esc(t.side),
                        esc(t.qty),
                        esc(f"{t.px_assumed:.2f}"),
                        esc(t.reason),
                        esc(t.order_no or ""),
                        esc("1" if t.ok else "0"),
                        esc(t.msg),
                    ]
                )
                + "\n"
            )
        self.trades_path.write_text("".join(lines), encoding="utf-8")

    def finalize_markdown(self, *, summary: Dict[str, Any]) -> None:
        """사람이 읽는 리포트 생성.

        summary 예시:
          {
            "date": "2026-02-24",
            "market_open": True,
            "groups": {...},
            "trades": [...],
            "notes": [...]
          }
        """
        lines: List[str] = []
        lines.append(f"# PairBot Daily Report — {summary.get('date','')}\n")

        lines.append("## 개요\n")
        lines.append(f"- 생성 시각: {datetime.now().astimezone().isoformat(timespec='seconds')}\n")
        lines.append(f"- 마켓 오픈: {summary.get('market_open')}\n")

        lines.append("\n## 유니버스\n")
        uni: Dict[str, Dict[str, str]] = summary.get("universe") or {}
        for g, u in uni.items():
            lines.append(f"- {g}: +2={u.get('lev')} / -2={u.get('inv')}")

        lines.append("\n\n## 그룹별 결과\n")
        groups: Dict[str, Any] = summary.get("groups") or {}
        for g, info in groups.items():
            lines.append(f"### {g}\n")
            lines.append(f"- decision: {info.get('decision')}\n")
            if info.get("entered"):
                lines.append(f"- entered: ✅\n")
            else:
                lines.append(f"- entered: ❌\n")
            if info.get("exit"):
                lines.append(f"- exit: {info.get('exit')}\n")
            lines.append("")

        lines.append("\n## 거래(가정 체결가 기반)\n")
        if not self._trades:
            lines.append("- (거래 없음)\n")
        else:
            lines.append("| 시간 | 그룹 | 종목 | 방향 | 수량 | 가정체결가 | 사유 | 주문번호 | 결과 |\n")
            lines.append("|---|---|---|---:|---:|---:|---|---|---|\n")
            for t in self._trades:
                ok = "OK" if t.ok else "FAIL"
                lines.append(
                    f"| {t.ts} | {t.group} | {t.symbol} | {t.side} | {t.qty} | {t.px_assumed:.2f} | {t.reason} | {t.order_no or ''} | {ok} |\n"
                )

        notes = summary.get("notes") or []
        if notes:
            lines.append("\n## Notes\n")
            for n in notes:
                lines.append(f"- {n}")

        self.report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def finalize(self, *, summary: Dict[str, Any]) -> None:
        self._write_trades_csv()
        self.finalize_markdown(summary=summary)
