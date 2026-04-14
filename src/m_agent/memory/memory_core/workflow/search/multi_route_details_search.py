# -*- coding: utf-8 -*-
"""
Multi-route detail search workflow.

Pipeline:
1) generate route-specific query rewrites (LLM or template)
2) execute `search_details` in parallel for each route
3) deduplicate and fuse route rankings
4) return unified result format with route diagnostics
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, Tuple

from .details_search import search_details

logger = logging.getLogger(__name__)

_DEFAULT_ROUTE_TYPES = ["entity", "action", "time", "relation", "topic"]
_DEFAULT_QUERY_GENERATOR = "llm"

_MULTI_QUERY_PROMPT_TEMPLATE = """You are a retrieval query rewrite assistant.
Given one user query, generate {query_count} complementary retrieval queries that improve recall from different angles.

Constraints:
1. Keep each query concise and specific.
2. Each query should focus on a different angle or missing detail.
3. Do not repeat the original query verbatim.
4. Return valid JSON only.

Output JSON format:
{{
  "queries": [
    "query 1",
    "query 2"
  ]
}}

Original query:
{query}
"""


def search_details_multi_route(
    detail_query: str,
    scene_dir: Path,
    embed_func: Callable[[str], List[float]],
    topk: int = 5,
    hybrid_config: Dict[str, Any] | None = None,
    route_config: Dict[str, Any] | None = None,
    llm_func: Callable[[str], str] | None = None,
) -> Dict[str, Any]:
    query = str(detail_query or "").strip()
    safe_topk = _safe_int(topk, default=5, minimum=1)
    cfg = _resolve_route_config(route_config)
    per_route_topk = max(safe_topk, _safe_int(cfg.get("per_route_topk"), default=safe_topk, minimum=1))

    if not query:
        return _empty_result(safe_topk)

    routes = _build_routes(
        query=query,
        route_types=cfg["route_types"],
        route_count=int(cfg["route_count"]),
        query_generator=str(cfg["query_generator"]),
        llm_func=llm_func,
    )
    if len(routes) <= 1:
        single = search_details(
            detail_query=query,
            scene_dir=scene_dir,
            embed_func=embed_func,
            topk=safe_topk,
            hybrid_config=hybrid_config,
        )
        single["route_diagnostics"] = [
            {
                "route_type": "base",
                "query": query,
                "hit_count": len(single.get("results", [])) if isinstance(single.get("results"), list) else 0,
                "status": "ok",
            }
        ]
        return single

    route_results: List[Dict[str, Any]] = []
    all_failed = True
    max_workers = max(1, _safe_int(cfg.get("max_workers"), default=len(routes), minimum=1))
    with ThreadPoolExecutor(max_workers=min(max_workers, len(routes))) as pool:
        future_map = {
            pool.submit(
                search_details,
                detail_query=route["query"],
                scene_dir=scene_dir,
                embed_func=embed_func,
                topk=per_route_topk,
                hybrid_config=hybrid_config,
            ): route
            for route in routes
        }
        for future in as_completed(future_map):
            route = future_map[future]
            route_type = str(route["route_type"])
            route_query = str(route["query"])
            try:
                payload = future.result()
                if isinstance(payload, dict):
                    all_failed = False
                else:
                    payload = _empty_result(per_route_topk)
                route_results.append(
                    {
                        "route_type": route_type,
                        "query": route_query,
                        "payload": payload,
                        "status": "ok",
                    }
                )
            except Exception as exc:
                logger.warning(
                    "search_details_multi_route route failed(route=%s, query=%s): %s",
                    route_type,
                    route_query,
                    exc,
                )
                route_results.append(
                    {
                        "route_type": route_type,
                        "query": route_query,
                        "payload": _empty_result(per_route_topk),
                        "status": f"failed:{exc}",
                    }
                )

    if all_failed:
        logger.warning("search_details_multi_route all routes failed, fallback to single route.")
        fallback = search_details(
            detail_query=query,
            scene_dir=scene_dir,
            embed_func=embed_func,
            topk=safe_topk,
            hybrid_config=hybrid_config,
        )
        fallback["route_diagnostics"] = [
            {"route_type": "base", "query": query, "hit_count": len(fallback.get("results", [])), "status": "fallback"}
        ]
        return fallback

    fused = _fuse_route_results(
        route_results=route_results,
        fusion=str(cfg["fusion"]),
        route_weights=cfg["route_weights"],
        rrf_k=_safe_int(cfg.get("rrf_k"), default=60, minimum=1),
        topk=safe_topk,
    )
    return fused


def _fuse_route_results(
    *,
    route_results: List[Dict[str, Any]],
    fusion: str,
    route_weights: Dict[str, float],
    rrf_k: int,
    topk: int,
) -> Dict[str, Any]:
    scored: Dict[str, Dict[str, Any]] = {}
    diagnostics: List[Dict[str, Any]] = []
    total_scene_count = 0
    total_fact_count = 0

    for route_item in route_results:
        route_type = str(route_item.get("route_type", "base"))
        route_query = str(route_item.get("query", ""))
        payload = route_item.get("payload") if isinstance(route_item.get("payload"), dict) else {}
        status = str(route_item.get("status", "ok"))
        results = payload.get("results")
        if not isinstance(results, list):
            results = []

        total_scene_count = max(total_scene_count, _safe_int(payload.get("total_scene_count"), default=0, minimum=0))
        total_fact_count = max(total_fact_count, _safe_int(payload.get("total_fact_count"), default=0, minimum=0))

        diagnostics.append(
            {
                "route_type": route_type,
                "query": route_query,
                "hit_count": len(results),
                "status": status,
            }
        )

        route_weight = float(route_weights.get(route_type, route_weights.get("default", 1.0)))
        for rank, item in enumerate(results, start=1):
            if not isinstance(item, dict):
                continue
            dedup_key = _build_dedup_key(item)
            if not dedup_key:
                continue

            if fusion == "weighted":
                contribution = route_weight * float(max(topk - rank + 1, 0))
            else:
                contribution = route_weight / float(rrf_k + rank)

            node = scored.get(dedup_key)
            if node is None:
                node = {
                    "item": item,
                    "fused_score": 0.0,
                    "routes": [],
                }
                scored[dedup_key] = node
            node["fused_score"] = float(node.get("fused_score", 0.0)) + float(contribution)
            node["routes"].append({"route_type": route_type, "rank": rank, "contribution": contribution})

    ranked_nodes = sorted(
        scored.values(),
        key=lambda node: float(node.get("fused_score", 0.0)),
        reverse=True,
    )
    final_results: List[Dict[str, Any]] = []
    for node in ranked_nodes[: max(1, int(topk))]:
        item = node.get("item", {})
        if not isinstance(item, dict):
            continue
        result_item = {
            "scene_id": str(item.get("scene_id", "")),
            "similarity": float(item.get("similarity", 0.0)),
            "Atomic fact": str(item.get("Atomic fact", "")),
            "evidence": item.get("evidence", {}),
            "fused_score": float(node.get("fused_score", 0.0)),
            "route_sources": node.get("routes", []),
        }
        final_results.append(result_item)

    return {
        "hit": len(final_results) > 0,
        "topk": max(1, int(topk)),
        "total_scene_count": total_scene_count,
        "total_fact_count": total_fact_count,
        "matched_count": len(final_results),
        "results": final_results,
        "route_diagnostics": diagnostics,
    }


def _build_routes(
    query: str,
    route_types: Sequence[str],
    route_count: int,
    query_generator: str,
    llm_func: Callable[[str], str] | None,
) -> List[Dict[str, str]]:
    routes: List[Dict[str, str]] = [{"route_type": "base", "query": query}]
    max_routes = max(1, int(route_count))
    normalized_generator = str(query_generator or "").strip().lower() or _DEFAULT_QUERY_GENERATOR

    if normalized_generator == "llm":
        llm_queries = _generate_complementary_queries_with_llm(
            query=query,
            llm_func=llm_func,
            max_queries=max(0, max_routes - len(routes)),
        )
        for generated_query in llm_queries:
            if len(routes) >= max_routes:
                break
            if _contains_query(routes, generated_query):
                continue
            routes.append({"route_type": "llm", "query": generated_query})
        return routes

    for route_type in route_types:
        if len(routes) >= max_routes:
            break
        rewritten = _rewrite_query(query, route_type)
        if not rewritten or rewritten == query:
            continue
        if _contains_query(routes, rewritten):
            continue
        routes.append({"route_type": str(route_type), "query": rewritten})
    return routes


def _generate_complementary_queries_with_llm(
    *,
    query: str,
    llm_func: Callable[[str], str] | None,
    max_queries: int,
) -> List[str]:
    if max_queries <= 0:
        return []
    if llm_func is None:
        logger.warning("search_details_multi_route llm query generation skipped: llm_func is None")
        return []

    prompt = _MULTI_QUERY_PROMPT_TEMPLATE.format(
        query_count=max(1, int(max_queries)),
        query=query,
    )
    raw_response = ""
    try:
        llm_output = llm_func(prompt)
        raw_response = str(getattr(llm_output, "content", llm_output) or "").strip()
    except Exception as exc:
        logger.warning("search_details_multi_route llm query generation failed: %s", exc)
        return []

    parsed_queries = _parse_multi_query_llm_response(raw_response)
    if not parsed_queries:
        return []

    deduped: List[str] = []
    normalized_original = str(query or "").strip().lower()
    for item in parsed_queries:
        candidate = str(item or "").strip()
        if not candidate:
            continue
        if candidate.lower() == normalized_original:
            continue
        if _contains_query([{"query": q} for q in deduped], candidate):
            continue
        deduped.append(candidate)
        if len(deduped) >= max(1, int(max_queries)):
            break
    return deduped


def _parse_multi_query_llm_response(raw: str) -> List[str]:
    payload = str(raw or "").strip()
    if not payload:
        return []

    fenced = re.search(r"```(?:json)?\s*(.*?)```", payload, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        payload = fenced.group(1).strip()

    parsed_payload = _try_parse_json(payload)
    if isinstance(parsed_payload, dict):
        queries = parsed_payload.get("queries")
        if isinstance(queries, list):
            return [str(item).strip() for item in queries if str(item).strip()]
    if isinstance(parsed_payload, list):
        return [str(item).strip() for item in parsed_payload if str(item).strip()]

    fallback_lines: List[str] = []
    for raw_line in payload.splitlines():
        line = re.sub(r"^\s*(?:[-*]\s*|\d+[.)]\s*)", "", raw_line).strip()
        if not line:
            continue
        fallback_lines.append(line)
    return fallback_lines


def _try_parse_json(payload: str) -> Any:
    text = str(payload or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass

    object_match = re.search(r"\{[\s\S]*\}", text)
    if object_match:
        try:
            return json.loads(object_match.group(0))
        except Exception:
            pass

    array_match = re.search(r"\[[\s\S]*\]", text)
    if array_match:
        try:
            return json.loads(array_match.group(0))
        except Exception:
            pass
    return None


def _contains_query(routes: Sequence[Dict[str, str]], candidate_query: str) -> bool:
    normalized = str(candidate_query or "").strip().lower()
    if not normalized:
        return False
    for route in routes:
        query = str(route.get("query", "")).strip().lower() if isinstance(route, dict) else ""
        if query == normalized:
            return True
    return False


def _rewrite_query(query: str, route_type: str) -> str:
    normalized_type = str(route_type or "").strip().lower()
    if normalized_type == "entity":
        return f"{query} person name identity"
    if normalized_type == "action":
        return f"{query} action event behavior"
    if normalized_type == "time":
        return f"{query} time date when"
    if normalized_type == "relation":
        return f"{query} relation with who"
    if normalized_type == "topic":
        return f"{query} topic summary context"
    return query


def _build_dedup_key(item: Dict[str, Any]) -> str:
    evidence = item.get("evidence", {}) if isinstance(item.get("evidence"), dict) else {}
    dialogue_id = str(evidence.get("dialogue_id", "")).strip()
    episode_id = str(evidence.get("episode_id", "")).strip()
    atomic_fact = str(item.get("Atomic fact", "")).strip()
    if not dialogue_id or not episode_id:
        return ""
    return f"{dialogue_id}:{episode_id}|{atomic_fact}"


def _resolve_route_config(route_config: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = dict(route_config or {})
    route_types = cfg.get("route_types")
    if not isinstance(route_types, list):
        route_types = list(_DEFAULT_ROUTE_TYPES)

    route_weights = cfg.get("route_weights")
    if not isinstance(route_weights, dict):
        route_weights = {"default": 1.0}

    query_generator = str(cfg.get("query_generator", _DEFAULT_QUERY_GENERATOR)).strip().lower()
    if query_generator not in {"template", "llm"}:
        query_generator = _DEFAULT_QUERY_GENERATOR

    return {
        "route_count": _safe_int(cfg.get("route_count"), default=4, minimum=1),
        "route_types": [str(item).strip().lower() for item in route_types if str(item).strip()],
        "per_route_topk": _safe_int(cfg.get("per_route_topk"), default=10, minimum=1),
        "fusion": str(cfg.get("fusion", "rrf")).strip().lower() or "rrf",
        "max_workers": _safe_int(cfg.get("max_workers"), default=4, minimum=1),
        "route_weights": {str(k): float(v) for k, v in route_weights.items()},
        "rrf_k": _safe_int(cfg.get("rrf_k"), default=60, minimum=1),
        "query_generator": query_generator,
    }


def _empty_result(topk: int) -> Dict[str, Any]:
    return {
        "hit": False,
        "topk": max(1, int(topk)),
        "total_scene_count": 0,
        "total_fact_count": 0,
        "matched_count": 0,
        "results": [],
    }


def _safe_int(value: Any, default: int, minimum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(int(minimum), parsed)
