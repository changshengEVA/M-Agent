#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Load LongMemEval JSON (haystack_sessions) into internal dialogue format.

Each haystack session becomes one dialogue; ``meta.sample_id`` is the benchmark
``question_id`` so ``filter_dialogues_by_conv_ids`` can keep one record per run.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from m_agent.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

_LONGMEMEVAL_DATE_RE = re.compile(
    r"^(?P<ymd>\d{4}/\d{2}/\d{2})\s*\([^)]+\)\s*(?P<hm>\d{1,2}:\d{2})$"
)


def parse_longmemeval_datetime(s: str) -> datetime:
    raw = (s or "").strip()
    if not raw:
        return datetime.now()
    m = _LONGMEMEVAL_DATE_RE.match(raw)
    if m:
        try:
            return datetime.strptime(f"{m.group('ymd')} {m.group('hm')}", "%Y/%m/%d %H:%M")
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw.replace(" ", "T"))
    except ValueError:
        logger.warning("LongMemEval date parse failed: %s — using now", raw)
        return datetime.now()


def _role_to_speaker(role: str) -> str:
    r = (role or "").strip().lower()
    if r == "user":
        return "User"
    if r == "assistant":
        return "Assistant"
    return role or "unknown"


def _extract_dialogues_from_record(
    record: Dict[str, Any],
    source_name: str,
) -> List[Dict[str, Any]]:
    question_id = str(record.get("question_id", "") or "").strip()
    if not question_id:
        return []

    sessions = record.get("haystack_sessions") or []
    dates = record.get("haystack_dates") or []
    session_ids = record.get("haystack_session_ids") or []

    if not isinstance(sessions, list) or not sessions:
        return []

    dialogues: List[Dict[str, Any]] = []
    for idx, session in enumerate(sessions):
        if not isinstance(session, list):
            continue
        session_start = parse_longmemeval_datetime(
            str(dates[idx]) if idx < len(dates) and dates else ""
        )
        turns: List[Dict[str, Any]] = []
        for turn_idx, turn in enumerate(session):
            if not isinstance(turn, dict):
                continue
            role = _role_to_speaker(str(turn.get("role", "")))
            text = turn.get("content", "")
            if not isinstance(text, str):
                text = str(text)
            turn_dt = session_start + timedelta(seconds=turn_idx * 2)
            turns.append(
                {
                    "turn_id": turn_idx,
                    "speaker": role,
                    "text": text,
                    "timestamp": turn_dt.isoformat(),
                }
            )
        if not turns:
            continue

        sid = str(session_ids[idx]) if idx < len(session_ids) and session_ids else f"session_{idx}"
        dialogue_id = "_".join(
            [
                "dlg",
                _sanitize(source_name),
                _sanitize(question_id),
                _sanitize(sid),
            ]
        )
        participants = sorted({t.get("speaker", "unknown") for t in turns})
        dialogue = {
            "dialogue_id": dialogue_id,
            "user_id": "User",
            "participants": participants,
            "meta": {
                "start_time": turns[0]["timestamp"],
                "end_time": turns[-1]["timestamp"],
                "language": "en",
                "platform": "longmemeval",
                "version": 1,
                "sample_id": question_id,
                "session_index": idx,
            },
            "turns": turns,
        }
        dialogues.append(dialogue)
    return dialogues


def _sanitize(s: str) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "").strip())
    return out or "x"


def load_longmemeval_dialogues(
    file_path: str | None = None,
    include_question_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    if file_path is None:
        file_path = str(PROJECT_ROOT / "data" / "LongMemEval" / "data" / "longmemeval_s_cleaned.json")

    if not os.path.exists(file_path):
        logger.error("LongMemEval file not found: %s", file_path)
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        logger.error("LongMemEval JSON must be a list")
        return []

    base_name = os.path.splitext(os.path.basename(file_path))[0] or "longmemeval"
    include_set = {str(x).strip() for x in (include_question_ids or []) if str(x).strip()}
    if not include_set:
        logger.error(
            "LongMemEval loader requires non-empty include_question_ids "
            "(one question_id per run via memory_pre include_conv_ids)."
        )
        return []

    out: List[Dict[str, Any]] = []
    for record in data:
        if not isinstance(record, dict):
            continue
        qid = str(record.get("question_id", "") or "").strip()
        if qid not in include_set:
            continue
        out.extend(_extract_dialogues_from_record(record, base_name))

    logger.info(
        "LongMemEval: loaded %d dialogue(s) from %s (%d record(s) in file)",
        len(out),
        file_path,
        len(data),
    )
    return out
