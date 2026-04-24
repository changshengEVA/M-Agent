from __future__ import annotations

import pytest

from m_agent.memory.memory_core.core.neo4j_require import require_neo4j_for_segment_entity_build

try:
    from m_agent.memory.memory_core.core.neo4j_store import Neo4jInitializationError, Neo4jStore
except Exception:  # pragma: no cover
    Neo4jInitializationError = Exception  # type: ignore
    Neo4jStore = None  # type: ignore


def test_require_neo4j_skips_or_passes() -> None:
    if Neo4jStore is None:
        pytest.skip("Neo4jStore unavailable")
    try:
        store = Neo4jStore.instance()
    except Neo4jInitializationError:
        pytest.skip("Neo4j not reachable (fail-fast init)")
    if not store.available:
        with pytest.raises(RuntimeError, match="Neo4j is required"):
            require_neo4j_for_segment_entity_build(None)
    else:
        require_neo4j_for_segment_entity_build(None)
