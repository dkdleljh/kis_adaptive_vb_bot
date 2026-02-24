from __future__ import annotations

"""utils_holiday.py

kis_orb_vwap_bot과 동일한 방식으로 한국 증시 휴장일(주말/공휴일)을 판정합니다.

- holidays 라이브러리로 KR 공휴일(음력 포함) 계산
- 근로자의 날(5/1), 연말(12/31) 추가 휴장 처리

주의: 특별 휴장(재난/임시공휴일/수능 등)까지 100% 커버하진 못합니다.
운영 중 특이 케이스가 있으면 별도 예외 리스트를 추가하는 방식으로 확장하세요.
"""

from datetime import date

import holidays

kr_holidays = holidays.KR()  # type: ignore[attr-defined]


def is_market_open(target_date: date) -> bool:
    # 주말
    if target_date.weekday() >= 5:
        return False

    # 법정 공휴일(+설/추석 등 음력 포함)
    if target_date in kr_holidays:
        return False

    # 근로자의 날
    if target_date.month == 5 and target_date.day == 1:
        return False

    # 연말 휴장
    if target_date.month == 12 and target_date.day == 31:
        return False

    return True


def get_holiday_name(target_date: date) -> str | None:
    if target_date.weekday() >= 5:
        return "Weekend"

    name = kr_holidays.get(target_date)
    if name:
        return str(name)

    if target_date.month == 5 and target_date.day == 1:
        return "Labor Day (Stock Market Closed)"

    if target_date.month == 12 and target_date.day == 31:
        return "Year-End Closing Day"

    return None
