from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo
import holidays

from app.hospital.models import OpenHours

SEOUL_TZ = ZoneInfo("Asia/Seoul")
KR_HOLIDAYS = holidays.KR()

DAYS_ORDER: tuple[str, ...] = (
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
    "SUNDAY",
)


def ensure_seoul_now(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(SEOUL_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=SEOUL_TZ)
    return now.astimezone(SEOUL_TZ)


def is_korean_holiday(target_date: date) -> bool:
    return target_date in KR_HOLIDAYS


def get_qt_code(target_date: date) -> int:
    if is_korean_holiday(target_date):
        return 8
    # weekday(): 월=0 ~ 일=6 -> QT: 1~7
    return target_date.weekday() + 1


def _parse_hhmm_minutes(raw_val: Any) -> Optional[int]:
    if raw_val is None:
        return None
    s = str(raw_val).strip()
    if not s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 3:
        digits = "0" + digits
    if len(digits) != 4:
        return None
    hh = int(digits[:2])
    mm = int(digits[2:])
    if hh == 24 and mm == 0:
        return 1440
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return hh * 60 + mm
    return None


def format_hhmm_to_colon(raw_val: Any) -> Optional[str]:
    if raw_val is None:
        return None
    s = str(raw_val).strip()
    if not s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 3:
        digits = "0" + digits
    if len(digits) != 4:
        return None
    hh = int(digits[:2])
    mm = int(digits[2:])
    if hh == 24 and mm == 0:
        return "24:00"
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return f"{hh:02d}:{mm:02d}"
    return None


def _is_within_slot_for_day(row: dict[str, Any], field_idx: int, current_minutes: int) -> bool:
    start_raw = row.get(f"dutyTime{field_idx}s")
    end_raw = row.get(f"dutyTime{field_idx}c")
    start_min = _parse_hhmm_minutes(start_raw)
    end_min = _parse_hhmm_minutes(end_raw)
    if start_min is None or end_min is None:
        return False

    # 자정을 넘기지 않는 일반 운영(예: 0900~1800, 0900~2400)
    if end_min > start_min:
        return start_min <= current_minutes < end_min

    # 자정을 넘기는 경우(예: 1800~0200 -> end_min <= start_min)
    if end_min <= start_min:
        return current_minutes >= start_min or current_minutes < end_min

    return False


def _is_overnight_spill_from_yesterday(row: dict[str, Any], prev_field_idx: int, current_minutes: int) -> bool:
    start_raw = row.get(f"dutyTime{prev_field_idx}s")
    end_raw = row.get(f"dutyTime{prev_field_idx}c")
    start_min = _parse_hhmm_minutes(start_raw)
    end_min = _parse_hhmm_minutes(end_raw)
    if start_min is None or end_min is None:
        return False

    # 전날 시작 시각이 자정을 넘겨 오늘 새벽(end_min)까지 이어지는 경우
    if end_min <= start_min and end_min > 0:
        return current_minutes < end_min
    return False


def is_open_now(row: dict[str, Any], now: Optional[datetime] = None, day: Optional[date] = None) -> bool:
    dt = ensure_seoul_now(now)
    target_date = day if day is not None else dt.date()
    current_minutes = dt.hour * 60 + dt.minute

    today_field_idx = get_qt_code(target_date)
    if _is_within_slot_for_day(row, today_field_idx, current_minutes):
        return True

    prev_date = target_date - timedelta(days=1)
    prev_field_idx = get_qt_code(prev_date)
    if _is_overnight_spill_from_yesterday(row, prev_field_idx, current_minutes):
        return True

    return False


def build_open_hours(row: dict[str, Any]) -> list[OpenHours]:
    entries: list[OpenHours] = []
    has_any_slot = False

    for idx, day_name in enumerate(DAYS_ORDER, start=1):
        open_fmt = format_hhmm_to_colon(row.get(f"dutyTime{idx}s"))
        close_fmt = format_hhmm_to_colon(row.get(f"dutyTime{idx}c"))
        if open_fmt is not None and close_fmt is not None:
            has_any_slot = True
            entries.append(
                OpenHours(
                    day_of_week=day_name,
                    open_time=open_fmt,
                    close_time=close_fmt,
                    is_closed=False,
                )
            )
        else:
            entries.append(
                OpenHours(
                    day_of_week=day_name,
                    open_time=None,
                    close_time=None,
                    is_closed=True,
                )
            )

    if not has_any_slot:
        return []
    return entries
