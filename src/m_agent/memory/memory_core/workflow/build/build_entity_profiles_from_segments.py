#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build Neo4j entities + local entity profiles from episode segments.

Uses only segment-generated memory text (segment_memory_content / optional title).
Requires Neo4j; raises RuntimeError if unavailable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from tqdm import tqdm

from m_agent.memory.memory_core.core.neo4j_require import require_neo4j_for_segment_entity_build
from m_agent.memory.memory_core.services_bank.entity_profile_sys.library import (
    AttributeEntry,
    EntityProfileRecord,
    EvidenceRef,
    EventEntry,
)
from m_agent.memory.memory_core.workflow.build.entity_segment_llm import (
    call_llm_json,
    load_entity_segment_prompts,
)
from m_agent.memory.memory_core.workflow.build.entity_segment_merge import (
    build_profile_summary_value,
    canonical_field_group,
    merge_attribute_rows,
)
from m_agent.memory.memory_core.workflow.search.entity_search import resolve_entity_id
from m_agent.prompt_utils import render_prompt_template

logger = logging.getLogger(__name__)

# Enable verbose per-segment logs when debugging behavior.
_SEGMENT_DEBUG = str(os.getenv("M_AGENT_SEGMENT_DEBUG", "")).strip().lower() in {"1", "true", "yes", "on"}
try:
    _SEGMENT_DEBUG_LEVEL = max(0, int(str(os.getenv("M_AGENT_SEGMENT_DEBUG_LEVEL", "1")).strip() or "1"))
except Exception:
    _SEGMENT_DEBUG_LEVEL = 1


@contextmanager
def _noop_ctx() -> Iterator[None]:
    yield


def _dbg_write(msg: str, *args: Any) -> None:
    """
    Debug-friendly writer that does not break tqdm bars.
    Falls back to logger.info when tqdm.write is unavailable.
    """
    if not _SEGMENT_DEBUG:
        return
    try:
        text = msg % args if args else msg
    except Exception:
        text = f"{msg} {args}"
    try:
        tqdm.write(text)
    except Exception:
        logger.info(text)

# Segment 构建时压低 INFO，避免刷屏：事件总线、逐实体 embedding、Neo4j 通知、profile 落盘等。
_SEGMENT_BUILD_QUIET_LOGGERS: tuple[str, ...] = (
    "m_agent.memory.memory_core.services_bank.entity_resolution.service",
    "m_agent.memory.memory_core.services_bank.entity_resolution.library",
    "m_agent.memory.memory_core.services_bank.entity_profile_sys.library",
    "m_agent.memory.memory_core.services_bank.entity_profile_sys.service",
    "m_agent.memory.memory_core.system.event_bus",
    "m_agent.memory.memory_core.system",
    "m_agent.memory.memory_core.core.kg_base",
    "neo4j",
    "neo4j.io",
    "neo4j.notifications",
)


@contextmanager
def _quiet_segment_entity_infra_loggers() -> Iterator[None]:
    prev: Dict[str, int] = {}
    for name in _SEGMENT_BUILD_QUIET_LOGGERS:
        log = logging.getLogger(name)
        prev[name] = log.level
        log.setLevel(logging.WARNING)
    try:
        yield
    finally:
        for name in _SEGMENT_BUILD_QUIET_LOGGERS:
            logging.getLogger(name).setLevel(prev.get(name, logging.NOTSET))


@dataclass
class SegmentJob:
    dialogue_id: str
    episode_id: str
    segment_id: str
    situation_text: str
    segment_ref: str


def _segment_content_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _collect_episode_files(episodes_root: Path) -> List[Path]:
    by_dialogue = episodes_root / "by_dialogue"
    if by_dialogue.is_dir():
        files = sorted(by_dialogue.rglob("episodes_*.json"))
        if files:
            return files
    return sorted(episodes_root.rglob("episodes_*.json"))


def _build_situation_text(segment: Dict[str, Any]) -> str:
    title = str(segment.get("segment_memory_title") or "").strip()
    content = str(segment.get("segment_memory_content") or "").strip()
    if title and content:
        return f"{title}\n\n{content}".strip()
    return content or title


