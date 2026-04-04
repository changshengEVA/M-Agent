from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Optional

from m_agent.utils.time_utils import resolve_timezone


ABSOLUTE_DATE_RE = re.compile(
    r"(?P<year>\d{4})\s*[-/年]\s*(?P<month>\d{1,2})\s*[-/月]\s*(?P<day>\d{1,2})(?:\s*日)?"
)
ABSOLUTE_MONTH_DAY_RE = re.compile(r"(?P<month>\d{1,2})\s*[-/月]\s*(?P<day>\d{1,2})(?:\s*日)?")
RELATIVE_DAY_PATTERNS = (
    ("day after tomorrow", 2),
    ("today", 0),
    ("tomorrow", 1),
    ("大后天", 3),
    ("后天", 2),
    ("明天", 1),
    ("明日", 1),
    ("今天", 0),
    ("今日", 0),
)
TIME_RE = re.compile(
    r"(?P<ampm>凌晨|早上|上午|中午|下午|傍晚|晚上|晚间|am|pm|AM|PM)?"
    r"\s*(?P<hour>\d{1,2})"
    r"(?:\s*(?:[:点时])\s*(?P<minute>\d{1,2}|半|一刻|三刻)?)?"
)


@dataclass
class ParsedDateTime:
    due_local: Optional[datetime]
    timezone_name: str
    matched_text: str = ""
    has_date: bool = False
    has_time: bool = False
    assumed_date: bool = False
    assumptions: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_payload(self) -> Dict[str, Any]:
        payload = {
            "timezone_name": self.timezone_name,
            "matched_text": self.matched_text,
            "has_date": self.has_date,
            "has_time": self.has_time,
            "assumed_date": self.assumed_date,
            "assumptions": dict(self.assumptions or {}),
            "error": self.error,
        }
        if self.due_local is not None:
            payload["local_iso_datetime"] = self.due_local.isoformat()
            payload["local_display"] = self.due_local.strftime("%Y-%m-%d %H:%M")
            payload["due_at_utc"] = self.due_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return payload


def _resolve_now(timezone_name: Optional[str], now_context: Optional[Dict[str, Any]]) -> tuple[datetime, str]:
    tz, resolved_timezone_name, _ = resolve_timezone(timezone_name)
    if isinstance(now_context, dict):
        iso_datetime = str(now_context.get("iso_datetime", "") or "").strip()
        if iso_datetime:
            try:
                parsed = datetime.fromisoformat(iso_datetime.replace("Z", "+00:00"))
                return parsed.astimezone(tz), resolved_timezone_name
            except Exception:
                pass
    return datetime.now(tz), resolved_timezone_name


def _adjust_year_for_month_day(month: int, day: int, now_local: datetime) -> date:
    candidate = date(year=now_local.year, month=month, day=day)
    if candidate < now_local.date() - timedelta(days=1):
        return date(year=now_local.year + 1, month=month, day=day)
    return candidate


def _extract_date(text: str, now_local: datetime) -> tuple[Optional[date], str, bool]:
    safe = str(text or "")
    absolute = ABSOLUTE_DATE_RE.search(safe)
    if absolute is not None:
        year = int(absolute.group("year"))
        month = int(absolute.group("month"))
        day = int(absolute.group("day"))
        return date(year=year, month=month, day=day), absolute.group(0), True

    month_day = ABSOLUTE_MONTH_DAY_RE.search(safe)
    if month_day is not None:
        month = int(month_day.group("month"))
        day = int(month_day.group("day"))
        return _adjust_year_for_month_day(month, day, now_local), month_day.group(0), True

    lowered = safe.lower()
    for pattern, day_offset in RELATIVE_DAY_PATTERNS:
        if pattern in lowered or pattern in safe:
            return now_local.date() + timedelta(days=day_offset), pattern, True

    return None, "", False


