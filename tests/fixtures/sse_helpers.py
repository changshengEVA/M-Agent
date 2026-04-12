from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List


def parse_sse_events(lines: Iterable[str]) -> List[Dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: dict[str, Any] = {}

    for raw_line in lines:
        line = str(raw_line or "")
        if not line.strip():
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith(":"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.lstrip()
        if key == "id":
            current["id"] = value
        elif key == "event":
            current["event"] = value
        elif key == "data":
            current["data_raw"] = value
            try:
                current["data"] = json.loads(value)
            except json.JSONDecodeError:
                current["data"] = value
    if current:
        events.append(current)
    return events
