#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Merge helpers for segment-derived entity statuses (field_canonical, replace/append).

Schema alignment (v1): attributes are list[dict] with
field, update_mode, field_canonical, value (list[str]), evidence_refs (list[str|dict]).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

# Minimal synonym groups -> first element is canonical field key for matching.
_FIELD_SYNONYMS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("hobby", ("hobby", "hobbies", "interests", "likes", "interest")),
    ("age", ("age", "age_group", "age_range")),
    ("occupation", ("occupation", "job", "work", "career")),
    ("location", ("location", "place", "city", "country")),
)


def canonical_field_group(field: str) -> List[str]:
    f = str(field or "").strip().lower()
    if not f:
        return []
    for canon, syns in _FIELD_SYNONYMS:
        if f == canon or f in syns:
            return list(dict.fromkeys([canon, *syns]))
    return [f]


def slot_key(row: Dict[str, Any]) -> str:
    fc = row.get("field_canonical")
    if isinstance(fc, list) and fc:
        return str(fc[0] or "").strip().lower()
    return str(row.get("field") or "").strip().lower()


def normalize_value_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value).strip()
    return [s] if s else []


def merge_attribute_rows(
    existing: List[Dict[str, Any]],
    incoming: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge incoming status rows into existing (by field_canonical / field)."""
    rows: Dict[str, Dict[str, Any]] = {}
    for item in existing or []:
        if not isinstance(item, dict):
            continue
        k = slot_key(item)
        if not k:
            continue
        rows[k] = dict(item)

    for item in incoming or []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        if not field:
            continue
        fc_list = item.get("field_canonical")
        if not isinstance(fc_list, list) or not fc_list:
            fc_list = canonical_field_group(field)
        mode = str(item.get("update_mode") or "append").strip().lower()
        if mode not in {"replace", "append"}:
            mode = "append"
        values = normalize_value_list(item.get("value"))
        ev = item.get("evidence_refs")
        if not isinstance(ev, list):
            ev = []
        key = str(fc_list[0] or field).strip().lower()
        prev = rows.get(key)
        if prev is None:
            rows[key] = {
                "field": fc_list[0] if fc_list else field,
                "update_mode": mode,
                "field_canonical": fc_list,
                "value": list(values),
                "evidence_refs": list(ev),
            }
            continue
        old_vals = normalize_value_list(prev.get("value"))
        old_ev = prev.get("evidence_refs")
        if not isinstance(old_ev, list):
            old_ev = []
        if mode == "replace":
            merged_vals = list(values)
        else:
            seen = {v.lower() for v in old_vals}
            merged_vals = list(old_vals)
            for v in values:
                if v.lower() not in seen:
                    seen.add(v.lower())
                    merged_vals.append(v)
        merged_ev = list(old_ev) + [x for x in ev if x not in old_ev]
        rows[key] = {
            "field": prev.get("field") or field,
            "update_mode": mode,
            "field_canonical": fc_list,
            "value": merged_vals,
            "evidence_refs": merged_ev,
        }
    return list(rows.values())


def build_profile_summary_value(
    *,
    canonical_name: str,
    entity_type: str,
    attributes: List[Dict[str, Any]],
) -> str:
    """Deterministic summary (no LLM); excludes events per Entity.yaml."""
    name = str(canonical_name or "").strip() or "Unknown"
    et = str(entity_type or "other").strip() or "other"
    lines = [f"Entity: {name} (type: {et})"]
    for row in attributes or []:
        if not isinstance(row, dict):
            continue
        field = str(row.get("field") or "").strip()
        vals = normalize_value_list(row.get("value"))
        if not field or not vals:
            continue
        lines.append(f"- {field}: {', '.join(vals)}")
    return "\n".join(lines).strip()