def iter_segment_jobs(episodes_root: Path) -> List[SegmentJob]:
    jobs: List[SegmentJob] = []
    if not episodes_root.exists():
        return jobs
    for path in _collect_episode_files(episodes_root):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.warning("Skip episode file %s: %s", path, exc)
            continue
        if not isinstance(data, dict):
            continue
        dialogue_id = str(data.get("dialogue_id") or "").strip()
        episodes = data.get("episodes")
        if not isinstance(episodes, list):
            continue
        for ep in episodes:
            if not isinstance(ep, dict):
                continue
            episode_id = str(ep.get("episode_id") or "").strip()
            segments = ep.get("segments")
            if not isinstance(segments, list):
                continue
            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                segment_id = str(seg.get("segment_id") or "").strip()
                situation = _build_situation_text(seg)
                if not situation:
                    logger.warning(
                        "Skip segment without segment_memory_content: %s %s %s",
                        dialogue_id,
                        episode_id,
                        segment_id or "?",
                    )
                    continue
                if not dialogue_id or not episode_id or not segment_id:
                    logger.warning("Skip segment with missing ids in %s", path)
                    continue
                seg_ref = f"{dialogue_id}:{episode_id}:{segment_id}"
                jobs.append(
                    SegmentJob(
                        dialogue_id=dialogue_id,
                        episode_id=episode_id,
                        segment_id=segment_id,
                        situation_text=situation,
                        segment_ref=seg_ref,
                    )
                )
    return jobs


def _new_entity_id() -> str:
    return f"ent_{uuid.uuid4().hex[:16]}"


# Intra-segment duplicate merge: embedding auto-merge threshold vs LLM gate band.
_INTRASEG_EMB_AUTO_MERGE = 0.965
_INTRASEG_EMB_LLM_LOW = 0.78


def _safe_embed_vec(embed_func: Any, text: str) -> Optional[List[float]]:
    try:
        v = embed_func(str(text or "").strip())
        return v if isinstance(v, list) else None
    except Exception:
        return None


def _cos_sim_vec(a: Optional[List[float]], b: Optional[List[float]]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


def _llm_same_entity_pair(
    llm: Callable[[str], str],
    *,
    prompt_language: str,
    situation: str,
    name_a: str,
    name_b: str,
) -> bool:
    sit = situation[:2000] if len(situation) > 2000 else situation
    lang = str(prompt_language or "").lower()
    if lang.startswith("zh"):
        prompt = (
            "你是实体对齐助手。判断以下两个名称是否指**同一现实世界的对象**（同一人/同一组织/同一地点等）。\n\n"
            f"情景文本（节选）：\n{sit}\n\n"
            f"名称A：{name_a}\n名称B：{name_b}\n\n"
            '只输出 JSON：{"same_entity": true} 或 {"same_entity": false}，不要其它文字。'
        )
    else:
        prompt = (
            "Decide if the two names refer to the same real-world entity "
            "(same person, organization, location, etc.).\n\n"
            f"Situation (excerpt):\n{sit}\n\n"
            f"Name A: {name_a}\nName B: {name_b}\n\n"
            'Output JSON only: {"same_entity": true} or {"same_entity": false}.'
        )
    d = call_llm_json(llm, prompt, context="entity_segment_intrasegment_same_entity")
    if not isinstance(d, dict):
        return False
    v = d.get("same_entity")
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"true", "1", "yes"}


