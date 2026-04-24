#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build Neo4j entities + local entity profiles from episode segments (facts_only path).

Uses only segment-generated memory text (segment_memory_content / optional title).
Requires Neo4j; raises RuntimeError if unavailable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

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

logger = logging.getLogger(__name__)


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
    Run segment → entity → status → relations pipeline for facts_only workflows.

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
    processed = 0
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

    for job in jobs:
        key = job.segment_ref
        h = _segment_content_hash(job.situation_text)
        if not force_update and seg_map.get(key) == h:
            skipped += 1
            continue

        try:
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
                    mapped.append({"id": eid, "name": name, "type": surf["entity_type"]})
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
                memory_core._align_entity_library_with_kg(memory_core.entity_resolution_service)
                mapped.append({"id": eid, "name": name, "type": surf["entity_type"]})
                touched_entities.add(eid)

            id_set = {m["id"] for m in mapped}

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
                merged_attrs = merge_attribute_rows(list(ent.get("attributes") or []), enriched)
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
        except Exception as exc:
            logger.exception("Segment entity build failed for %s", key)
            errors.append(f"{key}:{exc!s}")

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
        "touched_entities": sorted(touched_entities),
        "errors": errors,
    }
