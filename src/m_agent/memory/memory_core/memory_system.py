#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Memory Core 系统

核心入口类，封装 KGBase 和 EntityResolutionService，提供对话数据加载接口。
"""

import logging
import json
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List, Set, Tuple

from m_agent.config_paths import MEMORY_CORE_RUNTIME_PROMPT_CONFIG_PATH
from m_agent.memory.memory_core.core.kg_base import KGBase
from m_agent.memory.memory_core.services_bank.entity_resolution.service import (
    EntityResolutionService,
    create_default_resolution_service
)
from m_agent.memory.memory_core.services_bank.entity_profile_sys.service import (
    EntityProfileService,
    create_default_entity_profile_service,
)
from m_agent.memory.memory_core.system.event_bus import EventBus
from m_agent.memory.memory_core.system.event_types import EventType
from m_agent.paths import memory_workflow_dir
from m_agent.prompt_utils import normalize_prompt_language

logger = logging.getLogger(__name__)


class MemoryCore:
    """
    Memory Core 主类
    
    初始化步骤：
    1. 根据 workflow_id 构建本地目录（scene/dialogues/episodes/local_store）。
    2. 初始化 KGBase（Neo4j 后端，按 workflow 分库）。
    3. 初始化 EntityResolutionService（保留服务与事件机制）。
    4. 提供 episodes 导入与检索接口。
    """
    
    def __init__(
        self,
        workflow_id: str,
        llm_func: Optional[Callable[[str], str]] = None,
        embed_func: Optional[Callable[[str], List[float]]] = None,
        llm_temperature: float = 0.0,
        similarity_threshold: float = 0.7,
        top_k: int = 3,
        use_threshold: bool = True,
        scene_prompt_version: str = "v2",
        fact_prompt_version: str = "v2",
        memory_owner_name: str = "changshengEVA",
        prompt_language: str = "zh",
        runtime_prompt_config_path: Optional[str | Path] = None,
        detail_search_hybrid_config: Optional[Dict[str, Any]] = None,
        detail_search_multi_route_config: Optional[Dict[str, Any]] = None,
        facts_only_mode: bool = False,
    ):
        """
        初始化 MemoryCore
        
        Args:
            workflow_id: 工作流ID，用于确定数据存储路径
            llm_func: 可选的LLM函数，如果为None则使用默认OpenAI LLM
            embed_func: 可选的嵌入函数，如果为None则使用默认本地Embedding
            llm_temperature: LLM温度参数（仅当使用默认LLM时有效）
            similarity_threshold: 实体解析相似度阈值
            top_k: 实体解析返回前K个候选
            use_threshold: 是否使用阈值模式
            scene_prompt_version: Scene 构建 prompt 版本
            fact_prompt_version: Atomic facts 构建 prompt 版本
            memory_owner_name: 记忆主体名称
        """
        self.workflow_id = workflow_id
        self.llm_temperature = llm_temperature
        self.similarity_threshold = similarity_threshold
        self.top_k = top_k
        self.use_threshold = use_threshold
        self.scene_prompt_version = str(scene_prompt_version or "v2")
        self.fact_prompt_version = str(fact_prompt_version or "v2")
        self.memory_owner_name = str(memory_owner_name or "changshengEVA")
        self.prompt_language = normalize_prompt_language(prompt_language)
        self.runtime_prompt_config_path = Path(
            runtime_prompt_config_path or MEMORY_CORE_RUNTIME_PROMPT_CONFIG_PATH
        ).resolve()
        self.detail_search_hybrid_config = (
            dict(detail_search_hybrid_config)
            if isinstance(detail_search_hybrid_config, dict)
            else {}
        )
        self.detail_search_multi_route_config = (
            dict(detail_search_multi_route_config)
            if isinstance(detail_search_multi_route_config, dict)
            else {}
        )
        self.facts_only_mode = bool(facts_only_mode)
        
        # 1. 构建数据路径（极简化：不再使用 kg_data/entity/relation 文件结构）
        self.memory_root = memory_workflow_dir(workflow_id)
        self.local_store_dir = self.memory_root / "local_store"
        self.entity_library_path = self.local_store_dir / "entity_library"
        self.entity_profile_data_path = self.local_store_dir / "entity_profile"
        self.entity_profile_facts_situation_file = self.local_store_dir / "facts_situation.json"
        self.scene_dir = self.memory_root / "scene"
        self.facts_dir = self.memory_root / "facts"
        self.facts_situation_file = self.memory_root / "facts_situation.json"
        self.entity_statement_dir = self.memory_root / "entity_statement"
        self.dialogues_dir = self.memory_root / "dialogues"
        self.episodes_dir = self.memory_root / "episodes"

        # 确保目录存在
        self.local_store_dir.mkdir(parents=True, exist_ok=True)
        self.entity_library_path.mkdir(parents=True, exist_ok=True)
        self.entity_profile_data_path.mkdir(parents=True, exist_ok=True)
        self.scene_dir.mkdir(parents=True, exist_ok=True)
        self.facts_dir.mkdir(parents=True, exist_ok=True)
        self.entity_statement_dir.mkdir(parents=True, exist_ok=True)
        self.dialogues_dir.mkdir(parents=True, exist_ok=True)
        self.episodes_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"初始化 MemoryCore，工作流ID: {workflow_id}")
        logger.info(f"Memory根目录: {self.memory_root}")
        logger.info(f"LocalStore目录: {self.local_store_dir}")
        logger.info(f"实体库路径: {self.entity_library_path}")
        logger.info(f"实体档案库路径: {self.entity_profile_data_path}")
        logger.info(f"实体档案facts状态文件: {self.entity_profile_facts_situation_file}")
        logger.info(f"Scene目录: {self.scene_dir}")
        logger.info(f"Facts目录: {self.facts_dir}")
        logger.info(f"Facts状态文件: {self.facts_situation_file}")
        logger.info(f"Entity statement目录: {self.entity_statement_dir}")
        logger.info(f"Dialogues目录: {self.dialogues_dir}")
        logger.info(f"Episodes目录: {self.episodes_dir}")
        logger.info(f"Scene prompt版本: {self.scene_prompt_version}")
        logger.info(f"Fact prompt版本: {self.fact_prompt_version}")
        logger.info(f"Memory owner: {self.memory_owner_name}")
        logger.info(f"Prompt language: {self.prompt_language}")
        logger.info(f"Runtime prompt config: {self.runtime_prompt_config_path}")
        logger.info("Detail search hybrid config: %s", self.detail_search_hybrid_config)
        logger.info("Detail search multi-route config: %s", self.detail_search_multi_route_config)
        logger.info("Facts-only mode: %s", self.facts_only_mode)
        
        # 2. 初始化 EventBus
        self.event_bus = EventBus()
        logger.info("EventBus 初始化完成")
        
        # 3. 初始化 KGBase（传入 event_bus）
        self.kg_base = self._init_kg_base()
        
        # 4. 初始化 LLM 和 Embed 函数
        self.llm_func, self.embed_func = self._init_llm_embed(llm_func, embed_func)
        
        self.feature_search = None
        self.services: List[Any] = []

        # 5. 初始化 EntityResolutionService并进行注册
        self.entity_resolution_service = self._init_entity_resolution_service()
        self.register_service(self.entity_resolution_service)
        
        # 6. 初始化 EntityProfileService 并进行注册
        self.entity_profile_service = self._init_entity_profile_service()
        self.register_service(self.entity_profile_service)
        
        # 7. 发布初始化事件
        self.event_bus.publish(EventType.SYSTEM_INITIALIZED, {})
        
        logger.info("MemoryCore 初始化完成")
    
    def _init_kg_base(self) -> KGBase:
        """初始化 KGBase 实例。"""
        logger.info("初始化 KGBase: workflow_id=%s", self.workflow_id)
        return KGBase(
            workflow_id=self.workflow_id,
            event_bus=self.event_bus,
        )
    
    def _init_llm_embed(
        self,
        llm_func: Optional[Callable[[str], str]],
        embed_func: Optional[Callable[[str], List[float]]]
    ) -> tuple[Callable[[str], str], Callable[[str], List[float]]]:
        """
        初始化 LLM 和 Embed 函数
        
        Returns:
            (llm_func, embed_func) 元组
        """
        if llm_func is None:
            logger.info("使用默认 OpenAI LLM")
            from m_agent.load_model.OpenAIcall import get_llm
            llm_func = get_llm(self.llm_temperature)

        if embed_func is None:
            logger.info("使用默认本地 Embedding")
            from m_agent.load_model.BGEcall import get_embed_model
            embed_func = get_embed_model()
        
        return llm_func, embed_func
    
    def _init_entity_resolution_service(self) -> EntityResolutionService:
        """初始化 EntityResolutionService 实例"""
        logger.info("初始化 EntityResolutionService")
        
        # 使用便捷函数创建默认服务（不再传入kg_base）
        service = create_default_resolution_service(
            llm_func=self.llm_func,
            embed_func=self.embed_func,
            similarity_threshold=self.similarity_threshold,
            top_k=self.top_k,
            use_threshold=self.use_threshold,
            data_path=str(self.entity_library_path),
            prompt_language=self.prompt_language,
            runtime_prompt_config_path=str(self.runtime_prompt_config_path),
        )
        
        # 对齐实体库与 KG 中的实体
        self._align_entity_library_with_kg(service)
        
        return service
    
    def _init_entity_profile_service(self) -> EntityProfileService:
        """初始化 EntityProfileService 实例"""
        logger.info("初始化 EntityProfileService")
        service = create_default_entity_profile_service(
            llm_func=self.llm_func,
            embed_func=self.embed_func,
            memory_root=str(self.memory_root),
            profile_data_path=str(self.entity_profile_data_path),
            facts_situation_path=str(self.entity_profile_facts_situation_file),
            similarity_threshold=self.similarity_threshold,
            top_k=self.top_k,
            prompt_language=self.prompt_language,
            runtime_prompt_config_path=str(self.runtime_prompt_config_path),
            auto_align_on_init=False,
            align_on_system_initialized=False,
        )
        return service

    # ============================================================================
    # 与附属数据库的对齐操作
    # ============================================================================
    def _align_entity_library_with_kg(self, service: EntityResolutionService) -> None:
        """
        初始化 EntityLibrary 与 KG 的同步（极简版）。

        当前模式下 KG 数据以 Neo4j 为主存储，本地 EntityLibrary 仅为解析辅助索引。
        同步策略：
        - 若 KG 实体信息（仅比较 id/name）与当前 EntityLibrary 一致：跳过重建（避免重复初始化 embedding）。
        - 若不一致：清空并按 KG 重建（rebuild_from_kg 内部会重算 embedding）。
        """
        try:
            kg_entity_ids = self.kg_base.list_entity_ids()
            logger.info("KG 实体数: %s", len(kg_entity_ids))

            if not kg_entity_ids:
                service.entity_library.clear()
                if service.data_path:
                    library_dir = Path(service.data_path)
                    if library_dir.exists() and library_dir.is_dir():
                        for json_file in library_dir.glob("*.json"):
                            try:
                                json_file.unlink()
                            except Exception:
                                pass
                logger.info("KG 为空，EntityLibrary 已清空")
                return

            kg_data = {"entities": []}
            for entity_id in kg_entity_ids:
                ok, entity_data = self.kg_base.get_entity(entity_id)
                if not ok or not entity_data:
                    continue
                kg_data["entities"].append(
                    {
                        "id": entity_id,
                        "name": entity_data.get("name", entity_id),
                        "type": entity_data.get("type"),
                        "metadata": entity_data.get("metadata", {}),
                    }
                )

            kg_signature = self._build_kg_entity_signature(kg_data["entities"])
            library_signature = self._build_library_entity_signature(service)
            if kg_signature == library_signature:
                logger.info("KG 实体信息未变化，跳过 EntityLibrary 重建与 embedding 初始化")
                return

            rebuilt = service.entity_library.rebuild_from_kg(kg_data)
            if not rebuilt:
                logger.warning("EntityLibrary rebuild_from_kg failed")
                return

            if service.data_path:
                service.entity_library.save_to_path(service.data_path)
            logger.info("EntityLibrary 已按 KG 重建完成: %s 个实体", len(kg_data["entities"]))
        except Exception as e:
            logger.warning(f"初始化 EntityLibrary 同步时出错: {e}")

    def _build_kg_entity_signature(self, kg_entities: List[Dict[str, Any]]) -> tuple:
        signature_items = []
        for item in kg_entities:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("id") or "").strip()
            if not entity_id:
                continue
            name = str(item.get("name") or entity_id).strip()
            signature_items.append((entity_id, name))
        return tuple(sorted(signature_items))

    def _build_library_entity_signature(self, service: EntityResolutionService) -> tuple:
        signature_items = []
        entities = getattr(service.entity_library, "entities", {})
        if not isinstance(entities, dict):
            return tuple()

        for entity_id, record in entities.items():
            record_id = str(getattr(record, "entity_id", "") or entity_id).strip()
            if not record_id:
                continue
            canonical_name = str(getattr(record, "canonical_name", "") or record_id).strip()
            signature_items.append((record_id, canonical_name))
        return tuple(sorted(signature_items))
    
    # ============================================================================
    # 数据导入
    # ============================================================================
    def load_from_episode_path(
        self,
        path: Path,
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        新的数据导入接口：从 episodes 路径导入并在系统内部构建 scene + atomic facts。
        """
        from .workflow.build.load_from_episode import (
            load_from_episode_path as workflow_load_from_episode_path,
        )

        logger.info(f"调用 load_from_episode_path 接口，路径: {path}")
        return workflow_load_from_episode_path(
            path=path,
            memory_core=self,
            progress_callback=progress_callback,
        )

    def make_entity_statement(self, path: Path, force_update: bool = False) -> Dict[str, Any]:
        """
        从 episodes 路径导入并生成 entity statements。
        """
        from .workflow.build.make_entity_statement import (
            make_entity_statement as workflow_make_entity_statement,
        )

        logger.info(f"调用 make_entity_statement 接口，路径: {path}, force_update: {force_update}")
        return workflow_make_entity_statement(path=path, memory_core=self, force_update=force_update)

    def extract_fact_entities(self, force_update: bool = False, use_tqdm: bool = True) -> Dict[str, Any]:
        """
        扫描 scene facts 并提取实体（main_entity / other_entities），
        同步写入 data/memory/{workflow_id}/facts 与 episode_situation 状态。
        """
        from .workflow.build.extract_fact_entities import (
            extract_fact_entities as workflow_extract_fact_entities,
        )

        logger.info(
            "调用 extract_fact_entities 接口: workflow_id=%s, force_update=%s",
            self.workflow_id,
            force_update,
        )
        return workflow_extract_fact_entities(
            memory_core=self,
            force_update=force_update,
            use_tqdm=use_tqdm,
        )

    def import_fact_entities(
        self,
        force_update: bool = False,
        use_tqdm: bool = True,
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        导入 facts 的实体信息（UID + name）到 KG，并更新 facts_situation.json。
        """
        from .workflow.build.import_fact_entities import (
            import_fact_entities as workflow_import_fact_entities,
        )

        logger.info(
            "调用 import_fact_entities 接口: workflow_id=%s, force_update=%s",
            self.workflow_id,
            force_update,
        )
        result = workflow_import_fact_entities(
            memory_core=self,
            force_update=force_update,
            use_tqdm=use_tqdm,
        )

        # 对齐 EntityLibrary 与最新 KG，避免解析库滞后
        try:
            self._align_entity_library_with_kg(self.entity_resolution_service)
        except Exception as exc:
            logger.warning("import_fact_entities 后同步 EntityLibrary 失败: %s", exc)

        # 对齐实体档案系统与最新 facts 状态
        align_result = None
        try:
            if progress_callback is not None:
                try:
                    progress_callback(
                        "flush_stage",
                        {
                            "stage": "entity_profile_align",
                            "stage_label": "EntityProfile align",
                            "status": "started",
                        },
                    )
                except Exception:
                    logger.exception("EntityProfile align start callback failed")
            align_result = self.entity_profile_service.align_with_master_facts(force_rebuild=False)
            if progress_callback is not None:
                try:
                    progress_callback(
                        "flush_stage",
                        {
                            "stage": "entity_profile_align",
                            "stage_label": "EntityProfile align",
                            "status": "completed",
                            "result": align_result,
                        },
                    )
                except Exception:
                    logger.exception("EntityProfile align complete callback failed")
        except Exception as exc:
            if progress_callback is not None:
                try:
                    progress_callback(
                        "flush_stage",
                        {
                            "stage": "entity_profile_align",
                            "stage_label": "EntityProfile align",
                            "status": "failed",
                            "error": str(exc),
                        },
                    )
                except Exception:
                    logger.exception("EntityProfile align failure callback failed")
            logger.warning("import_fact_entities 后同步 EntityProfileService 失败: %s", exc)
            align_result = {
                "success": False,
                "error": str(exc),
            }
        if isinstance(result, dict):
            result["entity_profile_align_result"] = align_result
        return result

    def sync_entity_profile(
        self,
        force_rebuild: bool = False,
        sample_ratio: Optional[float] = None,
        sample_seed: int = 42,
        sample_output_tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        手动触发实体档案与 facts 对齐。
        """
        if sample_ratio is not None:
            return self.entity_profile_service.rebuild_from_sampled_facts(
                sample_ratio=sample_ratio,
                sample_seed=sample_seed,
                output_tag=sample_output_tag,
            )
        return self.entity_profile_service.align_with_master_facts(force_rebuild=force_rebuild)

    def sample_entity_profile_rebuild(
        self,
        sample_ratio: float = 0.01,
        sample_seed: int = 42,
        sample_output_tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        使用采样 facts 进行低成本 EntityProfile 重建演练，输出写入独立 sample 目录。
        """
        return self.entity_profile_service.rebuild_from_sampled_facts(
            sample_ratio=sample_ratio,
            sample_seed=sample_seed,
            output_tag=sample_output_tag,
        )

    def reset_entity_profile_alignment_state(
        self,
        confirm_token: str,
        clear_checkpoint: bool = True,
        clear_sample_outputs: bool = False,
    ) -> Dict[str, Any]:
        """
        手动清空 EntityProfile 的本地 facts 对齐状态与产物。
        该操作危险，必须显式提供确认 token。
        """
        return self.entity_profile_service.reset_alignment_state(
            confirm_token=confirm_token,
            clear_checkpoint=clear_checkpoint,
            clear_sample_outputs=clear_sample_outputs,
        )

    
    # ============================================================================
    # 公开接口
    # ============================================================================
    def search_content(
        self,
        dialogue_id: str,
        episode_id: str,
        segment_id: str | None = None,
    ) -> Dict[str, Any]:
        """
        内容检索公开接口。
        输入 dialogue_id 和 episode_id（可选 segment_id），返回对应对话片段的具体内容。
        当提供 segment_id 时，仅返回该 segment 对应的 turn 范围。
        """
        from .workflow.search.content_search import search_content as workflow_search_content

        logger.info(
            "调用 search_content 接口: dialogue_id=%s, episode_id=%s, segment_id=%s",
            dialogue_id,
            episode_id,
            segment_id,
        )
        return workflow_search_content(
            dialogue_id=dialogue_id,
            episode_id=episode_id,
            scene_dir=self.scene_dir,
            dialogues_dir=self.dialogues_dir,
            episodes_dir=self.episodes_dir,
            segment_id=segment_id,
        )

    def search_contents_by_episode_refs(self, episode_refs: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """
        批量内容检索接口。
        输入 [{"dialogue_id":"...", "episode_id":"...", "segment_id":"..."(可选)}, ...]，
        返回对应内容结果列表。当提供 segment_id 时按 segment 粒度切片。
        """
        if not isinstance(episode_refs, list):
            return []

        results: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in episode_refs:
            if not isinstance(item, dict):
                continue
            dialogue_id = str(item.get("dialogue_id", "")).strip()
            episode_id = str(item.get("episode_id", "")).strip()
            segment_id = str(item.get("segment_id", "")).strip() or None
            if not dialogue_id or not episode_id:
                continue
            ref = f"{dialogue_id}:{episode_id}"
            if segment_id:
                ref = f"{ref}:{segment_id}"
            if ref in seen:
                continue
            seen.add(ref)
            results.append(self.search_content(
                dialogue_id=dialogue_id,
                episode_id=episode_id,
                segment_id=segment_id,
            ))
        return results

    def search_events_by_time_range(self, start_time: str, end_time: str) -> List[Dict[str, Any]]:
        """
        按时间范围检索 scene 公开接口。

        Args:
            start_time: 查询起始时间（ISO 格式字符串）
            end_time: 查询结束时间（ISO 格式字符串）

        Returns:
            List[Dict[str, Any]]，每项包含:
            - scene_id
            - theme
            - starttime
            - endtime
        """
        from .workflow.search.time_event_search import (
            search_events_by_time_range as workflow_search_events_by_time_range
        )

        logger.info(
            "调用 search_events_by_time_range 接口: start_time=%s, end_time=%s",
            start_time,
            end_time,
        )
        return workflow_search_events_by_time_range(
            start_time=start_time,
            end_time=end_time,
            scene_dir=self.scene_dir,
        )

    def search_details(self, detail_query: str, topk: int = 5) -> Dict[str, Any]:
        """
        细节行为检索公开接口。
        基于 scene.facts 做混合检索（向量 + 关键词），并返回统一结果格式。
        """
        from .workflow.search.details_search import search_details as workflow_search_details

        logger.info(
            "调用 search_details 接口: detail_query=%s, topk=%s, scene_dir=%s",
            detail_query,
            topk,
            self.scene_dir,
        )
        return workflow_search_details(
            detail_query=detail_query,
            scene_dir=self.scene_dir,
            embed_func=self.embed_func,
            topk=topk,
            hybrid_config=self.detail_search_hybrid_config,
        )

    def search_details_multi_route(
        self,
        detail_query: str,
        topk: int = 5,
        route_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        多路并行细节检索公开接口。
        """
        from .workflow.search.multi_route_details_search import (
            search_details_multi_route as workflow_search_details_multi_route,
        )

        resolved_route_config = (
            dict(route_config)
            if isinstance(route_config, dict)
            else dict(self.detail_search_multi_route_config)
        )
        logger.info(
            "调用 search_details_multi_route 接口: detail_query=%s, topk=%s, route_config=%s",
            detail_query,
            topk,
            resolved_route_config,
        )
        return workflow_search_details_multi_route(
            detail_query=detail_query,
            scene_dir=self.scene_dir,
            embed_func=self.embed_func,
            topk=topk,
            hybrid_config=self.detail_search_hybrid_config,
            route_config=resolved_route_config,
            llm_func=self.llm_func,
        )

    def resolve_entity_id(self, entity_name_or_id: str) -> Dict[str, Any]:
        """
        解析实体名称到规范实体ID（暴露 entity_resolution 的外部接口）。
        """
        from .workflow.search.entity_search import resolve_entity_id as workflow_resolve_entity_id

        logger.info("调用 resolve_entity_id 接口: query=%s", entity_name_or_id)
        return workflow_resolve_entity_id(
            entity_name_or_id=entity_name_or_id,
            entity_library=self.entity_resolution_service.entity_library,
            llm_func=self.llm_func,
            embed_func=self.embed_func,
            max_candidates=max(3, int(self.top_k)),
            string_similarity_threshold=0.72,
            embedding_similarity_threshold=max(0.45, float(self.similarity_threshold) - 0.25),
            prompt_language=self.prompt_language,
            runtime_prompt_config_path=self.runtime_prompt_config_path,
        )

    def search_entity_feature(self, entity_id: str, feature_query: str, topk: int = 5) -> Dict[str, Any]:
        """
        实体ID + 特征 检索接口（含证据 dialogue_id / episode_id）。
        """
        return self.entity_profile_service.query_entity_feature(
            entity_id=entity_id,
            feature_query=feature_query,
            topk=topk,
        )

    def search_entity_event(self, entity_id: str, event_query: str, topk: int = 5) -> Dict[str, Any]:
        """
        实体ID + 事件 检索接口（含证据 dialogue_id / episode_id）。
        """
        return self.entity_profile_service.query_entity_event(
            entity_id=entity_id,
            event_query=event_query,
            topk=topk,
        )

    def search_entity_events_by_time(
        self,
        entity_id: str,
        start_time: str,
        end_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        实体ID + 时间窗口 检索接口（返回命中事件及证据 dialogue_id / episode_id）。
        当 end_time 为空时，按时间点查询处理（等价于 start_time=end_time）。
        """
        return self.entity_profile_service.query_entity_time(
            entity_id=entity_id,
            start_time=start_time,
            end_time=end_time,
        )

    def get_entity_profile(self, entity_id: str) -> Dict[str, Any]:
        """
        获取实体档案摘要（summary）。
        """
        return self.entity_profile_service.get_entity_profile(entity_id)

    def search_entity_profile(self, entity_uid: str, optional_query: Optional[str] = None) -> Dict[str, Any]:
        """三段式档案摘要检索（硬匹配 / 阈值 / LLM 确认）。"""
        return self.entity_profile_service.search_entity_profile(
            entity_uid=entity_uid,
            optional_query=optional_query,
        )

    def search_entity_status_answer(
        self,
        entity_uid: str,
        field_yield: str,
        user_question: str,
        topk: int = 3,
    ) -> Dict[str, Any]:
        """状态向量召回 top-k + 单次 LLM 作答。"""
        return self.entity_profile_service.search_entity_status_answer(
            entity_uid=entity_uid,
            field_yield=field_yield,
            user_question=user_question,
            topk=topk,
        )

    def augment_question_with_resolved_entities(self, question: str) -> str:
        """
        facts_only_mode=false：在问题末尾附加 [ENTITY_RESOLUTION] 名称 -> entity id 映射。
        facts_only_mode=true：原样返回（不在问题中注入）。
        """
        if bool(self.facts_only_mode):
            return str(question or "")
        q = str(question or "").strip()
        if not q:
            return q
        lib = self.entity_resolution_service.entity_library
        name_to_entity = getattr(lib, "name_to_entity", {}) or {}
        resolutions: List[Tuple[str, str]] = []
        seen_ids: Set[str] = set()
        for name_key, eid in name_to_entity.items():
            nk = str(name_key or "").strip()
            eid_s = str(eid or "").strip()
            if len(nk) < 2 or not eid_s:
                continue
            if nk.lower() not in q.lower():
                continue
            res = self.resolve_entity_id(nk)
            if not res.get("hit") or not str(res.get("entity_id") or "").strip():
                continue
            rid = str(res["entity_id"]).strip()
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            resolutions.append((nk, rid))
        if not resolutions:
            return q
        lines = [f"- {n} -> entity_id:{uid}" for n, uid in resolutions[:24]]
        return q + "\n\n[ENTITY_RESOLUTION]\n" + "\n".join(lines)

    # ============================================================================
    # 服务注册
    # ============================================================================
    def register_service(self, service: Any) -> None:
        """
        注册服务到 EventBus
        
        Args:
            service: 服务实例，必须是 BaseService 的子类
        """
        # 验证服务是 BaseService 的子类
        if hasattr(service, 'get_subscribed_events') and hasattr(service, 'handle_event'):
            self.event_bus.register(service)
            if service not in self.services:
                self.services.append(service)
            logger.info(f"注册服务到 EventBus: {service.__class__.__name__}")
        else:
            logger.warning(f"服务 {service.__class__.__name__} 不是 BaseService 子类，无法注册到 EventBus")
    
    # ============================================================================
    # Service 功能执行部件
    # ============================================================================
    def run_entity_resolution_pass(self) -> Dict[str, Any]:
        """
        执行实体解析阶段（Resolution Pass）。
        具体实现下沉到 workflow/service。
        """
        from .workflow.service.entity_resolution_pass import (
            run_entity_resolution_pass as workflow_run_entity_resolution_pass,
        )
        return workflow_run_entity_resolution_pass(self)
    
    # ============================================================================
    # 辅助方法
    # ============================================================================
    
    def get_kg_stats(self) -> Dict[str, Any]:
        """获取知识图谱统计信息"""
        return self.kg_base.get_kg_stats()
    
    def get_entity_resolution_stats(self) -> Dict[str, Any]:
        """获取实体解析服务统计信息"""
        return self.entity_resolution_service.get_library_stats()
    
    def get_entity_profile_stats(self) -> Dict[str, Any]:
        """获取实体档案服务统计信息"""
        return self.entity_profile_service.get_stats()
    
    def __str__(self) -> str:
        """字符串表示"""
        kg_stats = self.get_kg_stats()
        return (f"MemoryCore(workflow_id={self.workflow_id}, "
                f"entities={kg_stats['entity_count']}, "
                f"relations={kg_stats['relation_count']}, "
                f"services={len(self.services)})")


# 便捷函数
def create_memory_core(
    workflow_id: str,
    llm_func: Optional[Callable[[str], str]] = None,
    embed_func: Optional[Callable[[str], List[float]]] = None,
    **kwargs
) -> MemoryCore:
    """
    创建 MemoryCore 实例的便捷函数
    
    Args:
        workflow_id: 工作流ID
        llm_func: 可选的LLM函数
        embed_func: 可选的嵌入函数
        **kwargs: 其他参数传递给 MemoryCore.__init__
        
    Returns:
        MemoryCore 实例
    """
    return MemoryCore(
        workflow_id=workflow_id,
        llm_func=llm_func,
        embed_func=embed_func,
        **kwargs
    )

