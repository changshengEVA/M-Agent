#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run MemoryAgent on LongMemEval questions and write a jsonl of ``question_id`` + ``hypothesis``
for upstream LongMemEval ``src/evaluation/evaluate_qa.py``.

Does not compute LoCoMo-style F1; official scoring runs in the LongMemEval repository.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

THIS = Path(__file__).resolve()
PROJECT_ROOT = THIS.parents[2]
RUN_LOCOMO = PROJECT_ROOT / "scripts" / "run_locomo"
if str(RUN_LOCOMO) not in sys.path:
    sys.path.insert(0, str(RUN_LOCOMO))

from _bootstrap import bootstrap_project

bootstrap_project()

from m_agent.paths import LOG_DIR, resolve_project_path

logger = logging.getLogger("run_eval_longmemeval")


def _sanitize_test_id(test_id: str) -> str:
    cleaned = str(test_id).strip()
    if not cleaned:
        return "default"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", cleaned)
    return cleaned.strip("._-") or "default"


def _sanitize_filename(name: str) -> str:
    """Sanitize a string so it can be used as a filename cross-platform."""
    cleaned = str(name or "").strip()
    if not cleaned:
        return "unknown"
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned.strip("._-") or "unknown"


def _load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _question_text(record: Dict[str, Any]) -> str:
    for key in ("question", "query", "Query"):
        v = record.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _inject_question_date(record: Dict[str, Any], question: str) -> str:
    """Prepend benchmark ``question_date`` so relative times (e.g. last Saturday) resolve correctly.

    LongMemEval provides ``question_date`` on each record; MemoryAgent only sees the ask string,
    so the eval harness anchors time here without changing the agent.
    """
    qd = record.get("question_date")
    if not isinstance(qd, str) or not qd.strip():
        return question
    anchor = qd.strip()
    return (
        "[Evaluation context]\n"
        "The following question is asked at this datetime (treat as local wall-clock per the "
        "benchmark). Use it to resolve relative time phrases (e.g. \"last Saturday\", "
        "\"yesterday\", \"this week\"):\n"
        f"{anchor}\n\n"
        f"Question:\n{question}"
    )


def _hypothesis_from_result(result: Dict[str, Any]) -> str:
    g = result.get("gold_answer")
    if isinstance(g, str) and g.strip():
        return g.strip()
    a = result.get("answer")
    if isinstance(a, str):
        return a.strip()
    return ""


