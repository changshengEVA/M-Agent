from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _format_utc_offset(offset: Optional[timedelta]) -> Optional[str]:
    if offset is None:
        return None

    total_seconds = int(offset.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"{sign}{hours:02d}:{minutes:02d}"


def _timezone_label(tz: tzinfo) -> str:
    key = getattr(tz, "key", None)
    if isinstance(key, str) and key.strip():
        return key.strip()

    zone = getattr(tz, "zone", None)
    if isinstance(zone, str) and zone.strip():
        return zone.strip()

    tz_name = datetime.now(tz).tzname()
    if isinstance(tz_name, str) and tz_name.strip():
        return tz_name.strip()

    return "UTC"


def resolve_timezone(timezone_name: Optional[str] = None) -> Tuple[tzinfo, str, Optional[str]]:
    requested_timezone_name = str(timezone_name or "").strip() or None
    if requested_timezone_name:
        normalized_name = "UTC" if requested_timezone_name.upper() == "UTC" else requested_timezone_name
        if normalized_name == "UTC":
            return timezone.utc, "UTC", requested_timezone_name
        try:
            resolved_tz = ZoneInfo(normalized_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                "Unknown timezone. Use an IANA timezone name such as "
                "`Asia/Shanghai` or `America/New_York`."
            ) from exc
        return resolved_tz, normalized_name, requested_timezone_name

    local_now = datetime.now().astimezone()
    local_tz = local_now.tzinfo or timezone.utc
    return local_tz, _timezone_label(local_tz), None


def _relative_day_payload(dt: datetime) -> Dict[str, Any]:
    return {
        "date": dt.strftime("%Y-%m-%d"),
        "weekday": dt.strftime("%A"),
        "iso_weekday": dt.isoweekday(),
    }


def get_current_time_context(timezone_name: Optional[str] = None) -> Dict[str, Any]:
    requested_timezone_name = str(timezone_name or "").strip() or None
    try:
        resolved_tz, resolved_timezone_name, normalized_request = resolve_timezone(requested_timezone_name)
    except ValueError as exc:
        return {
            "ok": False,
            "requested_timezone_name": requested_timezone_name,
            "error": str(exc),
        }

    now = datetime.now(resolved_tz)
    return {
        "ok": True,
        "timezone_name": resolved_timezone_name,
        "requested_timezone_name": normalized_request,
        "timezone_source": "requested" if normalized_request else "system_local",
        "local_datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "local_date": now.strftime("%Y-%m-%d"),
        "local_time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
        "iso_weekday": now.isoweekday(),
        "iso_datetime": now.isoformat(),
        "utc_iso_datetime": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "utc_offset": _format_utc_offset(now.utcoffset()),
        "unix_timestamp": int(now.timestamp()),
        "relative_dates": {
            "yesterday": _relative_day_payload(now - timedelta(days=1)),
            "today": _relative_day_payload(now),
            "tomorrow": _relative_day_payload(now + timedelta(days=1)),
        },
    }
