#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Optional


API_TRACE_PATTERN = re.compile(
    r"^API (?P<phase>call|response): (?P<function>[A-Za-z0-9_.-]+)(?P<detail>.*)$"
)

ANSI_RESET = "\033[0m"
ANSI_COLORS = [
    "\033[38;5;39m",   # blue
    "\033[38;5;82m",   # green
    "\033[38;5;208m",  # orange
    "\033[38;5;201m",  # magenta
    "\033[38;5;45m",   # cyan
    "\033[38;5;220m",  # yellow
]
UI_COLORS = [
    "#0077cc",
    "#22863a",
    "#d97706",
    "#a855f7",
    "#0f766e",
    "#b45309",
]


@dataclass
class TraceEvent:
    timestamp: str
    logger_name: str
    level_name: str
    phase: Optional[str]
    function_name: Optional[str]
    detail: str
    raw_message: str


def parse_api_trace_message(message: str) -> Optional[Dict[str, str]]:
    matched = API_TRACE_PATTERN.match((message or "").strip())
    if not matched:
        return None
    data = matched.groupdict()
    data["phase"] = data["phase"].upper()
    data["detail"] = (data.get("detail") or "").strip()
    return data


class FunctionColorMapper:
    def __init__(self) -> None:
        self._index_by_function: Dict[str, int] = {}

    def _idx(self, function_name: str) -> int:
        if function_name not in self._index_by_function:
            self._index_by_function[function_name] = len(self._index_by_function)
        return self._index_by_function[function_name]

    def ansi_color_for(self, function_name: str) -> str:
        return ANSI_COLORS[self._idx(function_name) % len(ANSI_COLORS)]

    def ui_color_for(self, function_name: str) -> str:
        return UI_COLORS[self._idx(function_name) % len(UI_COLORS)]


class FunctionColorFormatter(logging.Formatter):
    def __init__(self, color_mapper: Optional[FunctionColorMapper] = None) -> None:
        super().__init__("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        self.color_mapper = color_mapper or FunctionColorMapper()

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        parsed = parse_api_trace_message(record.getMessage())
        if not parsed:
            return formatted
        color = self.color_mapper.ansi_color_for(parsed["function"])
        return f"{color}{formatted}{ANSI_RESET}"


class FunctionTraceHandler(logging.Handler):
    def __init__(
        self,
        callback: Callable[[TraceEvent], None],
        include_non_api: bool = False,
    ) -> None:
        super().__init__(level=logging.INFO)
        self.callback = callback
        self.include_non_api = include_non_api

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            parsed = parse_api_trace_message(message)
            if parsed is None and not self.include_non_api:
                return
            event = TraceEvent(
                timestamp=datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                logger_name=record.name,
                level_name=record.levelname,
                phase=parsed["phase"] if parsed else None,
                function_name=parsed["function"] if parsed else None,
                detail=parsed["detail"] if parsed else message,
                raw_message=message,
            )
            self.callback(event)
        except Exception:
            self.handleError(record)


def configure_colored_logging(level: int = logging.INFO) -> FunctionColorMapper:
    color_mapper = FunctionColorMapper()
    handler = logging.StreamHandler(sys.stdout)
    if getattr(handler.stream, "isatty", lambda: False)():
        handler.setFormatter(FunctionColorFormatter(color_mapper))
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
    logging.basicConfig(level=level, handlers=[handler], force=True)
    return color_mapper
