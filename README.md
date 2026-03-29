# KIS Adaptive VB Bot

`kis_adaptive_vb_bot`은 한국투자증권 KIS OpenAPI를 이용해 국내 ETF 4종을 대상으로 적응형 변동성 돌파 전략을 자동으로 실행하는 프로젝트다. 단순히 주문만 보내는 스크립트가 아니라, 장 시작 전 준비, 진입 판단, 리스크 제한, 장중 추적, 장 마감 청산, 일일 리포트 생성까지 하나의 운영 흐름으로 묶어 둔 것이 특징이다.

## 프로젝트 개요

- 전략 이름: Adaptive Volatility Breakout
- 시장: 한국 ETF
- 유니버스:
  - KOSPI 그룹: `122630` KODEX 레버리지, `252670` KODEX 200선물인버스2X
  - KOSDAQ 그룹: `233740` KODEX 코스닥150레버리지, `251340` KODEX 코스닥150선물인버스
- 기본 원칙:
  - 같은 그룹 안에서 레버리지와 인버스 중 한 방향만 선택
  - ATR20과 Noise20 기반으로 목표가를 계산
  - 장중 돌파 시 1회 진입
  - 트레일링 스탑과 15:15 강제 청산으로 오버나잇을 금지

## 전략 요약

이 봇은 오전 9시 전후에 각 그룹의 방향을 먼저 판단한 뒤, 승인된 방향의 종목만 감시한다. 목표가를 돌파하면 진입하고, 이후에는 샹들리에 방식의 트레일링 스탑으로 이익을 보호한다. 장이 끝날 때까지 포지션이 남아 있으면 15시 15분에 무조건 전량 청산한다.

핵심 판단 로직은 다음과 같다.

- 방향 선택: 09:00 시가 기준으로 레버리지와 인버스 중 하나만 MA5 위에 있을 때만 승인
- 진입 시점: 09:00부터 13:00 사이 목표가 돌파 시
- 수량 계산: 총자본 1% 리스크 기준과 종목별 명목 비중 한도를 함께 적용
- 청산: 트레일링 스탑 또는 15:15 타임아웃 청산

## 안전장치

실제 주문은 아래 조건이 모두 맞을 때만 전송된다.

- `.env`에서 `KIS_LIVE_ENABLED=1`
- `.env`에서 `KIS_LIVE_CONFIRM=YES`
- `.env`에서 `KIS_KILL_SWITCH=0`
- 프로젝트 루트에 `STOP_TRADING.flag`가 없어야 함

즉, 실행 중이라고 해서 곧바로 실거래가 나가는 구조가 아니다. 실전 환경에서는 먼저 `STOP_TRADING.flag`를 켠 상태로 로그와 리포트만 점검한 뒤, 최종 확인 후 해제하는 흐름을 권장한다.

## 빠른 시작

```bash
cd ~/Desktop/kis_adaptive_vb_bot
bash scripts/bootstrap.sh
cp .env.example .env
nano .env
bash scripts/run_bot.sh
```

중지는 아래와 같이 할 수 있다.

```bash
bash scripts/stop_bot.sh
```

## 주요 환경 변수

가장 중요한 값만 먼저 정리하면 아래와 같다.

```env
KIS_APP_KEY=...
KIS_APP_SECRET=...
KIS_ACCOUNT_NO=12345678
KIS_ACCOUNT_PRODUCT_CODE=01

KIS_LIVE_ENABLED=0
KIS_LIVE_CONFIRM=NO
KIS_KILL_SWITCH=1

CAPITAL_KRW=10000000
PAIRBOT_MAX_POSITION_NOTIONAL_PCT=0.5
PRICE_POLL_INTERVAL_SEC=1.0
LOG_LEVEL=INFO
```

실전 웹소켓을 쓰려면 아래 값도 함께 설정한다.

```env
KIS_WS_URL=ws://ops.koreainvestment.com:21000
```

## 실행 결과 확인

운영 중에 가장 자주 보게 되는 위치는 아래 세 군데다.

- `logs/`
  - 날짜별 실행 로그와 `nohup` 로그
- `reports/YYYY-MM-DD/`
  - `report.md`: 사람이 읽기 쉬운 일일 요약
  - `events.jsonl`: 구조화된 이벤트 타임라인
  - `trades.csv`: 주문/체결 추정 이력
  - `run_meta.json`: 실행 시점 설정과 유니버스 정보
- 루트 스크립트
  - `scripts/run_bot.sh`, `scripts/stop_bot.sh`, `scripts/ws_smoke_test.sh`

## 디렉터리 안내

- [main.py](/home/zenith/Desktop/kis_adaptive_vb_bot/main.py): 장중 실행을 조율하는 메인 오케스트레이터
- [data_handler.py](/home/zenith/Desktop/kis_adaptive_vb_bot/data_handler.py): 일봉, MA, ATR, Noise 계산
- [strategy_engine.py](/home/zenith/Desktop/kis_adaptive_vb_bot/strategy_engine.py): 방향 선택과 목표가 계산
- [risk_manager.py](/home/zenith/Desktop/kis_adaptive_vb_bot/risk_manager.py): 수량 계산과 트레일링 스탑
- [execution_handler.py](/home/zenith/Desktop/kis_adaptive_vb_bot/execution_handler.py): 주문 전송과 시세 조회
- [reporting.py](/home/zenith/Desktop/kis_adaptive_vb_bot/reporting.py): 일일 리포트 생성
- [사용설명서.md](/home/zenith/Desktop/kis_adaptive_vb_bot/사용설명서.md): 설치, 운영, 문제 해결 중심 안내서

## 운영 타임라인

- 08:50: 일봉 및 지표 갱신
- 09:01: 그룹별 방향 결정
- 09:00~13:00: 목표가 돌파 감시 및 진입
- 진입 후: 트레일링 스탑 감시
- 15:15: 전량 강제 청산
- 마감 후: 리포트 생성 및 선택적 알림 전송

## 리포트와 해석 주의

`trades.csv`에는 주문 API 응답 한계 때문에 실제 체결가가 아니라 주문 직전 관측 가격이 들어갈 수 있다. 따라서 손익 열은 운영 참고용 추정치로 보고, 최종 정산은 증권사 체결 내역과 반드시 대조해야 한다.

## 공개 저장소 운영 원칙

- `.env`, 토큰 캐시, 로그, PID 파일은 Git에 올리지 않는다.
- 샘플 리포트는 문서 예시로 포함할 수 있지만, 실계좌 식별 정보는 제거한 상태만 허용한다.
- 실전 사용 전에는 모의 환경 또는 소액 환경에서 충분히 검증하는 것이 좋다.
