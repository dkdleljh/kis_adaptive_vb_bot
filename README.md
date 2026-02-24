# 페어봇 (KIS Adaptive Volatility Breakout KR ETF Pair)

> ⚠️ 실전용 자동매매 봇입니다. 충분한 모의/소액 검증 후 사용하세요.
>
> - 기본 안전장치(kis_orb_vwap_bot과 동일):
>   - `KIS_LIVE_ENABLED=1` AND `KIS_LIVE_CONFIRM=YES` AND `KIS_KILL_SWITCH=0`
>   - 그리고 `STOP_TRADING.flag`가 **없어야** 주문이 나갑니다.
> - 이 프로젝트는 KIS OpenAPI 호출/웹소켓 포맷이 계정/상품/환경에 따라 달라질 수 있어, **TR_ID/필드명은 반드시 최종 점검**이 필요합니다.

## 1) 폴더 구조

- `data_handler.py` : 토큰/일봉 조회 + MA5/ATR20/Noise20 계산
- `strategy_engine.py` : 모순 필터 + 동적 K + 목표가 산출
- `risk_manager.py` : 변동성 타겟팅 수량 + 샹들리에 트레일링 스탑
- `execution_handler.py` : REST 주문 + 웹소켓(가능하면) 현재가 스트림
- `main.py` : 08:50 갱신 → 09:00~13:00 진입 → 15:15 전량청산

## 2) 실행 준비

### 2.1 가상환경

```bash
cd ~/Desktop/kis_adaptive_vb_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2.2 환경변수(.env)

`.env.example`을 복사해 `.env`를 만들고 값을 채우세요.

```bash
cp .env.example .env
```

- 실전 안전장치: 실주문을 허용하려면 `KIS_LIVE_ENABLED=1`, `KIS_LIVE_CONFIRM=YES`, `KIS_KILL_SWITCH=0`
- 실주문을 막고 싶으면 `touch STOP_TRADING.flag`

## 3) 실행

### (권장) KIS 봇처럼 스크립트로 바로 실행

```bash
bash scripts/run_bot.sh
```

- 로그: `logs/nohup_adaptive_vb.log`
- 중지:

```bash
bash scripts/stop_bot.sh
```

### 직접 실행

```bash
source venv/bin/activate
python main.py
```

## 4) 유니버스(고정)

- KOSPI pair
  - KODEX 레버리지(122630)
  - KODEX 200선물인버스2X(252670)
- KOSDAQ pair
  - KODEX 코스닥150레버리지(233740)
  - KODEX 코스닥150선물인버스(251340)

## 5) 운영 메모

- 본 구현은 **웹소켓이 불안정/미구성**일 때를 대비해, 웹소켓 연결 실패 시 **REST 현재가 폴링**으로 자동 폴백합니다.
- WebSocket approval key는 kis 봇처럼 `oauth2/Approval`로 자동 발급/레이트리밋 보호를 적용합니다.
- REST 폴링은 초당 호출 제한(EGW00201)을 맞추기 위해 기본 `PRICE_POLL_INTERVAL_SEC=1.0`로 둡니다.
