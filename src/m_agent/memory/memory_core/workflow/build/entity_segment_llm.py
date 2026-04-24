#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM I/O helpers for segment-based entity profile build."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from m_agent.prompt_utils import load_resolved_prompt_config

logger = logging.getLogger(__name__)


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Parse first JSON object from model output (handles fences)."""
    raw = str(text or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(raw[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                chunk = raw[start : i + 1]
                try:
                    data = json.loads(chunk)
                    return data if isinstance(data, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def load_entity_segment_prompts(
    *,
    runtime_prompt_config_path: str | Path,
    prompt_language: str,
) -> Dict[str, str]:
    path = Path(runtime_prompt_config_path).resolve()
    cfg = load_resolved_prompt_config(path, language=prompt_language)
    seg = cfg.get("entity_segment")
    if not isinstance(seg, dict):
        raise ValueError(f"`entity_segment` namespace missing in runtime prompts: {path}")
    out: Dict[str, str] = {}
    for key in ("step1_entities_prompt", "step2_status_event_prompt", "step_relations_prompt"):
        t = seg.get(key)
        if not isinstance(t, str) or not t.strip():
            raise ValueError(f"`entity_segment.{key}` missing or empty: {path}")
        out[key] = t.strip()
    return out


def call_llm_json(
    llm_func: Callable[[str], str],
    prompt: str,
    *,
    context: str = "entity_segment",
) -> Optional[Dict[str, Any]]:
    try:
        raw = llm_func(prompt)
    except Exception as exc:
        logger.error("%s LLM call failed: %s", context, exc)
        return None
    return extract_json_object(raw)
