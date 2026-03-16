#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Service workflow: entity resolution pass execution."""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def run_entity_resolution_pass(memory_core: Any) -> Dict[str, Any]:
    """
    Execute the entity resolution pass in four phases:
    1) collect decisions, 2) build identity graph,
    3) connected components, 4) stable merge.
    """
    logger.info("Start entity resolution pass")

    logger.info("Phase 1: collect decisions")
    decisions = memory_core.entity_resolution_service.resolve_unresolved_entities()
    logger.info("Collected decisions: %s", len(decisions))

    results: Dict[str, Any] = {
        "total_decisions": len(decisions),
        "same_as_existing": 0,
        "new_entities": 0,
        "merged": 0,
        "merge_errors": [],
        "decisions": [],
        "identity_groups": 0,
        "canonical_entities": 0,
    }

    for decision in decisions:
        decision_dict = decision.to_dict()
        results["decisions"].append(decision_dict)
        if decision.is_same_as_existing():
            results["same_as_existing"] += 1
        elif decision.is_new_entity():
            results["new_entities"] += 1

    logger.info("Phase 2: build identity graph")
    same_as_relations = []
    entity_set = set()
    for decision in decisions:
        if decision.is_same_as_existing() and decision.target_entity_id:
            source = decision.source_entity_id
            target = decision.target_entity_id
            same_as_relations.append((source, target))
            entity_set.add(source)
            entity_set.add(target)

    logger.info(
        "Identity graph built: relations=%s entities=%s",
        len(same_as_relations),
        len(entity_set),
    )

    logger.info("Phase 3: connected components")
    parent: Dict[str, str] = {}
    rank: Dict[str, int] = {}

    def find(x: str) -> str:
        if x not in parent:
            parent[x] = x
            rank[x] = 0
            return x
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x: str, y: str) -> None:
        root_x = find(x)
        root_y = find(y)
        if root_x == root_y:
            return
        if rank[root_x] < rank[root_y]:
            parent[root_x] = root_y
        elif rank[root_x] > rank[root_y]:
            parent[root_y] = root_x
        else:
            parent[root_y] = root_x
            rank[root_x] += 1

    for source, target in same_as_relations:
        union(source, target)

    groups: Dict[str, list[str]] = {}
    for entity in entity_set:
        root = find(entity)
        groups.setdefault(root, []).append(entity)

    identity_groups = {root: entities for root, entities in groups.items() if len(entities) > 1}
    results["identity_groups"] = len(identity_groups)
    logger.info("Connected components: %s", len(identity_groups))

    logger.info("Phase 4: stable merge")
    for _, entities in identity_groups.items():
        target_count: Dict[str, int] = {}
        for decision in decisions:
            if decision.is_same_as_existing() and decision.target_entity_id:
                target = decision.target_entity_id
                if target in entities:
                    target_count[target] = target_count.get(target, 0) + 1

        canonical = max(target_count.items(), key=lambda x: x[1])[0] if target_count else entities[0]
        results["canonical_entities"] += 1
        logger.info("Identity group canonical: %s <- %s", canonical, entities)

        for entity in entities:
            if entity == canonical:
                continue
            try:
                merge_result = memory_core.kg_base.merge_entities(
                    target_id=canonical,
                    source_id=entity,
                )
                if merge_result.get("success", False):
                    results["merged"] += 1
                else:
                    details = merge_result.get("details", {}) if isinstance(merge_result, dict) else {}
                    error_msg = f"merge failed: {details.get('error', 'unknown')}"
                    results["merge_errors"].append(
                        {"source": entity, "target": canonical, "error": error_msg}
                    )
                    logger.warning("Entity merge failed: %s -> %s (%s)", entity, canonical, error_msg)
            except Exception as exc:
                error_msg = f"merge exception: {exc}"
                results["merge_errors"].append(
                    {"source": entity, "target": canonical, "error": error_msg}
                )
                logger.error("Entity merge exception: %s -> %s (%s)", entity, canonical, exc)

    for decision in decisions:
        if decision.is_new_entity():
            logger.info("New-entity decision only (no action): %s", decision.source_entity_id)

    logger.info(
        "Entity resolution pass done: decisions=%s same_as=%s new=%s groups=%s canonical=%s merged=%s",
        results["total_decisions"],
        results["same_as_existing"],
        results["new_entities"],
        results["identity_groups"],
        results["canonical_entities"],
        results["merged"],
    )
    results["success"] = len(results["merge_errors"]) == 0
    return results