def _parse_minute(raw_minute: Optional[str]) -> int:
    value = str(raw_minute or "").strip()
    if not value:
        return 0
    if value == "半":
        return 30
    if value == "一刻":
        return 15
    if value == "三刻":
        return 45
    return max(0, min(59, int(value)))


def _adjust_hour(hour: int, ampm: str) -> int:
    marker = str(ampm or "").strip().lower()
    if marker in {"pm", "下午", "晚上", "晚间", "傍晚"} and hour < 12:
        return hour + 12
    if marker in {"am", "凌晨", "早上", "上午"} and hour == 12:
        return 0
    if marker == "中午":
        if hour == 0:
            return 12
        if 1 <= hour <= 10:
            return hour + 12
    return hour


def _extract_time(text: str) -> tuple[Optional[time], str, bool]:
    safe = str(text or "")
    for match in TIME_RE.finditer(safe):
        start = match.start()
        if start > 0 and safe[start - 1].isdigit():
            continue
        hour = int(match.group("hour"))
        minute = _parse_minute(match.group("minute"))
        hour = _adjust_hour(hour, match.group("ampm") or "")
        if not (0 <= hour <= 23):
            continue
        return time(hour=hour, minute=minute), match.group(0), True
    return None, "", False


def parse_due_datetime(
    text: str,
    *,
    timezone_name: Optional[str] = None,
    now_context: Optional[Dict[str, Any]] = None,
    default_date: Optional[date] = None,
) -> ParsedDateTime:
    safe = str(text or "").strip()
    now_local, resolved_timezone_name = _resolve_now(timezone_name, now_context)
    parsed_date, matched_date_text, has_date = _extract_date(safe, now_local)
    if parsed_date is None:
        parsed_date = default_date
        has_date = parsed_date is not None
    remainder = safe
    if matched_date_text:
        remainder = remainder.replace(matched_date_text, " ", 1)
    parsed_time, matched_time_text, has_time = _extract_time(remainder)
    if parsed_time is None and not matched_date_text:
        parsed_time, matched_time_text, has_time = _extract_time(safe)

    if parsed_time is None:
        return ParsedDateTime(
            due_local=None,
            timezone_name=resolved_timezone_name,
            matched_text=" ".join(x for x in (matched_date_text, matched_time_text) if x).strip(),
            has_date=has_date,
            has_time=False,
            error="missing_time",
        )

    assumed_date = False
    assumptions: Dict[str, Any] = {}
    if parsed_date is None:
        candidate = datetime.combine(now_local.date(), parsed_time, tzinfo=now_local.tzinfo)
        if candidate <= now_local:
            candidate = candidate + timedelta(days=1)
            assumptions["rolled_to_next_day"] = True
        assumed_date = True
        parsed_date = candidate.date()

    due_local = datetime.combine(parsed_date, parsed_time, tzinfo=now_local.tzinfo)
    return ParsedDateTime(
        due_local=due_local,
        timezone_name=resolved_timezone_name,
        matched_text=" ".join(x for x in (matched_date_text, matched_time_text) if x).strip(),
        has_date=has_date,
        has_time=has_time,
        assumed_date=assumed_date,
        assumptions=assumptions,
    )


def parse_day_window(
    text: str,
    *,
    timezone_name: Optional[str] = None,
    now_context: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    safe = str(text or "").strip()
    now_local, resolved_timezone_name = _resolve_now(timezone_name, now_context)
    parsed_date, matched_date_text, has_date = _extract_date(safe, now_local)
    if not has_date or parsed_date is None:
        return None
    start_local = datetime.combine(parsed_date, time.min, tzinfo=now_local.tzinfo)
    end_local = datetime.combine(parsed_date, time.max.replace(microsecond=0), tzinfo=now_local.tzinfo)
    return {
        "timezone_name": resolved_timezone_name,
        "matched_text": matched_date_text,
        "start_local": start_local,
        "end_local": end_local,
        "start_utc": start_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "end_utc": end_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
