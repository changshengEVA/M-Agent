#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实体解析（Entity Grounding）workflow 实现。
"""

import hashlib
import json
import logging
from typing import Dict, Any, Optional

from memory.memory_core.services_bank.entity_resolution.service import EntityResolutionService

logger = logging.getLogger(__name__)


def resolve_entity(
    name: str,
    entity_resolution_service: EntityResolutionService,
    kg_base: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    将实体名称解析为系统内部唯一实体标识。

    Args:
        name: 实体名称（可为规范名或别名）
        entity_resolution_service: 实体解析服务实例

    Returns:
        {
          "hit": bool,
          "entity_uid": str | None,
          "canonical_name": str | None,
          "aliases": list,
          "top_similar_entities": list  # hit=False 时返回
        }
    """
    empty_result = {
        "hit": False,
        "entity_uid": None,
        "canonical_name": None,
        "aliases": [],
        "top_similar_entities": []
    }

    if not isinstance(name, str):
        return empty_result

    query_name = name.strip()
    if not query_name:
        return empty_result

    entity_library = entity_resolution_service.entity_library

    resolved_true = 0
    resolved_false = 0
    for _entity_id, _record in entity_library.entities.items():
        if getattr(_record, "resolved", False):
            resolved_true += 1
        else:
            resolved_false += 1
    logger.debug(
        "Grounding状态: query=%s, entity_total=%d, resolved_true=%d, resolved_false=%d",
        query_name,
        len(entity_library.entities),
        resolved_true,
        resolved_false,
    )

    def _resolve_uid(entity_id: str) -> str:
        """
        将实体ID映射为真实UID（若无法获取则回退为实体ID）。
        """
        if kg_base is None:
            return entity_id
        try:
            success, entity_data = kg_base.repos.entity.load(entity_id)
            if success and isinstance(entity_data, dict):
                uid = entity_data.get("uid")
                if isinstance(uid, str) and uid.strip():
                    return uid.strip()
        except Exception as exc:
            logger.debug("读取实体UID失败: %s, err=%s", entity_id, exc)
        return entity_id

    def _build_hit_result(entity_id: str) -> Dict[str, Any]:
        record = entity_library.get_entity(entity_id)
        entity_uid = _resolve_uid(entity_id)
        if not record:
            return {
                "hit": True,
                "entity_uid": entity_uid,
                "canonical_name": entity_id,
                "aliases": [],
            }
        return {
            "hit": True,
            "entity_uid": entity_uid,
            "canonical_name": record.canonical_name,
            "aliases": list(record.aliases),
        }

    def _build_candidate(entity_id: str, similarity: float) -> Dict[str, Any]:
        record = entity_library.get_entity(entity_id)
        canonical_name = record.canonical_name if record else entity_id
        return {
            "entity_uid": _resolve_uid(entity_id),
            "canonical_name": canonical_name,
            "similarity": float(similarity),
        }

    # 先尝试精确命中（规范名 / 别名）
    record = entity_library.get_entity_by_name(query_name)
    if record:
        return _build_hit_result(record.entity_id)

    # 文本检索兜底（子串匹配）
    text_candidates = entity_library.search(query_name, max_results=1)
    if text_candidates:
        return _build_hit_result(text_candidates[0][0])

    # 向量检索兜底：对 query_name 直接生成 embedding，再在实体库中检索
    # 这一步不要求 query_name 先存在于实体库中。
    threshold = 0.7
    top_k = 3
    strategy = None
    if entity_resolution_service.strategies:
        strategy = entity_resolution_service.strategies[0]
        if hasattr(strategy, "similarity_threshold"):
            threshold = float(getattr(strategy, "similarity_threshold"))
        if hasattr(strategy, "top_k"):
            top_k = max(1, int(getattr(strategy, "top_k")))

    try:
        query_embedding = entity_resolution_service.embed_func(query_name)
        if isinstance(query_embedding, list) and query_embedding:
            # 先拿到不受阈值限制的 top5，用于 miss 时返回
            top5_candidates = entity_library.search_by_embedding(
                embedding=query_embedding,
                threshold=-1.0,
                top_k=5,
                exclude_unresolved=True
            )

            # 候选选择规则：
            # 1) 如果“超过阈值”的候选数量 > top_k，LLM使用全部超过阈值候选
            # 2) 否则，LLM使用 top_k 个最高相似度候选
            all_threshold_candidates = entity_library.search_by_embedding(
                embedding=query_embedding,
                threshold=threshold,
                top_k=max(1, entity_library.get_entity_count()),
                exclude_unresolved=True
            )
            topk_candidates = entity_library.search_by_embedding(
                embedding=query_embedding,
                threshold=-1.0,
                top_k=top_k,
                exclude_unresolved=True
            )
            raw_top10_candidates = entity_library.search_by_embedding(
                embedding=query_embedding,
                threshold=-1.0,
                top_k=10,
                exclude_unresolved=True
            )

            embedding_sig_src = ",".join(f"{x:.6f}" for x in query_embedding[:32]).encode("utf-8")
            embedding_sig = hashlib.sha1(embedding_sig_src).hexdigest()
            logger.debug(
                "QueryEmbedding: query=%s, dim=%d, sig=%s",
                query_name,
                len(query_embedding),
                embedding_sig
            )
            logger.debug(
                "EmbeddingTop10: %s",
                json.dumps(
                    [
                        _build_candidate(entity_id, similarity)
                        for entity_id, similarity in raw_top10_candidates
                    ],
                    ensure_ascii=False
                )
            )
            llm_candidates = (
                all_threshold_candidates
                if len(all_threshold_candidates) > top_k
                else topk_candidates
            )

            if llm_candidates:
                llm_candidate_pool = []
                for entity_id, similarity in llm_candidates:
                    record = entity_library.get_entity(entity_id)
                    llm_candidate_pool.append({
                        "entity_id": entity_id,
                        "entity_uid": _resolve_uid(entity_id),
                        "canonical_name": record.canonical_name if record else entity_id,
                        "similarity": float(similarity),
                    })

                logger.debug(
                    "LLM候选池: query=%s, threshold=%.4f, top_k=%d, threshold_hits=%d, llm_pool_size=%d, exclude_unresolved=%s",
                    query_name,
                    threshold,
                    top_k,
                    len(all_threshold_candidates),
                    len(llm_candidate_pool),
                    True,
                )
                logger.debug(
                    "LLM候选池详情: %s",
                    json.dumps(llm_candidate_pool, ensure_ascii=False)
                )

                llm_target_id = None
                if strategy and hasattr(strategy, "_llm_judgment"):
                    try:
                        llm_target_id = strategy._llm_judgment(
                            query_name,
                            llm_candidates,
                            entity_library
                        )
                    except Exception as exc:
                        logger.debug("LLM 判别失败，回退为未命中: %s", exc)
                logger.debug(
                    "LLM判别结果: query=%s, llm_target_id=%s",
                    query_name,
                    llm_target_id if llm_target_id else "NEW_ENTITY"
                )

                if llm_target_id:
                    return _build_hit_result(llm_target_id)

                miss_result = dict(empty_result)
                miss_result["top_similar_entities"] = [
                    _build_candidate(entity_id, similarity)
                    for entity_id, similarity in top5_candidates
                ]
                return miss_result
    except Exception as exc:
        logger.debug("query embedding 检索失败: %s", exc)

    logger.debug("实体未命中: %s", query_name)
    return empty_result
