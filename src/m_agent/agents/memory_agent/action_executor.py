from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple, TypedDict

from .action_planner import (
    ACTION_ENTITY_EVENT_SEARCH,
    ACTION_ENTITY_FEATURE_SEARCH,
    ACTION_EVENT_DETAIL_MULTI_ROUTE_RECALL,
    ACTION_EVENT_DETAIL_RECALL,
    ACTION_EVENT_TIME_RECALL,
    ACTION_RECALL_REMEDY_MULTI_ROUTE,
    MemoryAction,
)
from .workspace import WorkspaceDocument, build_episode_ref, split_episode_ref


class ActionExecutionReport(TypedDict):
    round_id: int
    action_results: List[Dict[str, Any]]
    episode_refs: List[str]
    evidences: List[WorkspaceDocument]


# ---------------------------------------------------------------------------
# Rendering: convert heterogeneous search results into structured text
# ---------------------------------------------------------------------------

def render_episode_content(
    ref: str,
    content_data: Dict[str, Any],
    facts: List[str],
    *,
    max_turns: int | None = None,
) -> str:
    """Render episode content (from search_contents + accumulated facts) into
    a structured text block suitable for LLM consumption.

    ``max_turns``:
    - ``None`` (default): render **all** turns in ``content_data`` (no omission).
      Truncating here previously defaulted to 6 turns and caused false
      "evidence missing" when an answer appeared after the cut (e.g. Q in turn 6,
      A in turn 7).
    - Positive int: cap display to that many turns (legacy / token budgeting).
    """
    parts: List[str] = []

    raw_event_time = content_data.get("event_time")
    if not isinstance(raw_event_time, dict):
        raw_event_time = content_data.get("turn_time_span", {})
    time_str = _format_time_range(raw_event_time)
    if time_str:
        parts.append(f"【对话发生时间 Dialogue time】{time_str}")

    event_info = content_data.get("event_info")
    if isinstance(event_info, dict):
        theme = str(event_info.get("scene_theme", "") or "").strip()
        if theme:
            parts.append(f"【场景主题 Scene theme】{theme}")

    raw_participants = content_data.get("participants")
    if isinstance(raw_participants, list) and raw_participants:
        names = [str(p).strip() for p in raw_participants if str(p).strip()]
        if names:
            parts.append(f"【参与者 Participants】{', '.join(names)}")

    clean_facts = _dedupe_strings(facts)
    if clean_facts:
        parts.append("【相关事实 Related facts】")
        for fact in clean_facts:
            parts.append(f"  - {fact}")

    turns = content_data.get("turns") if isinstance(content_data.get("turns"), list) else []
    turn_lines: List[str] = []
    if max_turns is None:
        shown = turns
        omitted_count = 0
    else:
        safe_max = max(1, int(max_turns))
        shown = turns[:safe_max]
        omitted_count = len(turns) - safe_max if len(turns) > safe_max else 0
    for turn in shown:
        line = _format_turn_line(turn)
        if line:
            turn_lines.append(line)
    if omitted_count > 0:
        turn_lines.append(f"  ... ({omitted_count} more turns omitted)")

    if turn_lines:
        parts.append("【对话内容 Dialogue】")
        parts.extend(turn_lines)
    elif not clean_facts:
        parts.append("【对话内容 Dialogue】(无内容)")

    return "\n".join(parts)


