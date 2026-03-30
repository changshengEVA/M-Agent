#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entity resolution strategies."""

from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from m_agent.config_paths import MEMORY_CORE_RUNTIME_PROMPT_CONFIG_PATH
from m_agent.prompt_utils import (
    load_resolved_prompt_config,
    normalize_prompt_language,
    render_prompt_template,
)

try:
    from .decision import (
        ResolutionDecision,
        ResolutionType,
        create_new_entity_decision,
        create_same_as_existing_decision,
    )
    from .library import EntityLibrary
except ImportError:
    from decision import (  # type: ignore
        ResolutionDecision,
        ResolutionType,
        create_new_entity_decision,
        create_same_as_existing_decision,
    )
    from library import EntityLibrary  # type: ignore


logger = logging.getLogger(__name__)


class ResolutionStrategy(ABC):
    """Strategy contract for entity resolution."""

    def __init__(self, name: str):
        self.name = name
        self.description = ""

    @abstractmethod
    def resolve(
        self,
        entity_id: str,
        entity_library: EntityLibrary,
        context: Optional[Dict[str, Any]] = None,
    ) -> ResolutionDecision:
        """Resolve one entity id."""

    def __str__(self) -> str:
        return f"{self.name}: {self.description}"


class AliasThenEmbeddingLLMStrategy(ResolutionStrategy):
    """Alias match -> embedding candidate recall -> LLM disambiguation."""

    def __init__(
        self,
        llm_func: Callable[[str], str],
        embed_func: Callable[[str], List[float]],
        similarity_threshold: float = 0.7,
        top_k: int = 3,
        use_threshold: bool = True,
        prompt_language: str = "zh",
        runtime_prompt_config_path: str | Path | None = None,
    ):
        super().__init__("AliasThenEmbeddingLLMStrategy")
        self.description = "Alias match -> embedding recall -> LLM judgment"
        self.llm_func = llm_func
        self.embed_func = embed_func
        self.similarity_threshold = float(similarity_threshold)
        self.top_k = int(top_k)
        self.use_threshold = bool(use_threshold)
        self.prompt_language = normalize_prompt_language(prompt_language)
        self.runtime_prompt_config_path = Path(
            runtime_prompt_config_path or MEMORY_CORE_RUNTIME_PROMPT_CONFIG_PATH
        ).resolve()
        self.runtime_prompts = self._load_runtime_prompts(self.runtime_prompt_config_path)

        logger.info(
            "Initialized %s: threshold=%s top_k=%s use_threshold=%s prompt_language=%s",
            self.name,
            self.similarity_threshold,
            self.top_k,
            self.use_threshold,
            self.prompt_language,
        )

    def _load_runtime_prompts(self, path: Path) -> Dict[str, str]:
        config = load_resolved_prompt_config(path, language=self.prompt_language)
        prompts = config.get("entity_resolution")
        if not isinstance(prompts, dict):
            raise ValueError(f"`entity_resolution` prompt namespace is required in runtime prompt config: {path}")
        template = prompts.get("alias_then_embedding_llm_judge_prompt")
        if not isinstance(template, str) or not template.strip():
            raise ValueError(
                f"`entity_resolution.alias_then_embedding_llm_judge_prompt` is required in runtime prompt config: {path}"
            )
        return {"alias_then_embedding_llm_judge_prompt": template.strip()}

    def _alias_match(
        self,
        entity_id: str,
        entity_library: EntityLibrary,
        exclude_unresolved: bool = False,
    ) -> Optional[str]:
        if not entity_library.name_exists(entity_id):
            return None
        record = entity_library.get_entity_by_name(entity_id)
        if record is None:
            return None
        if exclude_unresolved and not getattr(record, "resolved", False):
            return None
        return str(record.entity_id)

    def _embedding_similarity_match(
        self,
        entity_id: str,
        entity_library: EntityLibrary,
    ) -> List[Tuple[str, float]]:
        query_embedding = None
        if entity_id in getattr(entity_library, "embeddings", {}):
            query_embedding = entity_library.embeddings[entity_id]
        else:
            logger.info("Entity %s has no embedding; trying to initialize it", entity_id)
            if entity_library.init_entity_embedding(entity_id):
                query_embedding = entity_library.embeddings.get(entity_id)

        if query_embedding is None:
            logger.warning("Entity embedding is unavailable: %s", entity_id)
            return []

        return entity_library.search_by_embedding(
            embedding=query_embedding,
            threshold=self.similarity_threshold if self.use_threshold else 0.0,
            top_k=self.top_k,
            exclude_unresolved=True,
        )

    def _llm_judgment(
        self,
        source_entity_id: str,
        candidate_entities: List[Tuple[str, float]],
        entity_library: EntityLibrary,
    ) -> Optional[str]:
        if not candidate_entities:
            return None

        candidate_info: List[Dict[str, Any]] = []
        for candidate_id, similarity in candidate_entities:
            record = entity_library.get_entity(candidate_id)
            if record is None:
                continue
            candidate_info.append(
                {
                    "id": candidate_id,
                    "name": getattr(record, "canonical_name", candidate_id),
                    "similarity": similarity,
                }
            )

        if not candidate_info:
            return None

        prompt = self._build_llm_prompt(source_entity_id, candidate_info)
        try:
            llm_response = self.llm_func(prompt)
            target_entity_id = self._parse_llm_response(str(llm_response or ""), candidate_info)
            if target_entity_id:
                logger.info("LLM judgment: %s -> %s", source_entity_id, target_entity_id)
            else:
                logger.info("LLM judgment: %s -> NEW_ENTITY", source_entity_id)
            return target_entity_id
        except Exception as exc:
            logger.error("LLM judgment failed for %s: %s", source_entity_id, exc)
            return None

    def _build_llm_prompt(self, source_entity_id: str, candidate_info: List[Dict[str, Any]]) -> str:
        candidate_lines = [
            f"{index}. entity_id={item['id']}, name={item['name']}, similarity={float(item['similarity']):.3f}"
            for index, item in enumerate(candidate_info, start=1)
        ]
        return render_prompt_template(
            self.runtime_prompts["alias_then_embedding_llm_judge_prompt"],
            {
                "<source_entity_id>": source_entity_id,
                "<candidate_text>": "\n".join(candidate_lines),
            },
        ).strip()

    def _parse_llm_response(self, llm_response: str, candidate_info: List[Dict[str, Any]]) -> Optional[str]:
        response = str(llm_response or "").strip()
        if not response:
            return None

        if response.upper() == "NEW_ENTITY":
            return None

        valid_ids = {str(item["id"]) for item in candidate_info}
        if response in valid_ids:
            return response

        patterns = [
            r'entity_id[:=\s"]+([A-Za-z0-9_\-:.]+)',
            r'ID[:=\s"]+([A-Za-z0-9_\-:.]+)',
            r'\b([A-Za-z0-9_\-:.]+)\b',
        ]
        for pattern in patterns:
            matched = re.search(pattern, response, flags=re.IGNORECASE)
            if not matched:
                continue
            candidate_id = matched.group(1).strip()
            if candidate_id in valid_ids:
                return candidate_id

        logger.warning("Unable to parse LLM response for entity resolution: %s", response)
        return None

    def resolve(
        self,
        entity_id: str,
        entity_library: EntityLibrary,
        context: Optional[Dict[str, Any]] = None,
    ) -> ResolutionDecision:
        logger.info("Resolving entity: %s", entity_id)

        alias_match_target = self._alias_match(entity_id, entity_library, exclude_unresolved=True)
        if alias_match_target:
            logger.info("Alias match succeeded: %s -> %s", entity_id, alias_match_target)
            return create_same_as_existing_decision(
                source_entity_id=entity_id,
                target_entity_id=alias_match_target,
                strategy_name=self.name,
                confidence=0.95,
                evidence={
                    "match_type": "alias_match",
                    "matched_name": entity_id,
                    "target_entity": alias_match_target,
                },
            )

        similar_entities = self._embedding_similarity_match(entity_id, entity_library)
        if not similar_entities:
            logger.info("No embedding candidates found for %s", entity_id)
            return create_new_entity_decision(
                entity_id=entity_id,
                strategy_name=self.name,
                confidence=0.8,
                evidence={
                    "match_type": "no_candidate_found",
                    "alias_match": False,
                    "embedding_match": False,
                    "similar_entities_found": 0,
                },
            )

        logger.info("Found %d embedding candidates for %s", len(similar_entities), entity_id)
        llm_target = self._llm_judgment(entity_id, similar_entities, entity_library)
        if llm_target:
            best_similarity = float(similar_entities[0][1]) if similar_entities else 0.0
            confidence = min(0.9, 0.7 + best_similarity * 0.2)
            return create_same_as_existing_decision(
                source_entity_id=entity_id,
                target_entity_id=llm_target,
                strategy_name=self.name,
                confidence=confidence,
                evidence={
                    "match_type": "llm_judgment",
                    "alias_match": False,
                    "embedding_match": True,
                    "similar_entities": similar_entities,
                    "llm_target": llm_target,
                    "best_similarity": best_similarity,
                },
            )

        return create_new_entity_decision(
            entity_id=entity_id,
            strategy_name=self.name,
            confidence=0.85,
            evidence={
                "match_type": "llm_judgment_new",
                "alias_match": False,
                "embedding_match": True,
                "similar_entities": similar_entities,
                "llm_judgment": "NEW_ENTITY",
            },
        )