def _dedupe_mapped_rows(mapped: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """One row per entity id (first occurrence wins for display name)."""
    by_id: Dict[str, Dict[str, str]] = {}
    order: List[str] = []
    for m in mapped:
        i = str(m.get("id") or "").strip()
        if not i:
            continue
        if i not in by_id:
            by_id[i] = {
                "id": i,
                "name": str(m.get("name") or "").strip() or i,
                "type": str(m.get("type") or "other"),
                "origin": str(m.get("origin") or "").strip(),
            }
            order.append(i)
    return [by_id[i] for i in order]


def _merge_intrasegment_duplicate_entities(
    *,
    memory_core: Any,
    kg: Any,
    mapped: List[Dict[str, str]],
    segment_ref: str,
    situation_text: str,
    llm: Callable[[str], str],
    embed: Any,
    touched_entities: Set[str],
) -> int:
    """
    Before Step2, merge duplicate KG entities introduced within this segment.

    Uses name equality, high embedding similarity, or LLM in the mid band.
    Refreshes EntityLibrary from KG when any merge succeeds (cheap vs stale ids).
    """
    if len(mapped) < 2:
        mapped[:] = _dedupe_mapped_rows(mapped)
        _dbg_write("Intraseg check: segment=%s entities=%s (skip: <2)", segment_ref, len(mapped))
        return 0

    mapped[:] = _dedupe_mapped_rows(mapped)
    ids = [str(m["id"]).strip() for m in mapped if str(m.get("id") or "").strip()]
    if len(ids) < 2:
        _dbg_write("Intraseg check: segment=%s entities=%s (skip: <2 ids)", segment_ref, len(mapped))
        return 0

    uniq: Dict[str, Dict[str, str]] = {str(m["id"]): dict(m) for m in mapped}

    def name_for(eid: str) -> str:
        return str(uniq[eid].get("name") or eid).strip() or eid

    _dbg_write("Intraseg check: segment=%s entities=%s", segment_ref, len(ids))
    if _SEGMENT_DEBUG and _SEGMENT_DEBUG_LEVEL >= 2:
        rows = [
            f"{m.get('origin','?')}:{m.get('type','?')}:{m.get('name','?')}->{m.get('id','?')}"
            for m in mapped
        ]
        _dbg_write("Intraseg entities: %s", rows)

    edges: List[Tuple[str, str]] = []
    edge_reasons: Dict[Tuple[str, str], Dict[str, Any]] = {}
    sims: List[float] = []
    llm_calls = 0
    skipped_low = 0
    auto_edges = 0
    name_equal_edges = 0
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            ida, idb = ids[a], ids[b]
            if ida == idb:
                continue
            na = name_for(ida).lower().strip()
            nb = name_for(idb).lower().strip()
            if na and na == nb:
                edges.append((ida, idb))
                edge_reasons[(ida, idb)] = {"reason": "name_equal", "sim": 1.0}
                name_equal_edges += 1
                continue
            va = _safe_embed_vec(embed, name_for(ida))
            vb = _safe_embed_vec(embed, name_for(idb))
            sim = _cos_sim_vec(va, vb) if va and vb else 0.0
            sims.append(sim)
            if sim >= _INTRASEG_EMB_AUTO_MERGE:
                edges.append((ida, idb))
                edge_reasons[(ida, idb)] = {"reason": "emb_auto", "sim": sim}
                auto_edges += 1
                continue
            if sim < _INTRASEG_EMB_LLM_LOW:
                skipped_low += 1
                continue
            llm_calls += 1
            if _llm_same_entity_pair(
                llm,
                prompt_language=str(getattr(memory_core, "prompt_language", "zh")),
                situation=situation_text,
                name_a=name_for(ida),
                name_b=name_for(idb),
            ):
                edges.append((ida, idb))
                edge_reasons[(ida, idb)] = {"reason": "llm_same_entity", "sim": sim}

    if _SEGMENT_DEBUG:
        max_sim = max(sims) if sims else 0.0
        over_low = sum(1 for s in sims if s >= _INTRASEG_EMB_LLM_LOW)
        over_auto = sum(1 for s in sims if s >= _INTRASEG_EMB_AUTO_MERGE)
        _dbg_write(
            "Intraseg check: segment=%s pairs=%s max_sim=%.3f edges=%s (name_eq=%s auto=%s llm=%s)",
            segment_ref,
            (len(ids) * (len(ids) - 1)) // 2,
            max_sim,
            len(edges),
            name_equal_edges,
            auto_edges,
            llm_calls,
        )
        if edge_reasons and _SEGMENT_DEBUG_LEVEL >= 2:
            preview = list(edge_reasons.items())[:10]
            _dbg_write("Intraseg check edges (preview): %s", preview)

    if not edges:
        return 0

    adj: Dict[str, Set[str]] = {i: set() for i in ids}
    for u, v in edges:
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set()).add(u)

    seen: Set[str] = set()
    merge_count = 0

    for start in ids:
        if start in seen:
            continue
        stack = [start]
        comp: List[str] = []
        seen.add(start)
        while stack:
            x = stack.pop()
            comp.append(x)
            for y in adj.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        if len(comp) < 2:
            continue
        root = min(comp)
        for src in sorted(comp):
            if src == root:
                continue
            mr = kg.merge_entities(target_id=root, source_id=src)
            if not mr.get("success"):
                logger.debug(
                    "Intrasegment merge skipped %s <- %s: %s",
                    root,
                    src,
                    mr.get("details"),
                )
                continue
            merge_count += 1
            _dbg_write(
                "Intraseg merge: %s <- %s (segment=%s) name_root=%r name_src=%r",
                root,
                src,
                segment_ref,
                name_for(root),
                name_for(src),
            )
            for row in mapped:
                if row.get("id") == src:
                    row["id"] = root
            touched_entities.discard(src)
            touched_entities.add(root)
            if src in uniq:
                uniq.pop(src, None)

    if merge_count > 0:
        mapped[:] = _dedupe_mapped_rows(mapped)
        try:
            memory_core._align_entity_library_with_kg(memory_core.entity_resolution_service)
        except Exception as exc:
            logger.warning("Intrasegment merge: library KG align failed: %s", exc)
        _dbg_write("Intrasegment merges=%s segment=%s", merge_count, segment_ref)
        if _SEGMENT_DEBUG and edge_reasons:
            preview = list(edge_reasons.items())[:8]
            _dbg_write("Intraseg merge evidence (preview): %s", preview)

    return merge_count