def render_time_scene_content(scene_item: Dict[str, Any]) -> str:
    """Render a time-search scene result into structured text."""
    parts: List[str] = []

    start = str(scene_item.get("starttime", "") or "").strip()
    end = str(scene_item.get("endtime", "") or "").strip()
    if start and end and start != end:
        parts.append(f"【时间范围 Time range】{start} ~ {end}")
    elif start or end:
        parts.append(f"【时间范围 Time range】{start or end}")

    theme = str(scene_item.get("theme", "") or "").strip()
    if theme:
        parts.append(f"【场景主题 Scene theme】{theme}")

    if not parts:
        scene_id = str(scene_item.get("scene_id", "") or "").strip()
        parts.append(f"【场景 Scene】{scene_id}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _format_time_range(event_time: Dict[str, str] | None) -> str:
    if not isinstance(event_time, dict):
        return ""
    start = str(event_time.get("start_time", "") or "").strip()
    end = str(event_time.get("end_time", "") or "").strip()
    if start and end and start != end:
        return f"{start} ~ {end}"
    return start or end or ""


def _format_turn_line(turn: Dict[str, Any]) -> str:
    if not isinstance(turn, dict):
        return ""
    speaker = str(turn.get("speaker", "") or turn.get("role", "") or "").strip()
    text = str(
        turn.get("text", "")
        or turn.get("content", "")
        or turn.get("utterance", "")
        or ""
    ).strip()
    if not text:
        return ""
    if speaker:
        return f"  {speaker}: {text}"
    return f"  {text}"


def _dedupe_strings(items: List[str] | None) -> List[str]:
    if not items:
        return []
    seen: set[str] = set()
    result: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------

def execute_actions(
    actions: List[MemoryAction],
    *,
    round_id: int,
    search_details: Callable[[str, int], Dict[str, Any]],
    search_details_multi_route: Callable[[str, int], Dict[str, Any]],
    search_events_by_time_range: Callable[[str, str], List[Dict[str, Any]]],
    search_contents_by_episode_refs: Callable[[List[Dict[str, str]]], List[Dict[str, Any]]],
    search_entity_feature: Callable[[str, str, int], Dict[str, Any]] | None = None,
    search_entity_event: Callable[[str, str, int], Dict[str, Any]] | None = None,
    max_episode_candidates: int,
) -> ActionExecutionReport:
    action_results: List[Dict[str, Any]] = []
    ref_scores: Dict[str, float] = {}
    ref_sources: Dict[str, str] = {}
    ref_facts: Dict[str, List[str]] = {}
    time_documents: List[WorkspaceDocument] = []

    for action in actions:
        action_type = str(action.get("action_type", "")).strip()
        query = action.get("query", {})
        if not isinstance(query, dict):
            query = {}
        action_id = str(action.get("action_id", "")).strip()

        if action_type == ACTION_EVENT_DETAIL_RECALL:
            detail_query = str(query.get("detail_query", "")).strip()
            topk = max(1, int(query.get("topk", 5)))
            result = search_details(detail_query, topk)
            action_results.append({"action_id": action_id, "action_type": action_type, "result": result})
            _merge_ref_scores(
                ref_scores,
                ref_sources,
                ref_facts,
                action_id,
                _extract_refs_from_detail_result(result),
            )
            continue

        if action_type in (ACTION_EVENT_DETAIL_MULTI_ROUTE_RECALL, ACTION_RECALL_REMEDY_MULTI_ROUTE):
            detail_query = str(query.get("detail_query", "")).strip()
            topk = max(1, int(query.get("topk", 5)))
            result = search_details_multi_route(detail_query, topk)
            action_results.append({"action_id": action_id, "action_type": action_type, "result": result})
            _merge_ref_scores(
                ref_scores,
                ref_sources,
                ref_facts,
                action_id,
                _extract_refs_from_detail_result(result),
            )
            continue

        if action_type == ACTION_EVENT_TIME_RECALL:
            start_time = str(query.get("start_time", "")).strip()
            end_time = str(query.get("end_time", "")).strip()
            result = search_events_by_time_range(start_time, end_time)
            action_results.append({"action_id": action_id, "action_type": action_type, "result": result})
            if isinstance(result, list):
                for scene_item in result:
                    if not isinstance(scene_item, dict):
                        continue
                    scene_id = str(scene_item.get("scene_id", "") or "").strip()
                    if not scene_id:
                        continue
                    eid = f"time:{scene_id}"
                    content = render_time_scene_content(scene_item)
                    time_documents.append({
                        "evidence_id": eid,
                        "source_type": "time_scene",
                        "content": content,
                        "source_action_id": action_id,
                        "recall_score": None,
                        "rerank_score": None,
                        "meta": {
                            "scene_id": scene_id,
                            # Keep a small structured payload for downstream debugging.
                            "scene": scene_item,
                        },
                    })
            continue

        if action_type == ACTION_ENTITY_FEATURE_SEARCH and search_entity_feature is not None:
            entity_id = str(query.get("entity_id", "")).strip()
            feature_query = str(query.get("feature_query", "")).strip()
            topk = max(1, int(query.get("topk", 5)))
            result = search_entity_feature(entity_id, feature_query, topk)
            action_results.append({"action_id": action_id, "action_type": action_type, "result": result})
            _merge_ref_scores(
                ref_scores, ref_sources, ref_facts, action_id,
                _extract_refs_from_detail_result(result),
            )
            continue

        if action_type == ACTION_ENTITY_EVENT_SEARCH and search_entity_event is not None:
            entity_id = str(query.get("entity_id", "")).strip()
            event_query = str(query.get("event_query", "")).strip()
            topk = max(1, int(query.get("topk", 5)))
            result = search_entity_event(entity_id, event_query, topk)
            action_results.append({"action_id": action_id, "action_type": action_type, "result": result})
            _merge_ref_scores(
                ref_scores, ref_sources, ref_facts, action_id,
                _extract_refs_from_detail_result(result),
            )
            continue

        action_results.append(
            {
                "action_id": action_id,
                "action_type": action_type,
                "result": {"hit": False, "error": f"unsupported_action:{action_type}"},
            }
        )

    # --- Build episode-based documents ---
    ranked_refs = sorted(ref_scores.items(), key=lambda item: item[1], reverse=True)
    selected_refs = [ref for ref, _ in ranked_refs[: max(1, int(max_episode_candidates))]]
    ref_payload: List[Dict[str, str]] = []
    for ref in selected_refs:
        if ":" not in ref:
            continue
        dialogue_id, episode_id, segment_id = split_episode_ref(ref)
        if not dialogue_id or not episode_id:
            continue
        entry: Dict[str, str] = {
            "dialogue_id": dialogue_id,
            "episode_id": episode_id,
        }
        if segment_id:
            entry["segment_id"] = segment_id
        ref_payload.append(entry)
    episode_results = search_contents_by_episode_refs(ref_payload) if ref_payload else []

    hit_by_ref: Dict[str, Dict[str, Any]] = {}
    for item in episode_results:
        if not isinstance(item, dict):
            continue
        dialogue_id = str(item.get("dialogue_id", "")).strip()
        episode_id = str(item.get("episode_id", "")).strip()
        segment_id = str(item.get("segment_id", "")).strip() or None
        ref = build_episode_ref(dialogue_id, episode_id, segment_id)
        if not ref:
            continue
        hit_by_ref[ref] = item

    evidences: List[WorkspaceDocument] = []
    for ref in selected_refs:
        content_data = hit_by_ref.get(ref, {})
        dlg_id, ep_id, seg_id = split_episode_ref(ref)
        facts = _merge_fact_texts(
            ref_facts.get(ref),
            content_data.get("facts"),
        )
        rendered = render_episode_content(ref, content_data, facts)
        meta: Dict[str, Any] = {
            "dialogue_id": dlg_id,
            "episode_id": ep_id,
            "segment_id": (seg_id.strip() or None),
        }
        if facts:
            meta["facts"] = list(facts)
        if "turn_span" in content_data:
            meta["turn_span"] = content_data.get("turn_span")
        if "turns" in content_data:
            meta["turns"] = content_data.get("turns")

        doc: WorkspaceDocument = {
            "evidence_id": ref,
            "source_type": "episode",
            "content": rendered,
            "source_action_id": ref_sources.get(ref, ""),
            "recall_score": float(ref_scores.get(ref, 0.0)),
            "rerank_score": None,
            "meta": meta,
        }
        evidences.append(doc)

    evidences.extend(time_documents)

    return {
        "round_id": max(1, int(round_id)),
        "action_results": action_results,
        "episode_refs": selected_refs,
        "evidences": evidences,
    }


# ---------------------------------------------------------------------------
# Detail result extraction helpers
# ---------------------------------------------------------------------------

def _extract_refs_from_detail_result(payload: Dict[str, Any]) -> List[Tuple[str, float, str]]:
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if not isinstance(results, list):
        return []

    refs: List[Tuple[str, float, str]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence")
        if not isinstance(evidence, dict):
            continue
        dialogue_id = str(evidence.get("dialogue_id", "")).strip()
        episode_id = str(evidence.get("episode_id", "")).strip()
        segment_id = str(evidence.get("segment_id", "")).strip() or None
        ref = build_episode_ref(dialogue_id, episode_id, segment_id)
        if not ref:
            continue
        score_raw = item.get("similarity")
        try:
            score = float(score_raw)
        except Exception:
            score = 0.0
        refs.append((ref, score, _extract_fact_text(item, evidence)))
    return refs


def _merge_ref_scores(
    ref_scores: Dict[str, float],
    ref_sources: Dict[str, str],
    ref_facts: Dict[str, List[str]],
    source_action_id: str,
    refs_with_scores: List[Tuple[str, float, str]],
) -> None:
    for ref, score, fact_text in refs_with_scores:
        current = ref_scores.get(ref)
        if current is None or score > current:
            ref_scores[ref] = float(score)
            ref_sources[ref] = source_action_id
        if fact_text:
            bucket = ref_facts.setdefault(ref, [])
            if fact_text not in bucket:
                bucket.append(fact_text)


def _extract_fact_text(item: Dict[str, Any], evidence: Dict[str, Any]) -> str:
    item_keys = (
        "Atomic fact",
        "atomic_fact",
        "atomic fact",
        "Atomic_fact",
        "fact",
        "fact_text",
        "snippet",
        "text",
        "evidence_sentence",
    )
    for key in item_keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    evidence_keys = ("fact", "fact_text", "snippet", "text")
    for key in evidence_keys:
        value = evidence.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _merge_fact_texts(*groups: Any) -> List[str]:
    merged: List[str] = []
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = ""
                for key in ("text", "content", "fact", "sentence"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        text = value.strip()
                        break
            else:
                text = ""
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
    return merged