def _parse_question_ids_arg(raw: str) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for tok in str(raw or "").split(","):
        q = tok.strip()
        if not q or q in seen:
            continue
        seen.add(q)
        out.append(q)
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Write LongMemEval hypothesis jsonl (question_id + hypothesis) for official evaluate_qa.py."
    )
    p.add_argument(
        "--data-file",
        type=str,
        required=True,
        help="LongMemEval JSON list (e.g. longmemeval_s_cleaned.json).",
    )
    p.add_argument(
        "--config",
        type=str,
        default="config/agents/memory/longmemeval_eval_memory_agent.yaml",
        help="MemoryAgent yaml.",
    )
    p.add_argument(
        "--test-id",
        type=str,
        default="longmemeval_run",
        help="Subfolder under log/ for hypothesis jsonl.",
    )
    p.add_argument(
        "--workflow-id",
        type=str,
        default="",
        help="Override MemoryCore workflow_id (must match import process_id).",
    )
    p.add_argument(
        "--question-ids",
        type=str,
        default="",
        help="Comma-separated question_id filter (default: all records in file).",
    )
    p.add_argument(
        "--thread-id-prefix",
        type=str,
        default="longmemeval-eval",
        help="Prefix for MemoryAgent.ask thread_id.",
    )
    p.add_argument(
        "--hypothesis-jsonl",
        type=str,
        default="longmemeval_hypothesis.jsonl",
        help="Output filename under log/<test-id>/",
    )
    p.add_argument(
        "--recall-dir",
        type=str,
        default="recall",
        help=(
            "Write one pretty JSON file per question_id under log/<test-id>/<recall-dir>/"
            " (default: recall). Each file contains the full recall result dict."
        ),
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing hypothesis jsonl.",
    )
    p.add_argument(
        "--append",
        action="store_true",
        help="Append to hypothesis jsonl (for multi-question runs writing the same file).",
    )
    p.add_argument(
        "--max-questions",
        type=int,
        default=0,
        help="Cap number of questions (0 = all).",
    )
    p.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="Delay between ask() calls.",
    )
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()

    data_path = Path(resolve_project_path(args.data_file))
    agent_path = Path(resolve_project_path(args.config))
    if not data_path.is_file():
        logger.error("data-file not found: %s", data_path)
        return 2
    if not agent_path.is_file():
        logger.error("config not found: %s", agent_path)
        return 2

    test_id = _sanitize_test_id(args.test_id)
    out_dir = LOG_DIR / test_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_jsonl = out_dir / str(args.hypothesis_jsonl or "longmemeval_hypothesis.jsonl").strip()
    recall_dir = out_dir / _sanitize_filename(args.recall_dir)
    recall_dir.mkdir(parents=True, exist_ok=True)

    wf = str(args.workflow_id or "").strip()

    raw = _load_json(data_path)
    if not isinstance(raw, list):
        logger.error("Expected a JSON list in %s", data_path)
        return 2

    filter_ids = _parse_question_ids_arg(args.question_ids)
    filter_set: Optional[Set[str]] = set(filter_ids) if filter_ids else None

    records: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("question_id", "") or "").strip()
        if not qid:
            continue
        if filter_set is not None and qid not in filter_set:
            continue
        records.append(item)

    if not records:
        logger.error("No matching records after filter (question_ids=%s).", filter_ids)
        return 2

    if out_jsonl.exists() and not args.overwrite and not args.append:
        logger.error("Output exists: %s (use --overwrite or --append)", out_jsonl)
        return 2

    from m_agent.agents.memory_agent import create_memory_agent

    agent = create_memory_agent(str(agent_path), memory_workflow_id=wf or None)
    logger.info("workflow_id=%s", wf or "(from MemoryCore yaml)")
    logger.info("Writing %s", out_jsonl)
    logger.info("Writing per-question recall JSONs under %s", recall_dir)

    mode = "a" if args.append and out_jsonl.exists() else "w"
    asked = 0
    with open(out_jsonl, mode, encoding="utf-8") as fp:
        for rec in records:
            if args.max_questions and asked >= args.max_questions:
                break
            qid = str(rec.get("question_id", "") or "").strip()
            qtext = _question_text(rec)
            if not qtext:
                logger.warning("Skip question_id=%s (empty question text)", qid)
                continue
            ask_text = _inject_question_date(rec, qtext)
            thread_id = f"{args.thread_id_prefix}:{qid}"
            result: Dict[str, Any] | None = None
            error_text: str | None = None
            try:
                result = agent.ask(ask_text, thread_id=thread_id)
                hyp = _hypothesis_from_result(result if isinstance(result, dict) else {})
            except Exception as exc:
                logger.exception("ask failed for question_id=%s: %s", qid, exc)
                hyp = ""
                error_text = repr(exc)

            line = json.dumps(
                {"question_id": qid, "hypothesis": hyp},
                ensure_ascii=False,
            )
            fp.write(line + "\n")
            fp.flush()

            per_question = {
                "question_id": qid,
                "thread_id": thread_id,
                "question": qtext,
                "result": result,
                "error": error_text,
            }
            per_question_path = recall_dir / f"{_sanitize_filename(qid)}.json"
            with open(per_question_path, "w", encoding="utf-8") as f_one:
                json.dump(per_question, f_one, ensure_ascii=False, indent=2, default=str)

            # ------------------------------------------------------------------
            # Per-round workspace snapshots (optional, produced by MemoryAgent).
            # Target:
            #   log/<test_id>/<recall_dir>/<question_id>/Workspace/<round_XXX>.json
            # ------------------------------------------------------------------
            if isinstance(result, dict):
                rounds = result.get("workspace_rounds")
                if isinstance(rounds, list) and rounds:
                    q_folder = recall_dir / _sanitize_filename(qid)
                    ws_dir = q_folder / "Workspace"
                    ws_dir.mkdir(parents=True, exist_ok=True)
                    for item in rounds:
                        if not isinstance(item, dict):
                            continue
                        rid_raw = item.get("round_id")
                        try:
                            rid = int(rid_raw)
                        except Exception:
                            rid = 0
                        name = f"round_{rid:03d}.json" if rid > 0 else f"round_{_sanitize_filename(str(rid_raw))}.json"
                        with open(ws_dir / name, "w", encoding="utf-8") as f_round:
                            json.dump(item, f_round, ensure_ascii=False, indent=2, default=str)

            asked += 1
            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)

    logger.info("Wrote %d line(s) to %s", asked, out_jsonl)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
