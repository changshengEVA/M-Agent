#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实体属性/特征查询 workflow 实现。
"""

import logging
import math
import re
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def query_entity_property(
    entity_uid: str,
    query_text: str,
    kg_base: Any,
    entity_resolution_service: Any,
    embed_func: Callable[[str], List[float]],
    llm_func: Callable[[str], str],
    similarity_threshold: float = 0.7,
    top_k: int = 3,
) -> Dict[str, Any]:
    """
    查询实体属性或抽象特征及其证据来源。

    匹配策略与实体解析接口一致：
    1. query embedding 与属性 field / 特征 feature embedding 做相似度匹配
    2. 构建阈值候选与 top_k 候选
    3. 若阈值候选数量 > top_k，则 LLM 使用阈值候选；否则使用 top_k 候选
    4. LLM 判别是否命中
    """
    result: Dict[str, Any] = {
        "hit": False,
        "entity_uid": entity_uid,
        "query_text": query_text,
        "content": None,
        "top_similar_contents": [],
    }

    if not isinstance(entity_uid, str) or not entity_uid.strip():
        return result
    if not isinstance(query_text, str) or not query_text.strip():
        return result

    entity_id = _resolve_entity_id_from_uid(
        entity_uid=entity_uid.strip(),
        kg_base=kg_base,
    )
    if not entity_id:
        logger.info(f"query_entity_property 未找到实体: uid={entity_uid}")
        return result

    query = query_text.strip()
    try:
        query_embedding = embed_func(query)
    except Exception as e:
        logger.warning(f"query_entity_property 生成 query embedding 失败: {e}")
        return result

    if not isinstance(query_embedding, list) or not query_embedding:
        logger.warning("query_entity_property query embedding 为空")
        return result

    attributes = kg_base.repos.attribute.list(entity_id)
    features = kg_base.repos.feature.list(entity_id)
    candidates: List[Dict[str, Any]] = []

    for idx, attr in enumerate(attributes):
        field = attr.get("field")
        if not isinstance(field, str) or not field.strip():
            continue

        field_embedding = attr.get("field_embedding")
        if not (isinstance(field_embedding, list) and field_embedding):
            try:
                field_embedding = embed_func(field)
            except Exception:
                field_embedding = None

        similarity = _cosine_similarity(query_embedding, field_embedding)
        candidates.append(
            {
                "candidate_id": f"A{idx}",
                "type": "attribute",
                "field": field,
                "value": attr.get("value"),
                "similarity": similarity,
                "source": attr.get("sources", []),
            }
        )

    for idx, feat in enumerate(features):
        feature_text = feat.get("feature")
        if not isinstance(feature_text, str) or not feature_text.strip():
            continue

        feature_embedding = feat.get("feature_embedding")
        if not (isinstance(feature_embedding, list) and feature_embedding):
            try:
                feature_embedding = embed_func(feature_text)
            except Exception:
                feature_embedding = None

        similarity = _cosine_similarity(query_embedding, feature_embedding)
        candidates.append(
            {
                "candidate_id": f"F{idx}",
                "type": "feature",
                "feature": feature_text,
                "similarity": similarity,
                "source": feat.get("sources", []),
            }
        )

    if not candidates:
        return result

    candidates.sort(key=lambda x: x["similarity"], reverse=True)

    threshold = float(similarity_threshold)
    topk = max(1, int(top_k))
    if getattr(entity_resolution_service, "strategies", None):
        strategy = entity_resolution_service.strategies[0]
        threshold = float(getattr(strategy, "similarity_threshold", threshold))
        topk = max(1, int(getattr(strategy, "top_k", topk)))

    threshold_candidates = [c for c in candidates if c["similarity"] >= threshold]
    topk_candidates = candidates[:topk]
    llm_candidates = threshold_candidates if len(threshold_candidates) > topk else topk_candidates

    result["top_similar_contents"] = [_strip_property_candidate(c) for c in candidates[:5]]

    target_candidate_id = _llm_judge_property(
        query_text=query,
        candidates=llm_candidates,
        llm_func=llm_func,
    )
    if not target_candidate_id:
        return result

    target = next((c for c in llm_candidates if c["candidate_id"] == target_candidate_id), None)
    if not target:
        return result

    result["hit"] = True
    result["content"] = _strip_property_candidate(target)
    return result


def _resolve_entity_id_from_uid(entity_uid: str, kg_base: Any) -> Optional[str]:
    """
    将外部 entity_uid 映射回 KG 内部 entity_id。
    兼容直接传入 entity_id 的情况。
    """
    if kg_base.repos.entity.exists(entity_uid):
        return entity_uid

    for entity_id in kg_base.list_entity_ids():
        success, entity_data = kg_base.repos.entity.load(entity_id)
        if not success or not isinstance(entity_data, dict):
            continue
        uid = entity_data.get("uid")
        if isinstance(uid, str) and uid.strip() == entity_uid:
            return entity_id

    return None


def _cosine_similarity(vec_a: Any, vec_b: Any) -> float:
    """
    计算余弦相似度，输入非法时返回 0.0。
    """
    if not isinstance(vec_a, list) or not isinstance(vec_b, list):
        return 0.0
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for a, b in zip(vec_a, vec_b):
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            return 0.0
        dot += float(a) * float(b)
        norm_a += float(a) * float(a)
        norm_b += float(b) * float(b)

    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _strip_property_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    裁剪候选信息用于外部返回，避免暴露内部字段。
    """
    base = {
        "type": candidate.get("type"),
        "similarity": float(candidate.get("similarity", 0.0)),
        "source": candidate.get("source", []),
    }
    if candidate.get("type") == "attribute":
        base["field"] = candidate.get("field")
        base["value"] = candidate.get("value")
    else:
        base["feature"] = candidate.get("feature")
    return base