def _slot_key_for_log(row: Dict[str, Any]) -> str:
    fc = row.get("field_canonical")
    if isinstance(fc, list) and fc:
        return str(fc[0] or "").strip().lower()
    return str(row.get("field") or "").strip().lower()


def _summarize_attr_merge_for_log(
    before: List[Dict[str, Any]],
    after: List[Dict[str, Any]],
) -> Dict[str, Any]:
    b = { _slot_key_for_log(x): x for x in before if isinstance(x, dict) and _slot_key_for_log(x) }
    a = { _slot_key_for_log(x): x for x in after if isinstance(x, dict) and _slot_key_for_log(x) }
    added = sorted([k for k in a.keys() if k not in b])
    removed = sorted([k for k in b.keys() if k not in a])
    changed: List[Dict[str, Any]] = []
    for k in sorted(set(a.keys()) & set(b.keys())):
        bm = str(b[k].get("update_mode") or "")
        am = str(a[k].get("update_mode") or "")
        bv = b[k].get("value") if isinstance(b[k].get("value"), list) else []
        av = a[k].get("value") if isinstance(a[k].get("value"), list) else []
        if bm != am or len(bv) != len(av) or set(map(str, bv)) != set(map(str, av)):
            changed.append(
                {
                    "field": k,
                    "mode_before": bm,
                    "mode_after": am,
                    "len_before": len(bv),
                    "len_after": len(av),
                }
            )
    return {"added": added, "removed": removed, "changed": changed}


def _allowed_type(raw: str) -> str:
    t = str(raw or "").strip().lower()
    allowed = {"person", "organization", "location", "product", "event", "other"}
    return t if t in allowed else "other"


