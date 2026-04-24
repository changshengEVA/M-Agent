from __future__ import annotations

from m_agent.memory.memory_core.workflow.build.entity_segment_merge import (
    build_profile_summary_value,
    merge_attribute_rows,
)


def test_merge_attribute_rows_replace_and_append() -> None:
    existing = [
        {
            "field": "hobby",
            "update_mode": "append",
            "field_canonical": ["hobby", "interests"],
            "value": ["reading"],
            "evidence_refs": [],
        }
    ]
    incoming = [
        {"field": "interests", "update_mode": "append", "value": ["swimming"]},
        {"field": "age", "update_mode": "replace", "value": ["30"]},
    ]
    merged = merge_attribute_rows(existing, incoming)
    by_field = {row["field"]: row for row in merged}
    assert set(by_field) == {"hobby", "age"}
    assert set(by_field["hobby"]["value"]) == {"reading", "swimming"}
    assert by_field["age"]["value"] == ["30"]


def test_build_profile_summary_value_smoke() -> None:
    text = build_profile_summary_value(
        canonical_name="Ada",
        entity_type="person",
        attributes=[{"field": "hobby", "value": ["logic"]}],
    )
    assert "Ada" in text
    assert "person" in text
    assert "hobby" in text
