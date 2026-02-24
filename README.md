# 페어봇 (PairBot) — KIS Adaptive Volatility Breakout (KR ETF Pair)

KIS OpenAPI로 **KODEX ETF 4종목**을 대상으로
"적응형 변동성 돌파(Adaptive Volatility Breakout)"를 실행하는 자동매매 봇입니다.

- 유니버스(고정)
  - KOSPI: 122630(레버) vs 252670(인버스2X)
  - KOSDAQ: 233740(코스닥150레버) vs 251340(코스닥150인버스)

> ⚠️ 면책
> - 투자 조언이 아닙니다. 손실 책임은 사용자에게 있습니다.
> - 실거래 전 모의/소액으로 충분히 검증하세요.

---

## 문서
- **사용설명서(초보자용)**: `사용설명서.md`

---

## 빠른 시작(3분)

```bash
cd ~/Desktop/kis_adaptive_vb_bot
bash scripts/bootstrap.sh
cp .env.example .env
nano .env
bash scripts/run_bot.sh
```

로그:
```bash
tail -n 200 logs/nohup_adaptive_vb.log
```

중지:
```bash
bash scripts/stop_bot.sh
```

---

## 실전 주문 안전장치(중요)

주문이 나가려면 아래가 **모두** 필요합니다.

- `.env`:
  - `KIS_LIVE_ENABLED=1`
  - `KIS_LIVE_CONFIRM=YES`
  - `KIS_KILL_SWITCH=0`
- 그리고 `STOP_TRADING.flag` 파일이 **없어야** 합니다.

주문 차단(권장):
```bash
touch STOP_TRADING.flag
```

---

## 운영 타임테이블(KST)

- 08:50 데이터 갱신(일봉/지표)
- 09:00 모순 필터 + 목표가 산출
- 09:00~13:00 목표가 돌파 1회 진입
- 진입 후 트레일링 스탑 감시
- 15:15 전량 강제청산

---

## WebSocket(선택)

실전 WS 도메인(문서 H0STCNT0 기준):
- `ws://ops.koreainvestment.com:21000`

환경변수:
```env
KIS_WS_URL=ws://ops.koreainvestment.com:21000
```

점검:
```bash
bash scripts/ws_smoke_test.sh
```

---

## 폴더 구조

- `main.py` : 스케줄/오케스트레이션
- `data_handler.py` : 일봉/시가/지표 계산
- `strategy_engine.py` : 모순 필터 + 동적 K + 목표가
- `risk_manager.py` : 1% 리스크 수량 + 트레일링 스탑
- `execution_handler.py` : 주문/시세(WS→REST 폴백)
- `kis_auth.py` : tokenP/Approval/hashkey + 레이트리밋/backoff
- `kis_ws_marketdata.py` : WS 메시지 파싱(체결가)
- `utils_holiday.py` : 주말/휴장일 스킵
- `scripts/` : bootstrap/run/stop/ws_smoke
