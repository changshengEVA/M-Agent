#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quiet noisy INFO logs from HTTP clients and per-chunk scene/fact extraction unless debug.
"""

from __future__ import annotations

import logging
import os
from typing import Final

_NOISY_LOGGERS: Final[tuple[str, ...]] = (
    "httpx",
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "openai",
    "urllib3",
    "urllib3.connectionpool",
    "http.client",
)

_FACT_SCENE_LOGGER = "m_agent.memory.build_memory.form_scene_details"


def suppress_verbose_pipeline_loggers(*, debug: bool = False) -> None:
    """
    When ``debug`` is False, raise the level of third-party HTTP/OpenAI loggers and
    the verbose fact-extraction module to WARNING so INFO lines (e.g. "HTTP Request:")
    and per-chunk "Fact extract ..." lines are hidden.

    Set environment ``M_AGENT_LOG_DEBUG=1`` to force ``debug=True`` (e.g. for subprocesses).
    """
    if debug:
        return
    if (os.environ.get("M_AGENT_LOG_DEBUG", "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return
    level = logging.WARNING
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(level)
    logging.getLogger(_FACT_SCENE_LOGGER).setLevel(level)
