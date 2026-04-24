#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hard requirements for workflows that depend on Neo4j."""

from __future__ import annotations

from typing import Optional

from m_agent.memory.memory_core.core.neo4j_store import Neo4jStore


def require_neo4j_for_segment_entity_build(database: Optional[str]) -> None:
    """
    Facts-only segment entity pipeline must not degrade when Neo4j is down.
    Raises RuntimeError if the driver is unavailable or the session cannot run a read.
    """
    store = Neo4jStore.instance()
    if not store.available or store.driver is None:
        raise RuntimeError(
            "Neo4j is required for facts_only segment entity profile build but is not available"
        )
    try:
        store.run("RETURN 1 AS ok", {}, write=False, database=database)
    except Exception as exc:
        raise RuntimeError(
            "Neo4j connectivity check failed for segment entity profile build"
        ) from exc
