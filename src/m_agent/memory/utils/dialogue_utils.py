#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict


logger = logging.getLogger(__name__)


def save_dialogue(dialogue: Dict[str, Any], output_dir: str, default_output_dir: str = None) -> bool:
    if output_dir is None:
        if default_output_dir is None:
            logger.error("No output directory provided")
            return False
        output_dir = default_output_dir

    try:
        dialogue_id = dialogue.get("dialogue_id", "unknown")

        start_time = dialogue.get("meta", {}).get("start_time", "")
        if start_time:
            try:
                dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                year_month = dt.strftime("%Y-%m")
            except Exception:
                year_month = "unknown"
        else:
            year_month = "unknown"

        year_month_dir = os.path.join(output_dir, year_month)
        os.makedirs(year_month_dir, exist_ok=True)

        filename = f"{dialogue_id}.json"
        filepath = os.path.join(year_month_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(dialogue, f, ensure_ascii=False, indent=2)

        logger.info("Saved dialogue: %s", filepath)
        return True

    except Exception as exc:
        logger.error("Failed to save dialogue: %s", exc)
        return False