def _enrich_status_rows(
    rows: List[Dict[str, Any]],
    *,
    segment_ref: str,
    dialogue_id: str,
    episode_id: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        field = str(row.get("field") or "").strip()
        if not field:
            continue
        mode = str(row.get("update_mode") or "append").strip().lower()
        if mode not in {"replace", "append"}:
            mode = "append"
        vals = row.get("value")
        if isinstance(vals, str):
            vals = [vals] if vals.strip() else []
        elif not isinstance(vals, list):
            vals = []
        vals = [str(x).strip() for x in vals if str(x).strip()]
        fc = canonical_field_group(field)
        ev = [
            {
                "segment_ref": segment_ref,
                "dialogue_id": dialogue_id,
                "episode_id": episode_id,
            }
        ]
        out.append(
            {
                "field": fc[0] if fc else field,
                "update_mode": mode,
                "field_canonical": fc,
                "value": vals,
                "evidence_refs": ev,
            }
        )
    return out


def _append_segment_event(
    metadata: Dict[str, Any],
    *,
    summary: str,
    segment_ref: str,
) -> Dict[str, Any]:
    meta = dict(metadata or {})
    evs = meta.get("segment_events")
    if not isinstance(evs, list):
        evs = []
    s = str(summary or "").strip()
    if s:
        evs.append({"summary": s, "segment_ref": segment_ref})
    meta["segment_events"] = evs
    return meta


def _sync_library_from_kg_attrs(
    memory_core: Any,
    entity_id: str,
    *,
    embed_func: Optional[Callable[[str], List[float]]],
) -> None:
    ok, ent = memory_core.kg_base.get_entity(entity_id)
    if not ok or not ent:
        return
    lib = memory_core.entity_profile_service.entity_profile_library
    record = lib.ensure_entity(entity_id)
    record.metadata = dict(ent.get("metadata") or {})
    if isinstance(record.metadata, dict):
        record.metadata.setdefault("schema_version", "v1")
    attrs_raw = ent.get("attributes") or []
    record.attributes = []
    for row in attrs_raw if isinstance(attrs_raw, list) else []:
        if not isinstance(row, dict):
            continue
        vals = row.get("value")
        if isinstance(vals, str):
            content = [vals] if vals.strip() else []
        elif isinstance(vals, list):
            content = [str(x).strip() for x in vals if str(x).strip()]
        else:
            content = []
        field = str(row.get("field") or "").strip()
        ev_refs: List[EvidenceRef] = []
        for er in row.get("evidence_refs") or []:
            if isinstance(er, dict):
                dlg = str(er.get("dialogue_id") or "").strip()
                ep = str(er.get("episode_id") or "").strip()
                seg = ""
                sr = str(er.get("segment_ref") or "").strip()
                if sr.count(":") >= 2:
                    parts = sr.split(":", 2)
                    dlg = dlg or parts[0]
                    ep = ep or parts[1]
                    seg = parts[2] or ""
                ev_refs.append(
                    EvidenceRef(
                        dialogue_id=dlg,
                        episode_id=ep,
                        scene_id=seg,
                    )
                )
            elif isinstance(er, str) and er.strip():
                ev_refs.append(EvidenceRef(scene_id=er.strip()))
        md = {
            "update_mode": row.get("update_mode"),
            "field_canonical": row.get("field_canonical"),
        }
        text = f"{field}: {', '.join(content)}"
        emb = None
        if embed_func and text.strip():
            try:
                emb = embed_func(text)
            except Exception:
                emb = None
        record.attributes.append(
            AttributeEntry(
                field=field,
                content=content,
                text=text,
                embedding=emb if isinstance(emb, list) else None,
                sources=ev_refs,
                metadata=md,
            )
        )
    # events from metadata segment_events -> EventEntry (minimal)
    meta = ent.get("metadata") or {}
    sev = meta.get("segment_events") if isinstance(meta, dict) else None
    record.events = []
    if isinstance(sev, list):
        for item in sev:
            if not isinstance(item, dict):
                continue
            summ = str(item.get("summary") or "").strip()
            if not summ:
                continue
            ref = str(item.get("segment_ref") or "").strip()
            record.events.append(
                EventEntry(
                    event=summ,
                    text=summ,
                    sources=[EvidenceRef(scene_id=ref)] if ref else [],
                )
            )
    record.summary = build_profile_summary_value(
        canonical_name=str(ent.get("name") or entity_id),
        entity_type=str(ent.get("type") or "other"),
        attributes=attrs_raw if isinstance(attrs_raw, list) else [],
    )
    record.touch()
    lib.save_to_path(str(memory_core.entity_profile_data_path))


def build_entity_profiles_from_segments(
    memory_core: Any,
    *,
    force_update: bool = False,
    progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """
    Run segment → entity → status → relations pipeline after scene/facts extraction.

    Raises:
        RuntimeError: Neo4j unavailable or connectivity failure.
        ValueError: Missing prompts.
    """
    kg = memory_core.kg_base
    require_neo4j_for_segment_entity_build(kg.database)

    prompts = load_entity_segment_prompts(
        runtime_prompt_config_path=memory_core.runtime_prompt_config_path,
        prompt_language=memory_core.prompt_language,
    )

    state_path = Path(memory_core.local_store_dir) / "entity_segment_build_state.json"
    state: Dict[str, Any] = {}
    if state_path.exists():
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
    if not isinstance(state, dict):
        state = {}
    seg_map = state.get("segments")
    if not isinstance(seg_map, dict):
        seg_map = {}

    jobs = iter_segment_jobs(Path(memory_core.episodes_dir))
    llm = memory_core.llm_func
    embed = memory_core.embed_func

    touched_entities: Set[str] = set()
    merge_pass_result: Dict[str, Any] = {}
    processed = 0
    intrasegment_entity_merges = 0
    skipped = 0
    errors: List[str] = []

    def emit(event: str, payload: Dict[str, Any]) -> None:
        if progress_callback:
            try:
                progress_callback(event, payload)
            except Exception:
                logger.exception("progress_callback failed")

    emit(
        "flush_stage",
        {"stage": "build_entity_profiles_from_segments", "status": "started", "segment_count": len(jobs)},
    )

    # Always quiet infra logs so segment-level debug remains readable.
    ctx = _quiet_segment_entity_infra_loggers()
    with ctx:
        job_iter = tqdm(jobs, desc="Segment entity build", unit="seg", mininterval=0.5, ascii=True)
        for idx, job in enumerate(job_iter, start=1):
            key = job.segment_ref
            h = _segment_content_hash(job.situation_text)
            if not force_update and seg_map.get(key) == h:
                skipped += 1
                if idx % 50 == 0:
                    job_iter.set_postfix(processed=processed, skipped=skipped, merges=intrasegment_entity_merges)
                continue

            try:
                emit(
                    "segment_progress",
                    {"segment_ref": key, "index": idx, "total": len(jobs), "status": "started"},
                )
                # Step 1
                p1 = render_prompt_template(
                    prompts["step1_entities_prompt"],
                    {"<situation_text>": job.situation_text},
                )
                d1 = call_llm_json(llm, p1, context="entity_segment_step1")
                if not d1:
                    errors.append(f"step1_json_failed:{key}")
                    continue
                raw_entities = d1.get("entities")
                if not isinstance(raw_entities, list):
                    raw_entities = []
                surface_entities: List[Dict[str, str]] = []
                seen_names: Set[str] = set()
                for ent in raw_entities:
                    if not isinstance(ent, dict):
                        continue
                    name = str(ent.get("canonical_name") or "").strip()
                    if not name:
                        continue
                    nk = name.lower()
                    if nk in seen_names:
                        continue
                    seen_names.add(nk)
                    surface_entities.append(
                        {"canonical_name": name, "entity_type": _allowed_type(str(ent.get("entity_type") or ""))}
                    )

                mapped: List[Dict[str, str]] = []
                if not surface_entities:
                    seg_map[key] = h
                    state["segments"] = seg_map
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(state_path, "w", encoding="utf-8") as f:
                        json.dump(state, f, ensure_ascii=False, indent=2)
                    processed += 1
                    emit(
                        "segment_progress",
                        {"segment_ref": key, "index": idx, "total": len(jobs), "status": "completed", "entities": 0},
                    )
                    continue

                for surf in surface_entities:
                    name = surf["canonical_name"]
                    res = resolve_entity_id(
                        name,
                        memory_core.entity_resolution_service.entity_library,
                        llm_func=llm,
                        embed_func=embed,
                        max_candidates=max(3, int(memory_core.top_k)),
                        string_similarity_threshold=0.72,
                        embedding_similarity_threshold=max(0.45, float(memory_core.similarity_threshold) - 0.25),
                        prompt_language=memory_core.prompt_language,
                        runtime_prompt_config_path=memory_core.runtime_prompt_config_path,
                    )
                    if res.get("hit") and str(res.get("entity_id") or "").strip():
                        eid = str(res["entity_id"]).strip()
                        mapped.append({"id": eid, "name": name, "type": surf["entity_type"], "origin": "hit"})
                        if _SEGMENT_DEBUG:
                            _dbg_write("Resolved entity HIT: segment=%s name=%r -> %s", key, name, eid)
                        touched_entities.add(eid)
                        continue
                    eid = _new_entity_id()
                    add_res = kg.add_entity(
                        eid,
                        entity_type=surf["entity_type"],
                        entity_name=name,
                        source_info={
                            "segment_ref": job.segment_ref,
                            "dialogue_id": job.dialogue_id,
                            "episode_id": job.episode_id,
                            "origin": "segment_entity_build",
                        },
                    )
                    if not add_res.get("success"):
                        errors.append(f"add_entity_failed:{key}:{name}:{add_res}")
                        continue
                    mapped.append({"id": eid, "name": name, "type": surf["entity_type"], "origin": "new"})
                    if _SEGMENT_DEBUG:
                        _dbg_write("Resolved entity NEW: segment=%s name=%r -> %s", key, name, eid)
                    touched_entities.add(eid)

                intrasegment_entity_merges += _merge_intrasegment_duplicate_entities(
                    memory_core=memory_core,
                    kg=kg,
                    mapped=mapped,
                    segment_ref=key,
                    situation_text=job.situation_text,
                    llm=llm,
                    embed=embed,
                    touched_entities=touched_entities,
                )

                id_set = {m["id"] for m in mapped}
                job_iter.set_postfix(
                    processed=processed,
                    skipped=skipped,
                    entities=len(mapped),
                    merges=intrasegment_entity_merges,
                )

                # Step 2 per entity
                for m in mapped:
                    p2 = render_prompt_template(
                        prompts["step2_status_event_prompt"],
                        {
                            "<situation_text>": job.situation_text,
                            "<entity_name>": m["name"],
                            "<entity_type>": m["type"],
                        },
                    )
                    d2 = call_llm_json(llm, p2, context="entity_segment_step2")
                    if not d2:
                        errors.append(f"step2_json_failed:{key}:{m['id']}")
                        continue
                    statuses = d2.get("statuses")
                    if not isinstance(statuses, list):
                        statuses = []
                    event_summary = str(d2.get("event_summary") or "").strip()
                    enriched = _enrich_status_rows(
                        statuses,
                        segment_ref=job.segment_ref,
                        dialogue_id=job.dialogue_id,
                        episode_id=job.episode_id,
                    )
                    ok_g, ent = kg.get_entity(m["id"])
                    if not ok_g or not ent:
                        continue
                    before_attrs = list(ent.get("attributes") or [])
                    merged_attrs = merge_attribute_rows(before_attrs, enriched)
                    if _SEGMENT_DEBUG:
                        summary = _summarize_attr_merge_for_log(before_attrs, merged_attrs)
                        if summary["added"] or summary["removed"] or summary["changed"]:
                            add_s = ",".join(summary["added"]) if summary["added"] else "-"
                            rm_s = ",".join(summary["removed"]) if summary["removed"] else "-"
                            ch_s = ",".join([c.get("field", "?") for c in summary["changed"]]) if summary["changed"] else "-"
                            _dbg_write("Attr merge: seg=%s ent=%s(%s) +[%s] ~[%s] -[%s]", key, m["id"], m.get("name"), add_s, ch_s, rm_s)
                    meta = dict(ent.get("metadata") or {})
                    meta.setdefault("schema_version", "v1")
                    if event_summary:
                        meta = _append_segment_event(meta, summary=event_summary, segment_ref=job.segment_ref)
                    summ = build_profile_summary_value(
                        canonical_name=str(ent.get("name") or m["name"]),
                        entity_type=str(ent.get("type") or m["type"]),
                        attributes=merged_attrs,
                    )
                    meta["profile_summary"] = summ
                    kg.set_entity_document(
                        m["id"],
                        attributes=merged_attrs,
                        metadata=meta,
                        features=list(ent.get("features") or []),
                    )

                # Step relations (per segment)
                if len(mapped) >= 2:
                    lines = "\n".join(f"{x['id']} | {x['name']} | {x['type']}" for x in mapped)
                    p3 = render_prompt_template(
                        prompts["step_relations_prompt"],
                        {
                            "<situation_text>": job.situation_text,
                            "<entity_lines>": lines,
                        },
                    )
                    d3 = call_llm_json(llm, p3, context="entity_segment_relations")
                    rels = d3.get("relations") if isinstance(d3, dict) else None
                    if isinstance(rels, list):
                        for rel in rels:
                            if not isinstance(rel, dict):
                                continue
                            subj = str(rel.get("subject_id") or "").strip()
                            obj = str(rel.get("object_id") or "").strip()
                            rtype = str(rel.get("relation_type") or "").strip()
                            if not subj or not obj or not rtype:
                                continue
                            if subj not in id_set or obj not in id_set:
                                continue
                            kg.add_relation(
                                subj,
                                rtype,
                                obj,
                                source_info={"segment_ref": job.segment_ref},
                            )

                seg_map[key] = h
                state["segments"] = seg_map
                state_path.parent.mkdir(parents=True, exist_ok=True)
                with open(state_path, "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False, indent=2)
                processed += 1
                emit(
                    "segment_progress",
                    {
                        "segment_ref": key,
                        "index": idx,
                        "total": len(jobs),
                        "status": "completed",
                        "entities": len(mapped),
                        "intrasegment_merges_total": intrasegment_entity_merges,
                    },
                )
            except Exception as exc:
                logger.exception("Segment entity build failed for %s", key)
                errors.append(f"{key}:{exc!s}")

    # 全量对齐一次即可；勿在每实体 add 后调用 _align（会与 KG 名不一致触发反复 rebuild+全量 embed）。
    try:
        memory_core._align_entity_library_with_kg(memory_core.entity_resolution_service)
    except Exception as exc:
        logger.warning("Segment build: EntityLibrary KG align after loop failed: %s", exc)

    # 批量解析未解析实体 + 稳定合并写回 KG（warmup/segment 末尾统一触发；否则 EntityLibrary 长期 unresolved）
    try:
        merge_pass_result = memory_core.run_entity_resolution_pass()
    except Exception as exc:
        logger.exception("Segment build: entity resolution pass failed")
        errors.append(f"entity_resolution_pass:{exc!s}")
        merge_pass_result = {"success": False, "error": str(exc)}
    else:
        if isinstance(merge_pass_result, dict) and not merge_pass_result.get("success", True):
            logger.warning(
                "Segment build: entity resolution pass finished with merge_errors=%s",
                merge_pass_result.get("merge_errors", []),
            )
            errors.append("entity_resolution_pass_merge_errors")

    try:
        memory_core._align_entity_library_with_kg(memory_core.entity_resolution_service)
    except Exception as exc:
        logger.warning("Segment build: EntityLibrary KG re-align after merge failed: %s", exc)

    for eid in sorted(touched_entities):
        try:
            _sync_library_from_kg_attrs(memory_core, eid, embed_func=embed)
        except Exception as exc:
            logger.warning("Profile sync failed for %s: %s", eid, exc)

    emit(
        "flush_stage",
        {
            "stage": "build_entity_profiles_from_segments",
            "status": "completed",
            "processed_segments": processed,
            "skipped_segments": skipped,
            "errors": errors,
        },
    )
    return {
        "success": not errors,
        "processed_segments": processed,
        "skipped_segments": skipped,
        "intrasegment_entity_merges": intrasegment_entity_merges,
        "touched_entities": sorted(touched_entities),
        "errors": errors,
        "entity_resolution_pass": merge_pass_result,
    }
