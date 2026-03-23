#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entity profile service: build per-entity attributes/events from facts."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

try:
    import yaml  # type: ignore
except Exception:
    yaml = None

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

try:
    from .library import (
        AttributeEntry,
        EntityProfileLibrary,
        EntityProfileRecord,
        EvidenceRef,
        EventEntry,
        EventTimeRange,
    )
    from .strategies import EmbedThenLLMProfileMergeStrategy
except ImportError:
    from library import (
        AttributeEntry,
        EntityProfileLibrary,
        EntityProfileRecord,
        EvidenceRef,
        EventEntry,
        EventTimeRange,
    )
    from strategies import EmbedThenLLMProfileMergeStrategy

try:
    from memory.memory_core.services_bank.base_service import BaseService
    from memory.memory_core.system.event_types import EventType
except ImportError:
    import sys

    sys.path.append("..")
    from base_service import BaseService
    from system.event_types import EventType

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[4]


class EntityProfileService(BaseService):
    """Transform facts into entity profile attributes + timeline events."""

    def __init__(
        self,
        llm_func: Callable[[str], str],
        embed_func: Callable[[str], List[float]],
        memory_root: str,
        profile_data_path: Optional[str] = None,
        facts_situation_path: Optional[str] = None,
        prompt_path: Optional[str] = None,
        similarity_threshold: float = 0.78,
        top_k: int = 3,
        auto_merge_threshold: float = 0.93,
        enable_summary_llm: bool = True,
        enable_progress: bool = True,
        auto_align_on_init: bool = True,
        rebuild_checkpoint_every: int = 100,
    ):
        self.llm_func = llm_func
        self.embed_func = embed_func
        self.memory_root = Path(memory_root)
        self.workflow_id = self.memory_root.name
        self.enable_summary_llm = bool(enable_summary_llm)
        self.enable_progress = bool(enable_progress)

        self.local_store_dir = self.memory_root / "local_store"
        self.local_store_dir.mkdir(parents=True, exist_ok=True)
        self.profile_data_path = Path(profile_data_path) if profile_data_path else self.local_store_dir / "entity_profile"
        self.profile_data_path.mkdir(parents=True, exist_ok=True)
        self.rebuild_profile_checkpoint_path = self.local_store_dir / "entity_profile_rebuild_checkpoint"
        self.rebuild_checkpoint_every = max(1, int(rebuild_checkpoint_every or 100))

        self.local_facts_situation_file = (
            Path(facts_situation_path) if facts_situation_path else self.local_store_dir / "facts_situation.json"
        )
        self.master_facts_situation_file = self.memory_root / "facts_situation.json"
        self.facts_dir = self.memory_root / "facts"
        self.scene_dir = self.memory_root / "scene"

        self.prompt_path = (
            Path(prompt_path)
            if prompt_path
            else PROJECT_ROOT / "config" / "prompt" / "facts_filter.yaml"
        )
        self.facts_filter_prompt = self._load_fact_filter_prompt(self.prompt_path)

        self.entity_profile_library = EntityProfileLibrary(
            embed_func=embed_func,
            data_path=str(self.profile_data_path),
        )
        self.merge_strategy = EmbedThenLLMProfileMergeStrategy(
            llm_func=llm_func,
            embed_func=embed_func,
            similarity_threshold=similarity_threshold,
            top_k=top_k,
            auto_merge_threshold=auto_merge_threshold,
        )
        self._episode_time_cache: Optional[Dict[Tuple[str, str], Dict[str, str]]] = None

        if auto_align_on_init:
            self.align_with_master_facts(force_rebuild=False)

    # ------------------------------------------------------------------
    # EventBus contract
    # ------------------------------------------------------------------
    def get_subscribed_events(self):
        return [
            EventType.SYSTEM_INITIALIZED,
            EventType.ENTITY_ADDED,
            EventType.ENTITY_MERGED,
            EventType.ENTITY_RENAMED,
            EventType.ENTITY_DELETED,
        ]

    def handle_event(self, event_type: str, payload: dict) -> None:
        self._log_event_handling(event_type, payload)
        if event_type == EventType.SYSTEM_INITIALIZED:
            self.align_with_master_facts(force_rebuild=False)
            return
        if event_type == EventType.ENTITY_ADDED:
            entity_id = str(payload.get("entity_id", "") or "").strip()
            if entity_id:
                self.on_entity_added(entity_id)
            return
        if event_type == EventType.ENTITY_DELETED:
            entity_id = str(payload.get("entity_id", "") or "").strip()
            if entity_id:
                self.on_entity_deleted(entity_id)
            return
        if event_type == EventType.ENTITY_RENAMED:
            old_id = str(payload.get("old_id", "") or "").strip()
            new_id = str(payload.get("new_id", "") or "").strip()
            if old_id and new_id:
                self.on_entity_renamed(old_id, new_id)
            return
        if event_type == EventType.ENTITY_MERGED:
            source_id = str(payload.get("source_id", "") or "").strip()
            target_id = str(payload.get("target_id", "") or "").strip()
            if source_id and target_id:
                self.on_entity_merged(source_id, target_id)
            return

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def on_entity_added(self, entity_id: str) -> None:
        record = self.entity_profile_library.ensure_entity(entity_id)
        record.touch()
        self.entity_profile_library.save_to_path(str(self.profile_data_path))

    def on_entity_deleted(self, entity_id: str) -> None:
        changed = self.entity_profile_library.delete_entity(entity_id)
        if changed:
            self.entity_profile_library.save_to_path(str(self.profile_data_path))

        local_state = self._load_local_facts_situation()
        facts = local_state.get("facts", {})
        if isinstance(facts, dict):
            for _, node in facts.items():
                if not isinstance(node, dict):
                    continue
                if str(node.get("entity_uid", "") or "").strip() == entity_id:
                    node["entity_deleted"] = True
                    node["entity_deleted_at"] = self._now_iso()
        self._save_local_facts_situation(local_state)

    def on_entity_renamed(self, old_id: str, new_id: str) -> None:
        changed = self.entity_profile_library.rename_entity(old_id, new_id)
        if not changed:
            return

        local_state = self._load_local_facts_situation()
        facts = local_state.get("facts", {})
        if isinstance(facts, dict):
            for _, node in facts.items():
                if not isinstance(node, dict):
                    continue
                if str(node.get("entity_uid", "") or "").strip() == old_id:
                    node["entity_uid"] = new_id
                    node["entity_renamed_from"] = old_id
                    node["entity_renamed_at"] = self._now_iso()
        self._save_local_facts_situation(local_state)
        self._reconcile_entity_profile(new_id)
        self._save_all()

    def on_entity_merged(self, source_id: str, target_id: str) -> None:
        self.entity_profile_library.merge_entity(source_id=source_id, target_id=target_id)

        local_state = self._load_local_facts_situation()
        facts = local_state.get("facts", {})
        if isinstance(facts, dict):
            for _, node in facts.items():
                if not isinstance(node, dict):
                    continue
                if str(node.get("entity_uid", "") or "").strip() == source_id:
                    node["entity_uid"] = target_id
                    node["merged_from"] = source_id
                    node["merged_at"] = self._now_iso()
        self._save_local_facts_situation(local_state)

        self._reconcile_entity_profile(target_id)
        self._save_all()

    # ------------------------------------------------------------------
    # Public APIs
    # ------------------------------------------------------------------
    def get_stats(self) -> Dict[str, Any]:
        lib_stats = self.entity_profile_library.get_stats()
        local_state = self._load_local_facts_situation()
        summary = local_state.get("summary", {}) if isinstance(local_state, dict) else {}
        metadata = local_state.get("metadata", {}) if isinstance(local_state, dict) else {}
        return {
            **lib_stats,
            "workflow_id": self.workflow_id,
            "facts_summary": summary if isinstance(summary, dict) else {},
            "rebuild_checkpoint": self._extract_rebuild_checkpoint_meta(metadata),
            "local_facts_situation_file": str(self.local_facts_situation_file),
            "master_facts_situation_file": str(self.master_facts_situation_file),
        }

    def get_entity_profile(self, entity_id: str) -> Dict[str, Any]:
        safe_entity = str(entity_id or "").strip()
        profile = self.entity_profile_library.get_entity(safe_entity)
        if profile is None:
            return {"hit": False, "entity_id": safe_entity, "summary": ""}
        return {
            "hit": True,
            "entity_id": safe_entity,
            "summary": str(profile.summary or "").strip(),
        }

    def align_with_master_facts(self, force_rebuild: bool = False) -> Dict[str, Any]:
        scanned_facts = self._scan_fact_files()
        local_state = self._load_local_facts_situation()
        if self._has_active_rebuild_checkpoint(local_state):
            return self.rebuild_from_facts(scanned_facts=scanned_facts, reason="resume_checkpoint")

        local_facts = local_state.get("facts", {}) if isinstance(local_state.get("facts"), dict) else {}

        current_ids = set(scanned_facts.keys())
        local_ids = set(local_facts.keys())
        removed_ids = sorted(local_ids - current_ids)

        new_ids: List[str] = []
        changed_ids: List[str] = []
        for fact_id, fact_info in scanned_facts.items():
            local_node = local_facts.get(fact_id, {})
            if not isinstance(local_node, dict):
                new_ids.append(fact_id)
                continue
            if str(local_node.get("fingerprint", "") or "") != str(fact_info.get("fingerprint", "") or ""):
                changed_ids.append(fact_id)

        need_rebuild = bool(force_rebuild or removed_ids or changed_ids)
        if need_rebuild:
            reason_parts = []
            if force_rebuild:
                reason_parts.append("force_rebuild")
            if removed_ids:
                reason_parts.append(f"removed={len(removed_ids)}")
            if changed_ids:
                reason_parts.append(f"changed={len(changed_ids)}")
            reason = ", ".join(reason_parts) or "unknown"
            return self.rebuild_from_facts(scanned_facts=scanned_facts, reason=reason)

        master_state = self._load_master_facts_situation()
        master_fact_nodes = master_state.get("facts", {}) if isinstance(master_state.get("facts"), dict) else {}
        touched_entities: Set[str] = set()

        processed = 0
        failed = 0
        ordered_new_ids = sorted(new_ids)
        for fact_id in self._progress_iter(
            ordered_new_ids,
            desc="EntityProfile align",
            unit="fact",
        ):
            fact_info = scanned_facts[fact_id]
            node, entity_uid = self._process_single_fact(
                fact_id=fact_id,
                fact_info=fact_info,
                master_fact_node=master_fact_nodes.get(fact_id, {}),
            )
            local_facts[fact_id] = node
            processed += 1
            if node.get("status") == "failed":
                failed += 1
            if entity_uid:
                touched_entities.add(entity_uid)

        for entity_uid in sorted(touched_entities):
            self._refresh_entity_summary(entity_uid)

        local_state["facts"] = local_facts
        self._refresh_local_summary(local_state)
        self._save_local_facts_situation(local_state)
        self.entity_profile_library.save_to_path(str(self.profile_data_path))

        return {
            "success": failed == 0,
            "mode": "incremental",
            "force_rebuild": bool(force_rebuild),
            "facts_scanned": len(scanned_facts),
            "facts_processed": processed,
            "facts_failed": failed,
            "facts_new": len(new_ids),
            "facts_changed": len(changed_ids),
            "facts_removed": len(removed_ids),
        }

    def rebuild_from_facts(
        self,
        scanned_facts: Optional[Dict[str, Dict[str, Any]]] = None,
        reason: str = "",
    ) -> Dict[str, Any]:
        fact_map = scanned_facts if isinstance(scanned_facts, dict) else self._scan_fact_files()
        master_state = self._load_master_facts_situation()
        master_fact_nodes = master_state.get("facts", {}) if isinstance(master_state.get("facts"), dict) else {}
        ordered_fact_ids = sorted(fact_map.keys())

        resume_state = self._load_rebuild_checkpoint_state(fact_map)
        resumed_from_checkpoint = bool(resume_state)
        started_at = str((resume_state or {}).get("started_at") or self._now_iso())
        effective_reason = str((resume_state or {}).get("reason") or reason or "")

        if resume_state:
            local_state = resume_state["local_state"]
            local_facts = resume_state["local_facts"]
            self.entity_profile_library.load_from_path(str(self.rebuild_profile_checkpoint_path))
        else:
            self._reset_rebuild_checkpoint_dir()
            self.entity_profile_library.clear()
            local_state = self._empty_local_facts_situation()
            local_facts = {}
            local_state["facts"] = local_facts
            self._save_rebuild_checkpoint(
                local_state=local_state,
                processed_facts=0,
                total_facts=len(ordered_fact_ids),
                failed_facts=0,
                reason=effective_reason,
                started_at=started_at,
                dirty_entities=set(),
                resumed_from_checkpoint=False,
            )

        processed = len(local_facts)
        failed = self._count_failed_facts(local_facts)
        dirty_entities: Set[str] = set()
        remaining_fact_ids = [fact_id for fact_id in ordered_fact_ids if fact_id not in local_facts]

        for fact_id in self._progress_iter(
            remaining_fact_ids,
            desc="EntityProfile rebuild",
            unit="fact",
        ):
            fact_info = fact_map[fact_id]
            node, entity_uid = self._process_single_fact(
                fact_id=fact_id,
                fact_info=fact_info,
                master_fact_node=master_fact_nodes.get(fact_id, {}),
            )
            local_facts[fact_id] = node
            processed += 1
            if node.get("status") == "failed":
                failed += 1
            if entity_uid:
                dirty_entities.add(entity_uid)
            if processed % self.rebuild_checkpoint_every == 0:
                self._save_rebuild_checkpoint(
                    local_state=local_state,
                    processed_facts=processed,
                    total_facts=len(ordered_fact_ids),
                    failed_facts=failed,
                    reason=effective_reason,
                    started_at=started_at,
                    dirty_entities=dirty_entities,
                    resumed_from_checkpoint=resumed_from_checkpoint,
                )
                dirty_entities = set()

        self._save_rebuild_checkpoint(
            local_state=local_state,
            processed_facts=processed,
            total_facts=len(ordered_fact_ids),
            failed_facts=failed,
            reason=effective_reason,
            started_at=started_at,
            dirty_entities=dirty_entities,
            resumed_from_checkpoint=resumed_from_checkpoint,
        )
        self.entity_profile_library.save_to_path(str(self.profile_data_path))
        local_state["facts"] = local_facts
        self._refresh_local_summary(local_state)
        metadata = local_state.get("metadata", {}) if isinstance(local_state.get("metadata"), dict) else {}
        metadata["rebuild_reason"] = effective_reason
        self._update_rebuild_checkpoint_meta(
            metadata=metadata,
            status="completed",
            reason=effective_reason,
            started_at=started_at,
            total_facts=len(ordered_fact_ids),
            processed_facts=processed,
            failed_facts=failed,
            resumed_from_checkpoint=resumed_from_checkpoint,
        )
        local_state["metadata"] = metadata
        self._save_local_facts_situation(local_state)
        self._clear_rebuild_checkpoint_dir()

        return {
            "success": failed == 0,
            "mode": "rebuild",
            "facts_scanned": len(fact_map),
            "facts_processed": processed,
            "facts_failed": failed,
            "rebuild_reason": effective_reason,
            "resumed_from_checkpoint": resumed_from_checkpoint,
        }

    def query_entity_feature(self, entity_id: str, feature_query: str, topk: int = 5) -> Dict[str, Any]:
        safe_entity = str(entity_id or "").strip()
        query_text = str(feature_query or "").strip()
        safe_topk = max(1, int(topk))

        profile = self.entity_profile_library.get_entity(safe_entity)
        if profile is None:
            return {
                "hit": False,
                "entity_id": safe_entity,
                "feature_query": query_text,
                "matched_count": 0,
                "results": [],
            }

        query_embedding = self._safe_embed(query_text)
        scored: List[Tuple[float, AttributeEntry]] = []
        for item in profile.attributes:
            text = str(item.text or "").strip() or self.merge_strategy.build_attribute_text(item.field, item.content)
            if not text:
                continue
            embedding = item.embedding if isinstance(item.embedding, list) else self._safe_embed(text)
            if not embedding or not query_embedding:
                score = 1.0 if query_text and query_text.lower() in text.lower() else 0.0
            else:
                score = self._cosine_similarity(query_embedding, embedding)
            scored.append((float(score), item))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_items = scored[:safe_topk]
        results = []
        for score, item in top_items:
            results.append(
                {
                    "field": item.field,
                    "content": item.content,
                    "score": round(score, 6),
                    "evidence": self._flatten_evidence(item.sources),
                }
            )

        hit = bool(results and results[0]["score"] > 0.35)
        return {
            "hit": hit,
            "entity_id": safe_entity,
            "feature_query": query_text,
            "matched_count": len(results),
            "results": results,
        }

    def query_entity_event(self, entity_id: str, event_query: str, topk: int = 5) -> Dict[str, Any]:
        safe_entity = str(entity_id or "").strip()
        query_text = str(event_query or "").strip()
        safe_topk = max(1, int(topk))

        profile = self.entity_profile_library.get_entity(safe_entity)
        if profile is None:
            return {
                "hit": False,
                "entity_id": safe_entity,
                "event_query": query_text,
                "matched_count": 0,
                "results": [],
            }

        query_embedding = self._safe_embed(query_text)
        scored: List[Tuple[float, EventEntry]] = []
        for item in profile.events:
            text = str(item.text or "").strip() or self.merge_strategy.build_event_text(item.event, item.abstract_time)
            if not text:
                continue
            embedding = item.embedding if isinstance(item.embedding, list) else self._safe_embed(text)
            if not embedding or not query_embedding:
                score = 1.0 if query_text and query_text.lower() in text.lower() else 0.0
            else:
                score = self._cosine_similarity(query_embedding, embedding)
            scored.append((float(score), item))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_items = scored[:safe_topk]
        results = []
        for score, item in top_items:
            results.append(
                {
                    "event": item.event,
                    "actual_time": [slot.to_dict() for slot in item.actual_time],
                    "abstract_time": item.abstract_time,
                    "score": round(score, 6),
                    "evidence": self._flatten_evidence(item.sources),
                }
            )

        hit = bool(results and results[0]["score"] > 0.35)
        return {
            "hit": hit,
            "entity_id": safe_entity,
            "event_query": query_text,
            "matched_count": len(results),
            "results": results,
        }

    def query_entity_time(
        self,
        entity_id: str,
        start_time: str,
        end_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        safe_entity = str(entity_id or "").strip()
        query_start_text, query_end_text = self._normalize_time_window_inputs(start_time, end_time)
        profile = self.entity_profile_library.get_entity(safe_entity)
        if profile is None:
            return {
                "hit": False,
                "entity_id": safe_entity,
                "start_time": query_start_text,
                "end_time": query_end_text,
                "matched_count": 0,
                "results": [],
            }

        query_start, query_end = self._parse_time_window(query_start_text, query_end_text)
        if query_start is None or query_end is None:
            return {
                "hit": False,
                "entity_id": safe_entity,
                "start_time": query_start_text,
                "end_time": query_end_text,
                "matched_count": 0,
                "results": [],
                "error": "invalid_time_window",
            }

        matched: List[Dict[str, Any]] = []
        for event_item in profile.events:
            match_reason = ""
            for slot in event_item.actual_time:
                slot_start = self._parse_iso_datetime(slot.start_time)
                slot_end = self._parse_iso_datetime(slot.end_time)
                if slot_start is None and slot_end is None:
                    continue
                if slot_start is None:
                    slot_start = slot_end
                if slot_end is None:
                    slot_end = slot_start
                if slot_start is None or slot_end is None:
                    continue
                if slot_start <= query_end and slot_end >= query_start:
                    match_reason = "actual_time_overlap"
                    break

            if match_reason:
                matched.append(
                    {
                        "event": event_item.event,
                        "actual_time": [slot.to_dict() for slot in event_item.actual_time],
                        "abstract_time": event_item.abstract_time,
                        "match_reason": match_reason,
                        "evidence": self._flatten_evidence(event_item.sources),
                    }
                )

        return {
            "hit": bool(matched),
            "entity_id": safe_entity,
            "start_time": query_start_text,
            "end_time": query_end_text,
            "matched_count": len(matched),
            "results": matched,
        }

    # ------------------------------------------------------------------
    # Core processing
    # ------------------------------------------------------------------
    def _process_single_fact(
        self,
        fact_id: str,
        fact_info: Dict[str, Any],
        master_fact_node: Any,
    ) -> Tuple[Dict[str, Any], str]:
        payload = fact_info.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        master_node = master_fact_node if isinstance(master_fact_node, dict) else {}

        atomic_fact = self._extract_atomic_fact(payload)
        main_entity = str(payload.get("main_entity") or master_node.get("main_entity") or "").strip()
        entity_uid = str(payload.get("entity_UID") or master_node.get("entity_UID") or main_entity).strip()
        evidence = self._build_evidence(
            fact_id=fact_id,
            fact_file=str(fact_info.get("fact_file", "") or ""),
            payload=payload,
            master_fact_node=master_node,
        )

        filter_result = self._run_fact_filter(atomic_fact)

        status = "processed"
        error_text = ""
        attr_added = 0
        event_added = 0
        attr_merged = 0
        event_merged = 0

        try:
            if not entity_uid:
                status = "skipped"
                error_text = "missing_entity_uid"
            else:
                profile = self.entity_profile_library.ensure_entity(entity_uid)

                if filter_result.get("attribute_available", False):
                    attr_candidates = self._extract_attribute_candidates(
                        atomic_fact=atomic_fact,
                        entity_uid=entity_uid,
                        main_entity=main_entity,
                    )
                    for candidate in attr_candidates:
                        merged = self._upsert_attribute(profile, candidate, evidence)
                        if merged:
                            attr_merged += 1
                        else:
                            attr_added += 1

                if filter_result.get("event_available", False):
                    event_candidates = self._extract_event_candidates(
                        atomic_fact=atomic_fact,
                        entity_uid=entity_uid,
                        main_entity=main_entity,
                        evidence=evidence,
                    )
                    for candidate in event_candidates:
                        merged = self._upsert_event(profile, candidate, evidence)
                        if merged:
                            event_merged += 1
                        else:
                            event_added += 1

                profile.touch()

        except Exception as exc:
            status = "failed"
            error_text = str(exc)
            logger.warning("Process fact failed (%s): %s", fact_id, exc)

        fact_node = {
            "fact_file": str(fact_info.get("fact_file", "") or ""),
            "fingerprint": str(fact_info.get("fingerprint", "") or ""),
            "atomic_fact": atomic_fact,
            "main_entity": main_entity,
            "entity_uid": entity_uid,
            "source": evidence.to_dict(),
            "filter": {
                "event_available": bool(filter_result.get("event_available", False)),
                "event_reason": str(filter_result.get("event_reason", "") or ""),
                "attribute_available": bool(filter_result.get("attribute_available", False)),
                "attribute_reason": str(filter_result.get("attribute_reason", "") or ""),
            },
            "processing": {
                "attribute_added": attr_added,
                "attribute_merged": attr_merged,
                "event_added": event_added,
                "event_merged": event_merged,
            },
            "status": status,
            "error": error_text,
            "last_processed_at": self._now_iso(),
        }
        return fact_node, entity_uid

    def _upsert_attribute(self, profile: EntityProfileRecord, candidate: AttributeEntry, evidence: EvidenceRef) -> bool:
        candidate.sources = EntityProfileLibrary.merge_sources(candidate.sources, [evidence])
        candidate = self.merge_strategy.prepare_attribute_candidate(candidate)
        target_idx = self.merge_strategy.find_attribute_merge_target(candidate, profile.attributes)
        if target_idx is None:
            profile.attributes.append(candidate)
            return False

        target = profile.attributes[target_idx]
        target.content = self._merge_string_list(target.content, candidate.content)
        target.sources = EntityProfileLibrary.merge_sources(target.sources, candidate.sources)
        target.confidence = max(float(target.confidence or 0.0), float(candidate.confidence or 0.0))
        target.text = self.merge_strategy.build_attribute_text(target.field or candidate.field, target.content)
        if not target.embedding:
            target.embedding = candidate.embedding
        if not target.field:
            target.field = candidate.field
        target.metadata = {**candidate.metadata, **target.metadata}
        return True

    def _upsert_event(self, profile: EntityProfileRecord, candidate: EventEntry, evidence: EvidenceRef) -> bool:
        candidate.sources = EntityProfileLibrary.merge_sources(candidate.sources, [evidence])
        candidate = self.merge_strategy.prepare_event_candidate(candidate)
        target_idx = self.merge_strategy.find_event_merge_target(candidate, profile.events)
        if target_idx is None:
            profile.events.append(candidate)
            return False

        target = profile.events[target_idx]
        target.sources = EntityProfileLibrary.merge_sources(target.sources, candidate.sources)
        target.actual_time = EntityProfileLibrary.merge_time_ranges(target.actual_time, candidate.actual_time)
        target.abstract_time = EntityProfileLibrary.merge_abstract_time(target.abstract_time, candidate.abstract_time)
        target.confidence = max(float(target.confidence or 0.0), float(candidate.confidence or 0.0))
        if not target.event:
            target.event = candidate.event
        target.text = self.merge_strategy.build_event_text(target.event, target.abstract_time)
        if not target.embedding:
            target.embedding = candidate.embedding
        target.metadata = {**candidate.metadata, **target.metadata}
        return True

    def _reconcile_entity_profile(self, entity_id: str) -> None:
        profile = self.entity_profile_library.get_entity(entity_id)
        if profile is None:
            return

        old_attrs = list(profile.attributes)
        old_events = list(profile.events)
        profile.attributes = []
        profile.events = []

        for item in old_attrs:
            if not isinstance(item, AttributeEntry):
                continue
            candidate = AttributeEntry(
                field=item.field,
                content=list(item.content),
                text=item.text,
                embedding=item.embedding[:] if isinstance(item.embedding, list) else None,
                sources=list(item.sources),
                confidence=float(item.confidence or 0.0),
                metadata=dict(item.metadata or {}),
            )
            if not candidate.sources:
                candidate.sources = [EvidenceRef()]
            self._upsert_attribute(profile, candidate, candidate.sources[0])

        for item in old_events:
            if not isinstance(item, EventEntry):
                continue
            candidate = EventEntry(
                event=item.event,
                actual_time=list(item.actual_time),
                abstract_time=list(item.abstract_time),
                text=item.text,
                embedding=item.embedding[:] if isinstance(item.embedding, list) else None,
                sources=list(item.sources),
                confidence=float(item.confidence or 0.0),
                metadata=dict(item.metadata or {}),
            )
            if not candidate.sources:
                candidate.sources = [EvidenceRef()]
            self._upsert_event(profile, candidate, candidate.sources[0])

        profile.touch()
        self._refresh_entity_summary(entity_id)

    # ------------------------------------------------------------------
    # Fact filter + extraction
    # ------------------------------------------------------------------
    def _run_fact_filter(self, atomic_fact: str) -> Dict[str, Any]:
        prompt = self.facts_filter_prompt.replace("{fact}", atomic_fact)
        parsed = self._as_dict_payload(self._call_llm_json(prompt, default={}))
        event_available = self._to_bool(parsed.get("event_available", False))
        attribute_available = self._to_bool(parsed.get("attribute_available", False))
        return {
            "event_available": event_available,
            "event_reason": str(parsed.get("event_reason", "") or ""),
            "attribute_available": attribute_available,
            "attribute_reason": str(parsed.get("attribute_reason", "") or ""),
        }

    def _extract_attribute_candidates(self, atomic_fact: str, entity_uid: str, main_entity: str) -> List[AttributeEntry]:
        prompt = (
            "You are an information extraction system.\n"
            "Extract stable profile attributes from one atomic fact.\n"
            "If there is no stable attribute, return an empty list.\n\n"
            f"Entity UID: {entity_uid}\n"
            f"Entity Name: {main_entity}\n"
            f"Atomic Fact: {atomic_fact}\n\n"
            "Output JSON only:\n"
            '{"attributes":[{"field":"like","content":["apple","meat"]}]}\n'
            "Rules:\n"
            "- Return ONLY one JSON object. Do NOT return an array.\n"
            "- field should be concise, lower-case, snake_case style when possible.\n"
            "- content must be a list of strings.\n"
            "- Keep only stable preferences/traits/abilities."
        )
        payload = self._call_llm_json(prompt, default={})
        raw_items = self._extract_list_payload(payload, key="attributes")

        output: List[AttributeEntry] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field", "") or "").strip()
            raw_content = item.get("content", [])
            if isinstance(raw_content, str):
                content = [raw_content.strip()] if raw_content.strip() else []
            elif isinstance(raw_content, list):
                content = [str(x).strip() for x in raw_content if str(x).strip()]
            else:
                content = []
            content = self._merge_string_list([], content)
            if not field or not content:
                continue
            output.append(
                AttributeEntry(
                    field=field,
                    content=content,
                    text=self.merge_strategy.build_attribute_text(field, content),
                    confidence=0.8,
                    metadata={"source": "llm_attribute_extract"},
                )
            )
        return output

    def _extract_event_candidates(
        self,
        atomic_fact: str,
        entity_uid: str,
        main_entity: str,
        evidence: EvidenceRef,
    ) -> List[EventEntry]:
        known_start = str(evidence.start_time or "")
        known_end = str(evidence.end_time or "")
        prompt = (
            "You are an information extraction system.\n"
            "Extract timeline events from one atomic fact.\n"
            "If there is no valid event, return an empty list.\n\n"
            f"Entity UID: {entity_uid}\n"
            f"Entity Name: {main_entity}\n"
            f"Atomic Fact: {atomic_fact}\n"
            f"Known source time: start_time={known_start}, end_time={known_end}\n\n"
            "Output JSON only:\n"
            '{"events":[{"event":"walk in the park","actual_time":[{"start_time":"...","end_time":"..."}],"abstract_time":["everyday"]}]}\n'
            "Rules:\n"
            "- Return ONLY one JSON object. Do NOT return an array.\n"
            "- event must be concise and concrete.\n"
            "- actual_time can be [] when not available.\n"
            "- abstract_time can be [] when not available."
        )
        payload = self._call_llm_json(prompt, default={})
        raw_items = self._extract_list_payload(payload, key="events")

        output: List[EventEntry] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            event_text = str(item.get("event", "") or "").strip()
            if not event_text:
                continue

            raw_actual = item.get("actual_time", [])
            if isinstance(raw_actual, dict):
                raw_actual = [raw_actual]
            actual_time: List[EventTimeRange] = []
            if isinstance(raw_actual, list):
                for slot in raw_actual:
                    if not isinstance(slot, dict):
                        continue
                    start_time = str(slot.get("start_time", "") or "").strip()
                    end_time = str(slot.get("end_time", "") or "").strip()
                    if not start_time and not end_time:
                        continue
                    actual_time.append(EventTimeRange(start_time=start_time, end_time=end_time))

            if not actual_time and (known_start or known_end):
                actual_time.append(EventTimeRange(start_time=known_start, end_time=known_end))

            raw_abstract = item.get("abstract_time", [])
            if isinstance(raw_abstract, str):
                abstract_time = [raw_abstract.strip()] if raw_abstract.strip() else []
            elif isinstance(raw_abstract, list):
                abstract_time = [str(x).strip() for x in raw_abstract if str(x).strip()]
            else:
                abstract_time = []
            abstract_time = self._merge_string_list([], abstract_time)

            event_entry = EventEntry(
                event=event_text,
                actual_time=actual_time,
                abstract_time=abstract_time,
                text=self.merge_strategy.build_event_text(event_text, abstract_time),
                confidence=0.8,
                metadata={"source": "llm_event_extract"},
            )
            output.append(event_entry)
        return output

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    def _refresh_entity_summary(self, entity_id: str) -> None:
        profile = self.entity_profile_library.get_entity(entity_id)
        if profile is None:
            return
        if not self.enable_summary_llm:
            profile.summary = self._fallback_summary(profile)
            return

        attr_lines = []
        for item in profile.attributes[:8]:
            attr_lines.append(f"- {item.field}: {', '.join(item.content[:6])}")
        event_lines = []
        for item in profile.events[:8]:
            times = [slot.to_dict() for slot in item.actual_time]
            event_lines.append(
                f"- {item.event} | actual_time={times} | abstract_time={item.abstract_time[:4]}"
            )

        prompt = (
            "You are an entity profile summarizer.\n"
            "Write a concise profile summary in 2-4 sentences.\n"
            "Focus on stable traits and key recurring events.\n\n"
            f"Entity: {entity_id}\n"
            "Attributes:\n"
            + ("\n".join(attr_lines) if attr_lines else "- None")
            + "\n\nEvents:\n"
            + ("\n".join(event_lines) if event_lines else "- None")
            + "\n\nReturn plain text only."
        )
        try:
            summary = str(self.llm_func(prompt) or "").strip()
        except Exception as exc:
            logger.warning("Profile summary LLM failed (%s): %s", entity_id, exc)
            summary = ""

        profile.summary = summary if summary else self._fallback_summary(profile)

    @staticmethod
    def _fallback_summary(profile: EntityProfileRecord) -> str:
        attr_head = ", ".join(
            [
                f"{item.field}={','.join(item.content[:2])}"
                for item in profile.attributes[:3]
                if item.field and item.content
            ]
        )
        event_head = ", ".join([item.event for item in profile.events[:3] if item.event])
        parts = []
        if attr_head:
            parts.append(f"Traits: {attr_head}.")
        if event_head:
            parts.append(f"Events: {event_head}.")
        return " ".join(parts).strip()

    # ------------------------------------------------------------------
    # Local facts state helpers
    # ------------------------------------------------------------------
    def _load_local_facts_situation(self) -> Dict[str, Any]:
        data = self._load_json(self.local_facts_situation_file)
        if not isinstance(data, dict):
            return self._empty_local_facts_situation()
        if not isinstance(data.get("facts"), dict):
            data["facts"] = {}
        if not isinstance(data.get("summary"), dict):
            data["summary"] = {}
        if not isinstance(data.get("metadata"), dict):
            data["metadata"] = {}
        data["workflow_id"] = self.workflow_id
        return data

    def _load_master_facts_situation(self) -> Dict[str, Any]:
        data = self._load_json(self.master_facts_situation_file)
        if not isinstance(data, dict):
            return {"workflow_id": self.workflow_id, "summary": {}, "entities": [], "facts": {}, "metadata": {}}
        if not isinstance(data.get("facts"), dict):
            data["facts"] = {}
        return data

    def _save_local_facts_situation(self, payload: Dict[str, Any]) -> bool:
        payload["workflow_id"] = self.workflow_id
        payload["metadata"] = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
        payload["metadata"]["last_updated"] = self._now_iso()
        payload["metadata"]["source"] = "entity_profile_sys"
        return self._save_json(self.local_facts_situation_file, payload)

    def _empty_local_facts_situation(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "summary": {},
            "facts": {},
            "metadata": {},
        }

    def _refresh_local_summary(self, local_state: Dict[str, Any]) -> None:
        facts = local_state.get("facts", {})
        if not isinstance(facts, dict):
            facts = {}

        scanned = len(facts)
        failed = 0
        event_available = 0
        attribute_available = 0
        processed = 0
        for node in facts.values():
            if not isinstance(node, dict):
                continue
            processed += 1
            if str(node.get("status", "") or "") == "failed":
                failed += 1
            filter_node = node.get("filter", {})
            if isinstance(filter_node, dict):
                if bool(filter_node.get("event_available", False)):
                    event_available += 1
                if bool(filter_node.get("attribute_available", False)):
                    attribute_available += 1

        local_state["summary"] = {
            "facts_scanned": scanned,
            "facts_processed": processed,
            "facts_failed": failed,
            "event_available_count": event_available,
            "attribute_available_count": attribute_available,
            "entity_profile_count": self.entity_profile_library.get_stats().get("profile_count", 0),
            "updated_at": self._now_iso(),
        }

    def _refresh_entity_summaries(self, entity_ids: Set[str]) -> None:
        for entity_uid in sorted({str(x or "").strip() for x in entity_ids if str(x or "").strip()}):
            self._refresh_entity_summary(entity_uid)

    @staticmethod
    def _count_failed_facts(local_facts: Dict[str, Dict[str, Any]]) -> int:
        failed = 0
        for node in local_facts.values():
            if isinstance(node, dict) and str(node.get("status", "") or "") == "failed":
                failed += 1
        return failed

    @staticmethod
    def _extract_rebuild_checkpoint_meta(metadata: Any) -> Dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        node = metadata.get("rebuild_checkpoint", {})
        return dict(node) if isinstance(node, dict) else {}

    def _has_active_rebuild_checkpoint(self, local_state: Dict[str, Any]) -> bool:
        metadata = local_state.get("metadata", {}) if isinstance(local_state.get("metadata"), dict) else {}
        checkpoint_meta = self._extract_rebuild_checkpoint_meta(metadata)
        return str(checkpoint_meta.get("status", "") or "").strip().lower() == "in_progress"

    def _update_rebuild_checkpoint_meta(
        self,
        metadata: Dict[str, Any],
        status: str,
        reason: str,
        started_at: str,
        total_facts: int,
        processed_facts: int,
        failed_facts: int,
        resumed_from_checkpoint: bool,
    ) -> None:
        metadata["rebuild_reason"] = str(reason or "")
        metadata["rebuild_checkpoint"] = {
            "status": str(status or "").strip().lower(),
            "reason": str(reason or ""),
            "started_at": str(started_at or self._now_iso()),
            "updated_at": self._now_iso(),
            "total_facts": max(0, int(total_facts)),
            "processed_facts": max(0, int(processed_facts)),
            "failed_facts": max(0, int(failed_facts)),
            "remaining_facts": max(0, int(total_facts) - int(processed_facts)),
            "checkpoint_every": self.rebuild_checkpoint_every,
            "checkpoint_dir": str(self.rebuild_profile_checkpoint_path),
            "resumed_from_checkpoint": bool(resumed_from_checkpoint),
        }
        if str(status or "").strip().lower() == "completed":
            metadata["rebuild_checkpoint"]["completed_at"] = self._now_iso()

    def _clear_rebuild_checkpoint_dir(self) -> None:
        if self.rebuild_profile_checkpoint_path.exists():
            shutil.rmtree(self.rebuild_profile_checkpoint_path, ignore_errors=True)

    def _reset_rebuild_checkpoint_dir(self) -> None:
        self._clear_rebuild_checkpoint_dir()
        self.rebuild_profile_checkpoint_path.mkdir(parents=True, exist_ok=True)

    def _load_rebuild_checkpoint_state(
        self, fact_map: Dict[str, Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        local_state = self._load_local_facts_situation()
        if not self._has_active_rebuild_checkpoint(local_state):
            return None

        local_facts = local_state.get("facts", {})
        if not isinstance(local_facts, dict):
            return None
        if not local_facts:
            return None

        for fact_id, node in local_facts.items():
            if not isinstance(node, dict):
                logger.info("EntityProfile rebuild checkpoint invalid: fact node is not dict (%s)", fact_id)
                self._clear_rebuild_checkpoint_dir()
                return None
            current = fact_map.get(fact_id)
            if current is None:
                logger.info("EntityProfile rebuild checkpoint invalid: fact removed (%s)", fact_id)
                self._clear_rebuild_checkpoint_dir()
                return None
            checkpoint_fp = str(node.get("fingerprint", "") or "")
            current_fp = str(current.get("fingerprint", "") or "")
            if checkpoint_fp != current_fp:
                logger.info("EntityProfile rebuild checkpoint invalid: fingerprint changed (%s)", fact_id)
                self._clear_rebuild_checkpoint_dir()
                return None

        self.rebuild_profile_checkpoint_path.mkdir(parents=True, exist_ok=True)
        metadata = local_state.get("metadata", {}) if isinstance(local_state.get("metadata"), dict) else {}
        checkpoint_meta = self._extract_rebuild_checkpoint_meta(metadata)
        return {
            "local_state": local_state,
            "local_facts": local_facts,
            "started_at": str(checkpoint_meta.get("started_at", "") or self._now_iso()),
            "reason": str(checkpoint_meta.get("reason", "") or ""),
        }

    def _save_rebuild_checkpoint(
        self,
        local_state: Dict[str, Any],
        processed_facts: int,
        total_facts: int,
        failed_facts: int,
        reason: str,
        started_at: str,
        dirty_entities: Set[str],
        resumed_from_checkpoint: bool,
    ) -> None:
        local_facts = local_state.get("facts", {})
        if not isinstance(local_facts, dict):
            local_facts = {}
            local_state["facts"] = local_facts

        self._refresh_entity_summaries(dirty_entities)
        self.rebuild_profile_checkpoint_path.mkdir(parents=True, exist_ok=True)

        dirty_ids = sorted({str(x or "").strip() for x in dirty_entities if str(x or "").strip()})
        if dirty_ids:
            self.entity_profile_library.save_to_path(
                str(self.rebuild_profile_checkpoint_path),
                prune_missing=False,
                entity_ids=dirty_ids,
            )

        self._refresh_local_summary(local_state)
        metadata = local_state.get("metadata", {}) if isinstance(local_state.get("metadata"), dict) else {}
        self._update_rebuild_checkpoint_meta(
            metadata=metadata,
            status="in_progress",
            reason=reason,
            started_at=started_at,
            total_facts=total_facts,
            processed_facts=processed_facts,
            failed_facts=failed_facts,
            resumed_from_checkpoint=resumed_from_checkpoint,
        )
        local_state["metadata"] = metadata
        self._save_local_facts_situation(local_state)

    # ------------------------------------------------------------------
    # Fact/evidence parsing
    # ------------------------------------------------------------------
    def _scan_fact_files(self) -> Dict[str, Dict[str, Any]]:
        facts: Dict[str, Dict[str, Any]] = {}
        if not self.facts_dir.exists() or not self.facts_dir.is_dir():
            return facts

        for file_path in sorted(self.facts_dir.glob("*.json")):
            payload = self._load_json(file_path)
            if not isinstance(payload, dict):
                continue
            fact_id = str(payload.get("fact_id", "") or file_path.stem).strip()
            if not fact_id:
                fact_id = file_path.stem
            facts[fact_id] = {
                "fact_id": fact_id,
                "fact_file": file_path.name,
                "fact_path": str(file_path),
                "payload": payload,
                "fingerprint": self._build_fact_fingerprint(file_path, payload),
            }
        return facts

    def _build_fact_fingerprint(self, file_path: Path, payload: Dict[str, Any]) -> str:
        try:
            stat = file_path.stat()
            mtime_ns = int(stat.st_mtime_ns)
            size = int(stat.st_size)
        except Exception:
            mtime_ns = 0
            size = 0
        token = {
            "file": file_path.name,
            "mtime_ns": mtime_ns,
            "size": size,
            "fact_id": payload.get("fact_id"),
            "atomic_fact": self._extract_atomic_fact(payload),
            "entity_UID": payload.get("entity_UID"),
            "main_entity": payload.get("main_entity"),
        }
        raw = json.dumps(token, ensure_ascii=False, sort_keys=True)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _build_evidence(
        self,
        fact_id: str,
        fact_file: str,
        payload: Dict[str, Any],
        master_fact_node: Dict[str, Any],
    ) -> EvidenceRef:
        evidence = payload.get("evidence", {})
        if not isinstance(evidence, dict):
            evidence = {}
        source_node = master_fact_node.get("source", {})
        if not isinstance(source_node, dict):
            source_node = {}

        dialogue_id = str(evidence.get("dialogue_id") or source_node.get("dialogue_id") or "").strip()
        episode_id = str(evidence.get("episode_id") or source_node.get("episode_id") or "").strip()
        scene_id = str(payload.get("scene_id") or source_node.get("scene_id") or "").strip()
        start_time = str(payload.get("start_time") or source_node.get("start_time") or "").strip()
        end_time = str(payload.get("end_time") or source_node.get("end_time") or "").strip()

        if (not start_time and not end_time) and dialogue_id and episode_id:
            cached = self._lookup_episode_time(dialogue_id, episode_id)
            start_time = start_time or str(cached.get("start_time", "") or "")
            end_time = end_time or str(cached.get("end_time", "") or "")

        return EvidenceRef(
            fact_id=fact_id,
            fact_file=fact_file,
            scene_id=scene_id,
            dialogue_id=dialogue_id,
            episode_id=episode_id,
            start_time=start_time,
            end_time=end_time,
        )

    @staticmethod
    def _extract_atomic_fact(payload: Dict[str, Any]) -> str:
        for key in ("Atomic fact", "atomic_fact", "atomic fact", "Atomic_fact"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    # ------------------------------------------------------------------
    # Episode time cache
    # ------------------------------------------------------------------
    def _lookup_episode_time(self, dialogue_id: str, episode_id: str) -> Dict[str, str]:
        if self._episode_time_cache is None:
            self._episode_time_cache = self._build_episode_time_index()
        return self._episode_time_cache.get((dialogue_id, episode_id), {"start_time": "", "end_time": ""})

    def _build_episode_time_index(self) -> Dict[Tuple[str, str], Dict[str, str]]:
        index: Dict[Tuple[str, str], Dict[str, str]] = {}
        if not self.scene_dir.exists() or not self.scene_dir.is_dir():
            return index

        for scene_file in sorted(self.scene_dir.glob("*.json")):
            scene_data = self._load_json(scene_file)
            if not isinstance(scene_data, dict):
                continue
            source = scene_data.get("source", {})
            episodes = source.get("episodes", []) if isinstance(source, dict) else []
            if not isinstance(episodes, list):
                continue

            for ep in episodes:
                if not isinstance(ep, dict):
                    continue
                dialogue_id = str(ep.get("dialogue_id", "") or "").strip()
                episode_id = str(ep.get("episode_id", "") or "").strip()
                if not dialogue_id or not episode_id:
                    continue
                start_time = str(ep.get("start_time") or ep.get("starttime") or "").strip()
                end_time = str(ep.get("end_time") or ep.get("endtime") or "").strip()
                key = (dialogue_id, episode_id)
                current = index.get(key, {"start_time": "", "end_time": ""})
                index[key] = {
                    "start_time": self._pick_earlier_time(current.get("start_time", ""), start_time),
                    "end_time": self._pick_later_time(current.get("end_time", ""), end_time),
                }
        return index

    def _pick_earlier_time(self, a: str, b: str) -> str:
        if not a:
            return b
        if not b:
            return a
        dt_a = self._parse_iso_datetime(a)
        dt_b = self._parse_iso_datetime(b)
        if dt_a and dt_b:
            return a if dt_a <= dt_b else b
        return min(a, b)

    def _pick_later_time(self, a: str, b: str) -> str:
        if not a:
            return b
        if not b:
            return a
        dt_a = self._parse_iso_datetime(a)
        dt_b = self._parse_iso_datetime(b)
        if dt_a and dt_b:
            return a if dt_a >= dt_b else b
        return max(a, b)

    # ------------------------------------------------------------------
    # LLM helpers
    # ------------------------------------------------------------------
    def _load_fact_filter_prompt(self, prompt_path: Path) -> str:
        if not prompt_path.exists():
            logger.warning("facts_filter prompt not found: %s", prompt_path)
            return "{fact}"
        raw = prompt_path.read_text(encoding="utf-8")
        if yaml is not None:
            try:
                parsed = yaml.safe_load(raw) or {}
                if isinstance(parsed, dict):
                    template = parsed.get("fact_filter_v1")
                    if isinstance(template, str) and template.strip():
                        return template
            except Exception:
                pass

        block = self._extract_multiline_block(raw, key="fact_filter_v1")
        if block:
            return block
        return "{fact}"

    @staticmethod
    def _extract_multiline_block(raw_text: str, key: str) -> str:
        lines = raw_text.splitlines()
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            if line.strip() == f"{key}: |":
                base_indent = len(line) - len(line.lstrip(" ")) + 2
                block: List[str] = []
                idx += 1
                while idx < len(lines):
                    current = lines[idx]
                    stripped = current.strip()
                    current_indent = len(current) - len(current.lstrip(" "))
                    if stripped and current_indent < base_indent:
                        break
                    if current.startswith(" " * base_indent):
                        block.append(current[base_indent:])
                    elif not stripped:
                        block.append("")
                    else:
                        block.append(current.lstrip())
                    idx += 1
                return "\n".join(block).strip()
            idx += 1
        return ""

    def _call_llm_json(self, prompt: str, default: Any) -> Any:
        try:
            response = self.llm_func(prompt)
        except Exception as exc:
            logger.warning("LLM call failed: %s", exc)
            return default
        return self._extract_json_from_text(str(response or ""), default=default)

    def _extract_json_from_text(self, text: str, default: Any) -> Any:
        payload = str(text or "").strip()
        if not payload:
            return default

        fenced = re.search(r"```(?:json)?\s*(.*?)```", payload, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            payload = fenced.group(1).strip()

        try:
            return json.loads(payload)
        except Exception:
            pass

        arr = re.search(r"\[[\s\S]*\]", payload)
        if arr:
            try:
                return json.loads(arr.group(0))
            except Exception:
                pass

        obj = re.search(r"\{[\s\S]*\}", payload)
        if obj:
            try:
                return json.loads(obj.group(0))
            except Exception:
                pass
        return default

    @staticmethod
    def _as_dict_payload(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    return item
        return {}

    @staticmethod
    def _extract_list_payload(value: Any, key: str) -> List[Dict[str, Any]]:
        if isinstance(value, dict):
            raw = value.get(key, [])
            if isinstance(raw, dict):
                return [raw]
            if isinstance(raw, list):
                return [item for item in raw if isinstance(item, dict)]
            return []
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return []

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------
    def _progress_iter(self, items: Sequence[Any], desc: str, unit: str = "item"):
        if not self.enable_progress:
            return items
        if tqdm is None:
            return items
        try:
            return tqdm(items, total=len(items), desc=desc, unit=unit)
        except Exception:
            return items

    def _save_all(self) -> None:
        self.entity_profile_library.save_to_path(str(self.profile_data_path))
        local_state = self._load_local_facts_situation()
        self._refresh_local_summary(local_state)
        self._save_local_facts_situation(local_state)

    @staticmethod
    def _load_json(path: Path) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logger.warning("Load json failed (%s): %s", path, exc)
        return None

    @staticmethod
    def _save_json(path: Path, payload: Dict[str, Any]) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.warning("Save json failed (%s): %s", path, exc)
            return False

    @staticmethod
    def _merge_string_list(left: Sequence[str], right: Sequence[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for value in list(left) + list(right):
            text = str(value or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
        return out

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or "").strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n", ""}:
            return False
        return False

    @staticmethod
    def _flatten_evidence(sources: Sequence[EvidenceRef]) -> List[Dict[str, str]]:
        output: List[Dict[str, str]] = []
        seen = set()
        for src in sources:
            if not isinstance(src, EvidenceRef):
                continue
            key = (src.dialogue_id, src.episode_id, src.fact_id)
            if key in seen:
                continue
            seen.add(key)
            output.append(
                {
                    "dialogue_id": src.dialogue_id,
                    "episode_id": src.episode_id,
                    "fact_id": src.fact_id,
                    "fact_file": src.fact_file,
                    "scene_id": src.scene_id,
                }
            )
        return output

    def _safe_embed(self, text: str) -> Optional[List[float]]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return None
        try:
            vec = self.embed_func(cleaned)
        except Exception as exc:
            logger.warning("Embedding failed: %s", exc)
            return None
        if isinstance(vec, list) and vec:
            try:
                return [float(x) for x in vec]
            except Exception:
                return None
        return None

    @staticmethod
    def _cosine_similarity(vec_a: Optional[List[float]], vec_b: Optional[List[float]]) -> float:
        if not isinstance(vec_a, list) or not isinstance(vec_b, list) or not vec_a or not vec_b:
            return 0.0
        limit = min(len(vec_a), len(vec_b))
        if limit <= 0:
            return 0.0
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for i in range(limit):
            a = float(vec_a[i])
            b = float(vec_b[i])
            dot += a * b
            norm_a += a * a
            norm_b += b * b
        if norm_a <= 0.0 or norm_b <= 0.0:
            return 0.0
        return dot / ((norm_a ** 0.5) * (norm_b ** 0.5))

    @staticmethod
    def _normalize_time_window_inputs(start_time: str, end_time: Optional[str]) -> Tuple[str, str]:
        start_text = str(start_time or "").strip()
        end_text = str(end_time or "").strip()

        # Backward compatibility: allow legacy "start,end" packed into the first argument.
        if start_text and not end_text and "," in start_text:
            left, right = start_text.split(",", 1)
            start_text = left.strip()
            end_text = right.strip()

        if start_text and not end_text:
            end_text = start_text

        return start_text, end_text

    def _parse_time_window(self, start_time: str, end_time: str) -> Tuple[Optional[datetime], Optional[datetime]]:
        start = self._parse_iso_datetime(start_time)
        end = self._parse_iso_datetime(end_time)
        if start is None or end is None:
            return None, None
        if start > end:
            start, end = end, start
        return start, end

    @staticmethod
    def _parse_iso_datetime(raw_value: str) -> Optional[datetime]:
        value = str(raw_value or "").strip()
        if not value:
            return None
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(value)
        except Exception:
            return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    @staticmethod
    def _now_iso() -> str:
        return datetime.utcnow().isoformat() + "Z"


def create_default_entity_profile_service(
    llm_func: Callable[[str], str],
    embed_func: Callable[[str], List[float]],
    memory_root: str,
    profile_data_path: Optional[str] = None,
    facts_situation_path: Optional[str] = None,
    prompt_path: Optional[str] = None,
    similarity_threshold: float = 0.78,
    top_k: int = 3,
    auto_merge_threshold: float = 0.93,
    enable_summary_llm: bool = True,
    enable_progress: bool = True,
    auto_align_on_init: bool = True,
    rebuild_checkpoint_every: int = 100,
) -> EntityProfileService:
    """Convenience constructor for default entity profile service."""

    return EntityProfileService(
        llm_func=llm_func,
        embed_func=embed_func,
        memory_root=memory_root,
        profile_data_path=profile_data_path,
        facts_situation_path=facts_situation_path,
        prompt_path=prompt_path,
        similarity_threshold=similarity_threshold,
        top_k=top_k,
        auto_merge_threshold=auto_merge_threshold,
        enable_summary_llm=enable_summary_llm,
        enable_progress=enable_progress,
        auto_align_on_init=auto_align_on_init,
        rebuild_checkpoint_every=rebuild_checkpoint_every,
    )
