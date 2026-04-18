#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility shim for legacy imports.

Historically the LoCoMo eval entry lived at ``scripts/run_eval_locomo.py``.
The implementation has moved to ``scripts/run_locomo/run_eval_locomo.py``.
This module re-exports the public helpers used by tests and external tooling.
"""

from __future__ import annotations

import sys
from pathlib import Path

THIS = Path(__file__).resolve()
RUN_LOCOMO_DIR = THIS.parent / "run_locomo"
if str(RUN_LOCOMO_DIR) not in sys.path:
    sys.path.insert(0, str(RUN_LOCOMO_DIR))

# Re-export everything expected by downstream callers/tests.
from run_eval_locomo import (  # type: ignore  # noqa: F401
    LOCOMO_SOURCE_QA_INDEX_KEY,
    _apply_trace_record_to_qa,
    eval_question_answering_locomo,
    filter_samples_by_question_selection,
    load_question_selection_config,
    main,
)

__all__ = [
    "LOCOMO_SOURCE_QA_INDEX_KEY",
    "_apply_trace_record_to_qa",
    "eval_question_answering_locomo",
    "filter_samples_by_question_selection",
    "load_question_selection_config",
    "main",
]

if __name__ == "__main__":
    raise SystemExit(main())