def _llm_judge_property(
    query_text: str,
    candidates: List[Dict[str, Any]],
    llm_func: Callable[[str], str],
) -> Optional[str]:
    """
    使用 LLM 在候选内容中做最终判别，返回 candidate_id 或 None。
    """
    if not candidates:
        return None

    candidate_lines: List[str] = []
    for idx, c in enumerate(candidates, start=1):
        if c.get("type") == "attribute":
            desc = f"属性字段={c.get('field')}，属性值={c.get('value')}"
        else:
            desc = f"特征={c.get('feature')}"
        candidate_lines.append(
            f"{idx}. candidate_id={c.get('candidate_id')}, type={c.get('type')}, "
            f"similarity={float(c.get('similarity', 0.0)):.4f}, {desc}"
        )

    prompt = (
        "你是实体属性检索判别器。\n"
        f"查询文本：\"{query_text}\"\n\n"
        "候选列表：\n"
        + "\n".join(candidate_lines)
        + "\n\n请判断查询是否命中某个候选。\n"
          "如果命中，请只返回 candidate_id（例如 A0 或 F1）。\n"
          "如果都不命中，请只返回 NO_HIT。"
    )

    try:
        llm_response = llm_func(prompt)
    except Exception as e:
        logger.warning(f"query_entity_property LLM 判别失败: {e}")
        return None

    if not isinstance(llm_response, str):
        return None

    response = llm_response.strip()
    if not response:
        return None
    if response.upper() in {"NO_HIT", "NEW_ENTITY", "NONE"}:
        return None

    valid_ids = {str(c.get("candidate_id")) for c in candidates}
    if response in valid_ids:
        return response

    id_match = re.search(r"\b([AF]\d+)\b", response.upper())
    if id_match:
        candidate_id = id_match.group(1)
        if candidate_id in valid_ids:
            return candidate_id

    if response.isdigit():
        idx = int(response) - 1
        if 0 <= idx < len(candidates):
            return str(candidates[idx].get("candidate_id"))

    return None
