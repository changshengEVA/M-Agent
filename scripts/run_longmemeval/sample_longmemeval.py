#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stratified random sampling of LongMemEval question_ids by ``question_type``.

Configured under ``selection.sampling`` (legacy: ``eval.sampling``). When
``selection.question_ids`` is non-empty, that list is used and sampling is ignored
for target selection (see ``get_batch_question_ids``).
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

THIS = Path(__file__).resolve()
PROJECT_ROOT = THIS.parents[2]
RUN_LOCOMO = PROJECT_ROOT / "scripts" / "run_locomo"
if str(RUN_LOCOMO) not in sys.path:
    sys.path.insert(0, str(RUN_LOCOMO))

from _bootstrap import bootstrap_project

bootstrap_project()

from _shared import parse_question_ids


def _load_json_list(path: Path) -> List[Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _parse_sampling_dict(samp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Returns None if sampling is not enabled.
    Enabled when ``per_question_type`` is a non-empty mapping of
    ``question_type`` -> positive int (count to draw per type).
    """
    if not isinstance(samp, dict):
        return None
    raw = samp.get("per_question_type")
    if not isinstance(raw, dict) or not raw:
        return None
    per: Dict[str, int] = {}
    for k, v in raw.items():
        key = str(k or "").strip()
        if not key:
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > 0:
            per[key] = n
    if not per:
        return None
    try:
        default_pt = int(samp.get("default_per_type", 0))
    except (TypeError, ValueError):
        default_pt = 0
    try:
        seed = int(samp.get("seed", 42))
    except (TypeError, ValueError):
        seed = 42
    return {
        "seed": seed,
        "per_question_type": per,
        "default_per_type": max(0, default_pt),
    }


def parse_sampling_config(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Read ``selection.sampling`` first; if the key is absent, fall back to ``eval.sampling``
    (legacy). Returns None if neither defines a valid sampling block.
    """
    selection = payload.get("selection")
    if isinstance(selection, dict) and "sampling" in selection:
        samp = selection.get("sampling")
        if isinstance(samp, dict):
            return _parse_sampling_dict(samp)
        return None
    eval_cfg = payload.get("eval")
    if isinstance(eval_cfg, dict) and "sampling" in eval_cfg:
        samp = eval_cfg.get("sampling")
        if isinstance(samp, dict):
            return _parse_sampling_dict(samp)
        return None
    return None


def sample_question_ids(
    *,
    data_path: Path,
    selection_question_ids: List[str],
    sampling_cfg: Dict[str, Any],
) -> List[str]:
    """
    Load ``data_path`` (JSON list), optionally restrict to ``selection_question_ids``,
    then for each ``question_type`` draw up to ``per_question_type[type]`` records
    (without replacement within type) using ``sampling_cfg['seed']``.

    Types not listed in ``per_question_type`` use ``default_per_type`` (default 0 = skip).
    """
    raw = _load_json_list(data_path)
    records: List[Dict[str, Any]] = [x for x in raw if isinstance(x, dict)]
    if selection_question_ids:
        sel = set(selection_question_ids)
        records = [r for r in records if str(r.get("question_id", "") or "").strip() in sel]

    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in records:
        qt = str(r.get("question_type") or "").strip() or "_unknown"
        groups[qt].append(r)

    rng = random.Random(int(sampling_cfg["seed"]))
    per = dict(sampling_cfg["per_question_type"])
    default_n = int(sampling_cfg.get("default_per_type", 0))

    chosen: List[Dict[str, Any]] = []
    for qt, items in sorted(groups.items(), key=lambda x: x[0]):
        n_want = per.get(qt, default_n)
        if n_want <= 0:
            continue
        k = min(int(n_want), len(items))
        if k <= 0:
            continue
        if k == len(items):
            chosen.extend(items)
        else:
            chosen.extend(rng.sample(items, k))

    ids = sorted(
        {
            str(r.get("question_id", "") or "").strip()
            for r in chosen
            if str(r.get("question_id", "") or "").strip()
        }
    )
    return ids


def get_batch_question_ids(payload: Dict[str, Any], data_path: Path) -> List[str]:
    """
    Resolve which question_ids to run:

    - If ``selection.question_ids`` is non-empty, return it (``selection.sampling`` ignored).
    - Else if ``selection.sampling`` (or legacy ``eval.sampling``) is valid, sample from
      the full ``data.file`` (stratified by ``question_type``).
    - Else raise ``ValueError``.
    """
    sel = parse_question_ids(payload)
    if sel:
        return sel
    sc = parse_sampling_config(payload)
    if sc is not None:
        ids = sample_question_ids(
            data_path=data_path,
            selection_question_ids=[],
            sampling_cfg=sc,
        )
        if not ids:
            raise ValueError(
                "selection.sampling produced no question_ids (check data file and per_question_type counts)."
            )
        return ids
    raise ValueError(
        "No targets: set selection.question_ids or selection.sampling (per_question_type). "
        "Legacy eval.sampling is still read if selection.sampling is absent."
    )
