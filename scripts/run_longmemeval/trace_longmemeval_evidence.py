#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trace LongMemEval gold evidence (oracle) ↔ M-Agent segments ↔ facts.

This script is intentionally read-only: it consumes existing outputs:
- log/<test_id>/<recall_dir>/<question_id>.json (from run_eval_longmemeval.py)
- data/memory/longmemeval/<data_stem>/<question_id>/... (import + warmup outputs)
and an external oracle file:
- longmemeval_oracle.json (downloaded from LongMemEval dataset release)

It then writes:
- log/<test_id>/recall_trace/<question_id>.json
- log/<test_id>/recall_trace/summary.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

THIS = Path(__file__).resolve()
PROJECT_ROOT = THIS.parents[2]
RUN_LOCOMO = PROJECT_ROOT / "scripts" / "run_locomo"
if str(RUN_LOCOMO) not in sys.path:
    sys.path.insert(0, str(RUN_LOCOMO))

from _bootstrap import bootstrap_project

bootstrap_project()

from m_agent.paths import LOG_DIR
from scripts.run_locomo._shared import resolve_project_path

logger = logging.getLogger("trace_longmemeval_evidence")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EpisodeRef:
    dialogue_id: str
    episode_id: str
    segment_id: str

    def to_ref(self) -> str:
        d = str(self.dialogue_id or "").strip()
        e = str(self.episode_id or "").strip()
        s = str(self.segment_id or "").strip()
        return f"{d}:{e}:{s}".strip(":")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Generate evidence trace logs for LongMemEval: "
            "oracle gold turns → M-Agent segments → facts, aligned with model recall."
        )
    )
    p.add_argument("--test-id", type=str, required=True, help="Eval test_id (subfolder under log/).")
    p.add_argument(
        "--recall-dir",
        type=str,
        default="recall",
        help="Recall folder under log/<test_id>/ (default: recall).",
    )
    p.add_argument(
        "--oracle-json",
        type=str,
        required=True,
        help="Path to upstream longmemeval_oracle.json (not the cleaned full haystack).",
    )
    p.add_argument(
        "--question-ids",
        type=str,
        default="",
        help="Optional comma-separated question_id filter (default: all *.json under recall-dir).",
    )
    p.add_argument(
        "--memory-root",
        type=str,
        default="",
        help=(
            "Optional override for memory root directory. If provided, the script expects "
            "<memory-root>/<question_id>/... to exist. "
            "Default: auto-discover under data/memory/longmemeval/*/<question_id>/."
        ),
    )
    p.add_argument(
        "--max-scene-files",
        type=int,
        default=0,
        help="Debug option: cap number of scene/*.json files to scan (0=all).",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing recall_trace/<qid>.json if present.",
    )
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


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)


def _write_jsonl_line(path: Path, row: Dict[str, Any]) -> None:
    line = json.dumps(row, ensure_ascii=False, default=str)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._-]+$")


def _is_safe_qid(qid: str) -> bool:
    return bool(str(qid or "").strip() and _SAFE_TOKEN.fullmatch(str(qid).strip()))


def _sanitize_filename(name: str) -> str:
    cleaned = str(name or "").strip()
    if not cleaned:
        return "unknown"
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned.strip("._-") or "unknown"


# ---------------------------------------------------------------------------
# Locate inputs (recall / memory roots)
# ---------------------------------------------------------------------------


def _discover_recall_question_ids(recall_dir: Path) -> List[str]:
    qids: List[str] = []
    for path in sorted(recall_dir.glob("*.json")):
        qid = path.stem
        if _is_safe_qid(qid):
            qids.append(qid)
    return qids


def _resolve_memory_root_for_qid(qid: str, override: str = "") -> Tuple[Optional[Path], List[str]]:
    """Return (memory_root, candidates). memory_root points to .../<data_stem>/<qid>/."""
    if override:
        base = Path(resolve_project_path(override))
        candidate = base / qid
        if candidate.is_dir():
            return candidate, [str(candidate)]
        if base.is_dir() and (base / "dialogues").is_dir() and (base / "scene").is_dir():
            # User passed the per-qid memory root directly.
            return base, [str(base)]
        return None, [str(candidate), str(base)]

    base = PROJECT_ROOT / "data" / "memory" / "longmemeval"
    candidates: List[str] = []
    if not base.is_dir():
        return None, candidates

    hits: List[Path] = []
    for stem_dir in sorted(base.iterdir()):
        if not stem_dir.is_dir():
            continue
        p = stem_dir / qid
        candidates.append(str(p))
        if p.is_dir():
            hits.append(p)
    if not hits:
        return None, candidates
    if len(hits) > 1:
        # Stable pick: prefer *_s_cleaned, else first.
        hits.sort(key=lambda p: ("_s_cleaned" not in p.parent.name, p.parent.name))
    return hits[0], [str(p) for p in hits]


