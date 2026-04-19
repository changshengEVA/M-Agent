#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build LOCOMO recall_trace artifacts (aligned with LongMemEval trace_longmemeval_evidence output).

Reads:
  - log/<test_id>/<recall_dir>/*.json (from run_eval_locomo.py)
  - LoCoMo annotation JSON (sample_id + qa + evidence D#:#)
  - data/memory/<workflow_id>/ (episodes + scene, same layout as import)

Writes:
  - log/<test_id>/recall_trace/<trace_id>.json
  - log/<test_id>/recall_trace/summary.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

THIS = Path(__file__).resolve()
PROJECT_ROOT = THIS.parents[2]
if str(PROJECT_ROOT / "scripts" / "run_longmemeval") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "run_longmemeval"))

from _bootstrap import bootstrap_project

bootstrap_project()

from m_agent.paths import LOG_DIR

from trace_longmemeval_evidence import (  # type: ignore[import-not-found]
    EpisodeRef,
    _attach_facts,
    _build_segment_facts_index,
    _extract_retrieved_episode_refs,
    _find_segment_for_turn,
    _load_episode_segments_map,
    _load_json,
    _sanitize_filename,
    _write_json,
    _write_jsonl_line,
)

logger = logging.getLogger("trace_locomo_evidence")

_DIALOGUE_REF = re.compile(r"^D(\d+):(\d+)$")


def _sanitize_recall_dir_segment(name: str) -> str:
    """Match run_eval_locomo._sanitize_recall_dir_segment for recall folder naming."""
    s = str(name or "").strip() or "recall"
    s = s.replace("\\", "/").split("/")[-1]
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("._-") or "recall"
    return s[:120]


def _sanitize_test_id(test_id: str) -> str:
    cleaned = str(test_id).strip()
    if not cleaned:
        return "default"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", cleaned)
    cleaned = cleaned.strip("._-")
    return cleaned or "default"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate recall_trace/*.json for LOCOMO from recall/*.json + annotations + memory store."
    )
    p.add_argument("--test-id", type=str, required=True, help="Eval test_id (subfolder under log/).")
    p.add_argument(
        "--recall-dir",
        type=str,
        default="recall",
        help="Recall folder under log/<test_id>/ (default: recall).",
    )
    p.add_argument(
        "--data-file",
        type=str,
        default="data/locomo/data/locomo10.json",
        help="LoCoMo JSON list (sample_id + qa).",
    )
    p.add_argument(
        "--workflow-id",
        type=str,
        required=True,
        help="Memory workflow id (data/memory/<workflow_id>/).",
    )
    p.add_argument(
        "--question-ids",
        type=str,
        default="",
        help="Optional comma-separated trace_id filter (default: all *.json under recall-dir).",
    )
    p.add_argument(
        "--max-scene-files",
        type=int,
        default=0,
        help="Cap scene/*.json scan (0=all).",
    )
    p.add_argument("--overwrite", action="store_true", help="Overwrite recall_trace/<id>.json if present.")
    return p.parse_args()


def _parse_csv(raw: str) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for tok in str(raw or "").split(","):
        v = tok.strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _resolve_project_path(rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / p).resolve()


def parse_trace_id(trace_id: str) -> Tuple[str, int]:
    t = str(trace_id or "").strip()
    if "__q" not in t:
        raise ValueError(f"Invalid trace_id (expected sample_id__q<int>): {trace_id!r}")
    sid, _, rest = t.partition("__q")
    sid = sid.strip()
    try:
        qidx = int(rest.strip())
    except Exception as exc:
        raise ValueError(f"Invalid trace_id suffix: {trace_id!r}") from exc
    if not sid:
        raise ValueError(f"Empty sample_id in trace_id: {trace_id!r}")
    return sid, qidx


def _load_locomo_index(data_path: Path) -> Dict[str, Dict[str, Any]]:
    raw = _load_json(data_path)
    if not isinstance(raw, list):
        raise ValueError(f"Expected LoCoMo list in {data_path}")
    out: Dict[str, Dict[str, Any]] = {}
    for item in raw:
        if isinstance(item, dict):
            sid = str(item.get("sample_id", "") or "").strip()
            if sid:
                out[sid] = item
    return out


def _dialogue_id_for_session(
    episodes_root: Path,
    sample_id: str,
    session_num: int,
) -> Optional[str]:
    by_d = episodes_root / "by_dialogue"
    if not by_d.is_dir():
        return None
    suffix = f"_{session_num}"
    sid_token = str(sample_id or "").strip()
    for p in sorted(by_d.iterdir()):
        if not p.is_dir():
            continue
        name = p.name
        if not name.endswith(suffix):
            continue
        if sid_token and sid_token not in name:
            continue
        return name
    return None


def _parse_d_ref(ref: str) -> Optional[Tuple[int, int]]:
    m = _DIALOGUE_REF.match(str(ref or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _d_ref_to_episode_ref(
    d_ref: str,
    *,
    memory_root: Path,
    sample_id: str,
    episodes_root: Path,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """Map Dsession:turn1based to dialogue_id:episode_id:segment_id. Returns (ref or None, issues)."""
    issues: List[Dict[str, Any]] = []
    parsed = _parse_d_ref(d_ref)
    if not parsed:
        issues.append({"type": "invalid_d_ref", "ref": d_ref})
        return None, issues
    session_num, turn_one_based = parsed
    turn_idx = turn_one_based - 1
    if turn_idx < 0:
        issues.append({"type": "invalid_turn", "ref": d_ref, "turn_one_based": turn_one_based})
        return None, issues

    dialogue_id = _dialogue_id_for_session(episodes_root, sample_id, session_num)
    if not dialogue_id:
        issues.append(
            {
                "type": "dialogue_not_found",
                "sample_id": sample_id,
                "session_num": session_num,
                "d_ref": d_ref,
            }
        )
        return None, issues

    segments = _load_episode_segments_map(memory_root, dialogue_id)
    hit = _find_segment_for_turn(segments, turn_idx)
    if not hit:
        issues.append(
            {
                "type": "turn_without_segment",
                "dialogue_id": dialogue_id,
                "turn_index": turn_idx,
                "d_ref": d_ref,
            }
        )
        return None, issues
    episode_id, segment_id, turn_span = hit
    ref = EpisodeRef(dialogue_id, episode_id, segment_id).to_ref()
    return ref, issues


def _build_gold_segments_for_qa(
    evidence: Any,
    *,
    memory_root: Path,
    sample_id: str,
    episodes_root: Path,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    if not isinstance(evidence, list):
        return rows, issues
    for item in evidence:
        ref_raw = str(item or "").strip()
        if not ref_raw:
            continue
        ep_ref, iss = _d_ref_to_episode_ref(
            ref_raw, memory_root=memory_root, sample_id=sample_id, episodes_root=episodes_root
        )
        issues.extend(iss)
        if not ep_ref:
            continue
        parts = ep_ref.split(":")
        did = parts[0] if parts else ""
        eid = parts[1] if len(parts) > 1 else ""
        sid = parts[2] if len(parts) > 2 else ""
        rows.append(
            {
                "episode_ref": ep_ref,
                "dialogue_id": did,
                "episode_id": eid,
                "segment_id": sid,
                "source_d_ref": ref_raw,
            }
        )
    return rows, issues


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()

    test_id = str(args.test_id or "").strip()
    if not test_id:
        logger.error("--test-id is required.")
        return 2

    safe_test = _sanitize_test_id(test_id)
    recall_root = (LOG_DIR / safe_test / _sanitize_recall_dir_segment(args.recall_dir)).resolve()
    if not recall_root.is_dir():
        logger.error("Recall dir not found: %s", recall_root)
        return 2

    data_path = _resolve_project_path(str(args.data_file or "").strip())
    if not data_path.is_file():
        logger.error("Data file not found: %s", data_path)
        return 2

    wf = str(args.workflow_id or "").strip()
    if not wf:
        logger.error("--workflow-id is required.")
        return 2
    memory_root = (PROJECT_ROOT / "data" / "memory" / wf).resolve()
    if not memory_root.is_dir():
        logger.error("Memory root not found: %s", memory_root)
        return 2
    episodes_root = memory_root / "episodes"
    if not episodes_root.is_dir():
        logger.error("episodes/ not under memory root: %s", memory_root)
        return 2

    sample_index = _load_locomo_index(data_path)
    logger.info("Loaded LoCoMo samples: %d from %s", len(sample_index), data_path)

    trace_root = (LOG_DIR / safe_test / "recall_trace").resolve()
    trace_root.mkdir(parents=True, exist_ok=True)
    summary_path = trace_root / "summary.jsonl"
    if summary_path.exists() and args.overwrite:
        summary_path.unlink()

    filter_ids = _parse_csv(args.question_ids)
    recall_files = sorted(p for p in recall_root.glob("*.json") if p.is_file())
    if filter_ids:
        want = set(filter_ids)
        recall_files = [p for p in recall_files if p.stem in want]

    if not recall_files:
        logger.error("No recall/*.json under %s", recall_root)
        return 2

    wrote = 0
    for recall_json in recall_files:
        stem = recall_json.stem
        try:
            sample_id, qa_idx = parse_trace_id(stem)
        except ValueError:
            logger.warning("Skip %s (cannot parse trace_id from filename)", recall_json.name)
            continue

        out_path = trace_root / f"{_sanitize_filename(stem)}.json"
        if out_path.exists() and not args.overwrite:
            logger.info("Skip %s (exists; use --overwrite)", out_path.name)
            continue

        recall_obj = _load_json(recall_json)
        if not isinstance(recall_obj, dict):
            logger.warning("Skip %s (invalid recall json)", recall_json.name)
            continue

        sample = sample_index.get(sample_id)
        if not isinstance(sample, dict):
            logger.warning("No sample_id=%s in data file", sample_id)
            sample = {}

        qas = sample.get("qa", [])
        qa: Dict[str, Any] = {}
        if isinstance(qas, list) and 0 <= qa_idx < len(qas) and isinstance(qas[qa_idx], dict):
            qa = qas[qa_idx]
        else:
            logger.warning("Missing qa index %d for sample_id=%s", qa_idx, sample_id)

        oracle_question = str(qa.get("question", "") or "").strip()
        oracle_answer = str(qa.get("answer", "") or "").strip()
        evidence = qa.get("evidence")

        gold_segments, mapping_issues = _build_gold_segments_for_qa(
            evidence,
            memory_root=memory_root,
            sample_id=sample_id,
            episodes_root=episodes_root,
        )

        model_result = recall_obj.get("result") if isinstance(recall_obj.get("result"), dict) else {}
        retrieved_refs = _extract_retrieved_episode_refs(model_result)

        retrieved_segments: List[Dict[str, Any]] = []
        for ref in retrieved_refs:
            parts = ref.split(":")
            if len(parts) < 3:
                continue
            did, eid, sid = parts[0], parts[1], parts[2]
            retrieved_segments.append(
                {
                    "episode_ref": ref,
                    "dialogue_id": did,
                    "episode_id": eid,
                    "segment_id": sid,
                }
            )

        facts_index = _build_segment_facts_index(memory_root, max_scene_files=int(args.max_scene_files or 0))
        _attach_facts(gold_segments, facts_index)
        _attach_facts(retrieved_segments, facts_index)

        gold_set = {
            str(x.get("episode_ref", "") or "").strip()
            for x in gold_segments
            if str(x.get("episode_ref", "") or "").strip()
        }
        retrieved_set = {
            str(x.get("episode_ref", "") or "").strip()
            for x in retrieved_segments
            if str(x.get("episode_ref", "") or "").strip()
        }

        hit = sorted(gold_set.intersection(retrieved_set))
        missed = sorted(gold_set.difference(retrieved_set))
        extra = sorted(retrieved_set.difference(gold_set))

        hyp = ""
        if isinstance(model_result, dict):
            g = model_result.get("gold_answer")
            a = model_result.get("answer")
            if isinstance(g, str) and g.strip():
                hyp = g.strip()
            elif isinstance(a, str) and a.strip():
                hyp = a.strip()

        payload: Dict[str, Any] = {
            "question_id": stem,
            "trace_id": stem,
            "sample_id": sample_id,
            "qa_index": qa_idx,
            "test_id": test_id,
            "paths": {
                "recall_json": str(recall_json),
                "trace_json": str(out_path),
                "data_file": str(data_path),
                "memory_root": str(memory_root),
            },
            "oracle": {
                "question": oracle_question,
                "answer": oracle_answer,
                "evidence_d_refs": evidence if isinstance(evidence, list) else [],
                "gold_segments": gold_segments,
                "mapping_issues": mapping_issues,
            },
            "model_recall": {
                "hypothesis": hyp,
                "result": model_result,
                "retrieved_episode_refs": retrieved_refs,
                "retrieved_segments": retrieved_segments,
            },
            "alignment": {
                "gold_segment_count": len(gold_set),
                "retrieved_segment_count": len(retrieved_set),
                "hit_gold_segments": hit,
                "missed_gold_segments": missed,
                "extra_segments": extra,
            },
        }

        _write_json(out_path, payload)
        _write_jsonl_line(
            summary_path,
            {
                "question_id": stem,
                "test_id": test_id,
                "sample_id": sample_id,
                "qa_index": qa_idx,
                "oracle_answer": oracle_answer,
                "hypothesis": hyp,
                "gold_segment_count": len(gold_set),
                "retrieved_segment_count": len(retrieved_set),
                "hit_gold_segment_count": len(hit),
                "missed_gold_segment_count": len(missed),
            },
        )
        wrote += 1

    logger.info("Wrote %d trace file(s) under %s", wrote, trace_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
