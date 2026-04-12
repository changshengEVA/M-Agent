from __future__ import annotations

from typing import Any, Dict


def run_payload(*, message: str, thread_id: str = "demo-thread", config: str | None = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "thread_id": thread_id,
        "message": message,
    }
    if config is not None:
        payload["config"] = config
    return payload


def memory_mode_payload(*, mode: str, discard_pending: bool = False) -> Dict[str, Any]:
    return {
        "mode": mode,
        "discard_pending": bool(discard_pending),
    }


def memory_flush_payload(*, reason: str = "manual_test") -> Dict[str, Any]:
    return {"reason": reason}
