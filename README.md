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

## 매매 전략(요약)

페어봇은 KOSPI/KOSDAQ 각각에 대해 **레버리지 vs 인버스 중 하나만 선택해 매수**하는
"적응형 변동성 돌파(Adaptive Volatility Breakout)" 전략입니다.

### 1) 방향 선택(모순 필터)
- 09:00 시가 기준으로, 같은 그룹 내 레버/인버스 중
  - **오직 하나만** MA5 위에 있을 때만 거래(방향 확정)
  - 둘 다 위/둘 다 아래면 그 그룹은 당일 스킵

### 2) 동적 K와 목표가(Target)
- 지표:
  - ATR20 (20일 평균 변동성)
  - Noise20avg = 평균( 1 - |C-O|/(H-L) )
- 동적 K:
  - **K = Noise20avg × (전일 고저폭 / ATR20)**
- 목표가:
  - **Target = 시가 + (전일 고저폭 × K)**

### 3) 진입/청산 규칙
- **진입(09:00~13:00)**: 현재가 ≥ Target이면 시장가 매수(그룹당 1회)
- **수량 제한**: `min(리스크기반수량, 포지션명목한도수량)`
  - 리스크기반수량 = `floor((총자본×1%) / ATR20)`
  - 포지션명목한도수량 = `floor((총자본×PAIRBOT_MAX_POSITION_NOTIONAL_PCT) / 진입가)`
- **트레일링 스탑**(샹들리에): 최고가 - ATR20×1.5
- **강제 청산(15:15)**: 오버나잇 금지, 전량 시장가 매도

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
- 승인 종목이 0개인 날도 마감 리포트(`reports/YYYY-MM-DD/`)는 항상 생성

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

## 로그 / 리포트 / 보고서 (운영 친화)

페어봇은 "돌아갔는지"를 **로그만 보고 추측**하지 않도록, 실행 결과를 별도 리포트로 남깁니다.

### 1) 실행 로그
- 콘솔(stdout) + 파일 동시 기록
- 파일 경로: `logs/pairbot_YYYY-MM-DD.log`

### 2) 일일 리포트(권장 확인)
- 경로: `reports/YYYY-MM-DD/`
  - `report.md` : 사람이 읽는 요약 보고서(통계 + 그룹별 결과)
  - `events.jsonl` : 결정/진입/청산/에러 타임라인(구조화)
  - `trades.csv` : 거래 요약(가정 체결가 기반, SELL에는 PnL 추정치 포함)
  - `run_meta.json` : 유니버스/설정 메타
  - `notify.txt` : 메시지 전송용 짧은 요약(생성됨)

> 참고: KIS 주문 API 응답에 체결가가 항상 포함되진 않습니다.
> `trades.csv`의 가격은 **주문 직전 관측 가격(px) 기준 가정값**일 수 있으니,
> 정확한 정산은 증권사 체결내역/체결조회와 대조하세요.

### 3) (선택) 장 마감 후 자동 알림(OpenClaw systemEvent)

OpenClaw가 동작하는 환경이라면, 봇 종료 시 **모델 호출 없이(systemEvent)** 텔레그램/채널로 요약 리포트를 푸시할 수 있습니다.

환경변수:
```env
REPORT_NOTIFY_OPENCLAW=1
REPORT_NOTIFY_MAXCHARS=3500
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