def _data_stem_from_memory_root(memory_root: Path) -> str:
    # memory_root: .../data/memory/longmemeval/<data_stem>/<qid>/
    return str(memory_root.parent.name)


# ---------------------------------------------------------------------------
# Oracle parsing
# ---------------------------------------------------------------------------


def _load_oracle_index(oracle_json_path: Path) -> Dict[str, Dict[str, Any]]:
    raw = _load_json(oracle_json_path)
    if not isinstance(raw, list):
        raise ValueError(f"Expected oracle json to be a list: {oracle_json_path}")
    out: Dict[str, Dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("question_id", "") or "").strip()
        if not qid:
            continue
        out[qid] = item
    return out


def _oracle_turn_evidence(oracle_item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return a flat list of turn evidence rows with session_id + turn index."""
    sess_ids = oracle_item.get("haystack_session_ids")
    sessions = oracle_item.get("haystack_sessions")
    if not isinstance(sess_ids, list) or not isinstance(sessions, list):
        return []

    rows: List[Dict[str, Any]] = []
    for i, sid in enumerate(sess_ids):
        session_id = str(sid or "").strip()
        if not session_id:
            continue
        turns = sessions[i] if i < len(sessions) else []
        if not isinstance(turns, list):
            continue
        for t_idx, turn in enumerate(turns):
            if not isinstance(turn, dict):
                continue
            if turn.get("has_answer") is not True:
                continue
            role = str(turn.get("role", "") or "").strip()
            content = str(turn.get("content", "") or "").strip()
            rows.append(
                {
                    "session_id": session_id,
                    "turn_index": t_idx,
                    "role": role,
                    "content": content,
                }
            )
    return rows


# ---------------------------------------------------------------------------
# M-Agent mapping: session_id → dialogue_id → segment (EpisodeRef)
# ---------------------------------------------------------------------------


def _dialogue_files(memory_root: Path) -> List[Path]:
    d = memory_root / "dialogues"
    if not d.is_dir():
        return []
    # Expected layout: dialogues/YYYY-MM/dlg_*.json
    return [p for p in d.rglob("dlg_*.json") if p.is_file()]


def _build_session_to_dialogue_id(
    memory_root: Path, *, qid: str, data_stem: str
) -> Dict[str, str]:
    prefix = f"dlg_{data_stem}_{qid}_"
    mapping: Dict[str, str] = {}
    for path in _dialogue_files(memory_root):
        try:
            obj = _load_json(path)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        did = str(obj.get("dialogue_id", "") or "").strip()
        if not did.startswith(prefix):
            continue
        session_id = did[len(prefix) :].strip()
        if session_id and session_id not in mapping:
            mapping[session_id] = did
    return mapping


def _load_episode_segments_map(memory_root: Path, dialogue_id: str) -> List[Dict[str, Any]]:
    """Return list of segments with episode_id, segment_id, turn_span."""
    p = memory_root / "episodes" / "by_dialogue" / dialogue_id / "episodes_v1.json"
    if not p.is_file():
        return []
    try:
        obj = _load_json(p)
    except Exception:
        return []
    episodes = obj.get("episodes")
    if not isinstance(episodes, list):
        return []
    out: List[Dict[str, Any]] = []
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        episode_id = str(ep.get("episode_id", "") or "").strip()
        segments = ep.get("segments")
        if not episode_id or not isinstance(segments, list):
            continue
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            segment_id = str(seg.get("segment_id", "") or "").strip()
            turn_span = seg.get("turn_span")
            if not segment_id or not (isinstance(turn_span, list) and len(turn_span) == 2):
                continue
            try:
                a = int(turn_span[0])
                b = int(turn_span[1])
            except Exception:
                continue
            out.append(
                {
                    "episode_id": episode_id,
                    "segment_id": segment_id,
                    "turn_span": [a, b],
                }
            )
    return out


def _find_segment_for_turn(
    segments: Sequence[Dict[str, Any]], turn_index: int
) -> Optional[Tuple[str, str, List[int]]]:
    for seg in segments:
        span = seg.get("turn_span")
        if not (isinstance(span, list) and len(span) == 2):
            continue
        a, b = span
        if isinstance(a, int) and isinstance(b, int) and a <= turn_index <= b:
            return str(seg.get("episode_id", "") or ""), str(seg.get("segment_id", "") or ""), [a, b]
    return None


# ---------------------------------------------------------------------------
# Facts index: EpisodeRef → facts[]
# ---------------------------------------------------------------------------


def _extract_fact_text(fact_item: Dict[str, Any]) -> str:
    for key in ("Atomic fact", "atomic_fact", "fact", "fact_text", "evidence_sentence", "text"):
        v = fact_item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _iter_scene_files(memory_root: Path, max_files: int = 0) -> Iterable[Path]:
    scene_dir = memory_root / "scene"
    if not scene_dir.is_dir():
        return []
    files = [p for p in scene_dir.glob("*.json") if p.is_file()]
    files.sort(key=lambda p: p.name)
    if max_files and max_files > 0:
        files = files[: max_files]
    return files


def _build_segment_facts_index(memory_root: Path, *, max_scene_files: int = 0) -> Dict[str, List[str]]:
    """Build mapping: 'dialogue:episode:segment' → [fact_text...]"""
    out: Dict[str, List[str]] = {}
    seen: Dict[str, set[str]] = {}

    for scene_file in _iter_scene_files(memory_root, max_files=max_scene_files):
        try:
            obj = _load_json(scene_file)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        facts = obj.get("facts")
        if not isinstance(facts, list):
            continue
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            ev = fact.get("evidence")
            if not isinstance(ev, dict):
                continue
            did = str(ev.get("dialogue_id", "") or "").strip()
            eid = str(ev.get("episode_id", "") or "").strip()
            sid = str(ev.get("segment_id", "") or "").strip()
            if not (did and eid and sid):
                continue
            ref = EpisodeRef(did, eid, sid).to_ref()
            text = _extract_fact_text(fact)
            if not text:
                continue
            bucket = out.setdefault(ref, [])
            sset = seen.setdefault(ref, set())
            if text in sset:
                continue
            sset.add(text)
            bucket.append(text)

    return out


# ---------------------------------------------------------------------------
# Recall extraction (what the model retrieved)
# ---------------------------------------------------------------------------


def _extract_retrieved_episode_refs(result: Dict[str, Any]) -> List[str]:
    refs = result.get("evidence_episode_refs")
    if isinstance(refs, list):
        out: List[str] = []
        for item in refs:
            r = str(item or "").strip()
            if r and r not in out:
                out.append(r)
        return out

    # Fallback: use the last workspace round kept ids (if present).
    rounds = result.get("workspace_rounds")
    if isinstance(rounds, list) and rounds:
        last = rounds[-1] if isinstance(rounds[-1], dict) else {}
        judge_ws = last.get("workspace_after_judge") if isinstance(last, dict) else None
        if isinstance(judge_ws, dict):
            kept = judge_ws.get("kept_evidence_ids")
            if isinstance(kept, list):
                out = [str(x).strip() for x in kept if str(x).strip()]
                # Keep unique + stable order
                seen = set()
                uniq = []
                for r in out:
                    if r in seen:
                        continue
                    seen.add(r)
                    uniq.append(r)
                return uniq
    return []


# ---------------------------------------------------------------------------
# Main per-question trace
# ---------------------------------------------------------------------------


def _build_gold_segments(
    *,
    oracle_turns: List[Dict[str, Any]],
    session_to_dialogue: Dict[str, str],
    memory_root: Path,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (gold_turn_rows_enriched, gold_segment_rows, mapping_issues)."""
    gold_turns: List[Dict[str, Any]] = []
    gold_segments: Dict[str, Dict[str, Any]] = {}
    issues: List[Dict[str, Any]] = []

    seg_cache: Dict[str, List[Dict[str, Any]]] = {}

    for row in oracle_turns:
        session_id = str(row.get("session_id", "") or "").strip()
        turn_index_raw = row.get("turn_index")
        try:
            turn_index = int(turn_index_raw)
        except Exception:
            turn_index = -1
        dialogue_id = session_to_dialogue.get(session_id, "")
        enriched = dict(row)
        enriched["dialogue_id"] = dialogue_id or None
        enriched["episode_ref"] = None
        gold_turns.append(enriched)

        if not dialogue_id or turn_index < 0:
            issues.append(
                {
                    "type": "unmapped_turn",
                    "session_id": session_id,
                    "turn_index": turn_index,
                    "reason": "missing_dialogue_mapping" if not dialogue_id else "invalid_turn_index",
                }
            )
            continue
        segments = seg_cache.get(dialogue_id)
        if segments is None:
            segments = _load_episode_segments_map(memory_root, dialogue_id)
            seg_cache[dialogue_id] = segments
        hit = _find_segment_for_turn(segments, turn_index)
        if not hit:
            issues.append(
                {
                    "type": "turn_without_segment",
                    "session_id": session_id,
                    "dialogue_id": dialogue_id,
                    "turn_index": turn_index,
                    "reason": "no_segment_covering_turn",
                }
            )
            continue
        episode_id, segment_id, turn_span = hit
        if not (episode_id and segment_id):
            issues.append(
                {
                    "type": "turn_without_segment",
                    "session_id": session_id,
                    "dialogue_id": dialogue_id,
                    "turn_index": turn_index,
                    "reason": "empty_episode_or_segment_id",
                }
            )
            continue
        ref = EpisodeRef(dialogue_id, episode_id, segment_id).to_ref()
        enriched["episode_ref"] = ref
        node = gold_segments.get(ref)
        if node is None:
            node = {
                "episode_ref": ref,
                "dialogue_id": dialogue_id,
                "episode_id": episode_id,
                "segment_id": segment_id,
                "turn_span": turn_span,
                "supporting_turns": [],
            }
            gold_segments[ref] = node
        node["supporting_turns"].append(
            {"session_id": session_id, "turn_index": turn_index, "role": row.get("role"), "content": row.get("content")}
        )

    # Stable order: by ref string
    segment_rows = [gold_segments[k] for k in sorted(gold_segments.keys())]
    return gold_turns, segment_rows, issues


def _attach_facts(rows: List[Dict[str, Any]], facts_index: Dict[str, List[str]]) -> None:
    for row in rows:
        ref = str(row.get("episode_ref", "") or "").strip()
        if not ref:
            ref = EpisodeRef(
                str(row.get("dialogue_id", "") or ""),
                str(row.get("episode_id", "") or ""),
                str(row.get("segment_id", "") or ""),
            ).to_ref()
            row["episode_ref"] = ref
        row["facts"] = list(facts_index.get(ref, []))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()

    test_id = str(args.test_id or "").strip()
    if not test_id:
        logger.error("--test-id is required.")
        return 2

    recall_root = (LOG_DIR / test_id / _sanitize_filename(args.recall_dir)).resolve()
    if not recall_root.is_dir():
        logger.error("Recall dir not found: %s", recall_root)
        return 2

    trace_root = (LOG_DIR / test_id / "recall_trace").resolve()
    trace_root.mkdir(parents=True, exist_ok=True)
    summary_path = trace_root / "summary.jsonl"
    if summary_path.exists() and args.overwrite:
        summary_path.unlink()

    oracle_path = Path(resolve_project_path(args.oracle_json))
    if not oracle_path.is_file():
        logger.error("Oracle json not found: %s", oracle_path)
        return 2

    oracle_index = _load_oracle_index(oracle_path)
    logger.info("Loaded oracle: %s (items=%d)", oracle_path, len(oracle_index))

    filter_ids = _parse_csv(args.question_ids)
    if filter_ids:
        qids = [qid for qid in filter_ids if _is_safe_qid(qid)]
    else:
        qids = _discover_recall_question_ids(recall_root)

    if not qids:
        logger.error("No question_id found (recall_root=%s).", recall_root)
        return 2

    wrote = 0
    for qid in qids:
        recall_json = recall_root / f"{qid}.json"
        if not recall_json.is_file():
            logger.warning("Skip %s (missing recall json: %s)", qid, recall_json)
            continue

        out_path = trace_root / f"{_sanitize_filename(qid)}.json"
        if out_path.exists() and not args.overwrite:
            logger.info("Skip %s (trace exists; use --overwrite): %s", qid, out_path)
            continue

        recall_obj = _load_json(recall_json)
        if not isinstance(recall_obj, dict):
            logger.warning("Skip %s (invalid recall json shape)", qid)
            continue

        oracle_item = oracle_index.get(qid)
        if oracle_item is None:
            logger.warning("No oracle entry for %s; still exporting model_recall only.", qid)
            oracle_item = {}

        memory_root, memory_candidates = _resolve_memory_root_for_qid(qid, override=str(args.memory_root or "").strip())
        if memory_root is None:
            logger.warning("No memory root found for %s. candidates=%s", qid, memory_candidates)
            memory_root = None

        model_result = recall_obj.get("result") if isinstance(recall_obj.get("result"), dict) else {}
        retrieved_refs = _extract_retrieved_episode_refs(model_result)

        oracle_answer = str(oracle_item.get("answer", "") or "").strip() if isinstance(oracle_item, dict) else ""
        oracle_question = str(oracle_item.get("question", "") or "").strip() if isinstance(oracle_item, dict) else ""
        oracle_answer_session_ids = (
            oracle_item.get("answer_session_ids") if isinstance(oracle_item, dict) else None
        )
        if not isinstance(oracle_answer_session_ids, list):
            oracle_answer_session_ids = []

        oracle_turns = _oracle_turn_evidence(oracle_item) if isinstance(oracle_item, dict) else []

        gold_turns: List[Dict[str, Any]] = []
        gold_segments: List[Dict[str, Any]] = []
        gold_mapping_issues: List[Dict[str, Any]] = []
        session_to_dialogue: Dict[str, str] = {}
        data_stem = ""

        if memory_root is not None:
            data_stem = _data_stem_from_memory_root(memory_root)
            session_to_dialogue = _build_session_to_dialogue_id(memory_root, qid=qid, data_stem=data_stem)
            gold_turns, gold_segments, gold_mapping_issues = _build_gold_segments(
                oracle_turns=oracle_turns,
                session_to_dialogue=session_to_dialogue,
                memory_root=memory_root,
            )

        facts_index: Dict[str, List[str]] = {}
        if memory_root is not None:
            facts_index = _build_segment_facts_index(memory_root, max_scene_files=int(args.max_scene_files or 0))
            _attach_facts(gold_segments, facts_index)

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
                    "facts": list(facts_index.get(ref, [])),
                }
            )

        gold_set = {str(x.get("episode_ref", "") or "").strip() for x in gold_segments if str(x.get("episode_ref", "") or "").strip()}
        retrieved_set = {str(x.get("episode_ref", "") or "").strip() for x in retrieved_segments if str(x.get("episode_ref", "") or "").strip()}

        hit = sorted(gold_set.intersection(retrieved_set))
        missed = sorted(gold_set.difference(retrieved_set))
        extra = sorted(retrieved_set.difference(gold_set))

        payload: Dict[str, Any] = {
            "question_id": qid,
            "test_id": test_id,
            "paths": {
                "recall_json": str(recall_json),
                "trace_json": str(out_path),
                "oracle_json": str(oracle_path),
                "memory_root": str(memory_root) if memory_root is not None else None,
                "memory_root_candidates": memory_candidates,
            },
            "oracle": {
                "question": oracle_question,
                "answer": oracle_answer,
                "answer_session_ids": list(oracle_answer_session_ids),
                "turn_evidence": gold_turns,
                "gold_segments": gold_segments,
                "mapping_issues": gold_mapping_issues,
                "unmapped_answer_sessions": [
                    sid for sid in oracle_answer_session_ids if str(sid) not in session_to_dialogue
                ]
                if session_to_dialogue and oracle_answer_session_ids
                else [],
            },
            "model_recall": {
                "hypothesis": "",
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

        # Best-effort hypothesis / answer strings for summary purposes.
        hyp = ""
        if isinstance(model_result, dict):
            g = model_result.get("gold_answer")
            a = model_result.get("answer")
            if isinstance(g, str) and g.strip():
                hyp = g.strip()
            elif isinstance(a, str) and a.strip():
                hyp = a.strip()
        payload["model_recall"]["hypothesis"] = hyp

        _write_json(out_path, payload)
        _write_jsonl_line(
            summary_path,
            {
                "question_id": qid,
                "test_id": test_id,
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

