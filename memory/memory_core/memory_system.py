#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Memory Core 系统

核心入口类，封装 KGBase 和 EntityResolutionService，提供对话数据加载接口。
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List

from memory.memory_core.core.kg_base import KGBase
from memory.memory_core.services_bank.entity_resolution.service import (
    EntityResolutionService,
    create_default_resolution_service
)
from memory.memory_core.system.event_bus import EventBus
from memory.memory_core.system.event_types import EventType

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
        action_prompt_version: str = "v1",
        memory_owner_name: str = "changshengEVA",
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
            action_prompt_version: Atomic facts 构建 prompt 版本
            memory_owner_name: 记忆主体名称
        """
        self.workflow_id = workflow_id
        self.llm_temperature = llm_temperature
        self.similarity_threshold = similarity_threshold
        self.top_k = top_k
        self.use_threshold = use_threshold
        self.scene_prompt_version = str(scene_prompt_version or "v2")
        self.action_prompt_version = str(action_prompt_version or "v1")
        self.memory_owner_name = str(memory_owner_name or "changshengEVA")
        
        # 1. 构建数据路径（极简化：不再使用 kg_data/entity/relation 文件结构）
        self.memory_root = Path(f"data/memory/{workflow_id}")
        self.local_store_dir = self.memory_root / "local_store"
        self.entity_library_path = self.local_store_dir / "entity_library"
        self.scene_dir = self.memory_root / "scene"
        self.dialogues_dir = self.memory_root / "dialogues"
        self.episodes_dir = self.memory_root / "episodes"

        # 确保目录存在
        self.local_store_dir.mkdir(parents=True, exist_ok=True)
        self.entity_library_path.mkdir(parents=True, exist_ok=True)
        self.scene_dir.mkdir(parents=True, exist_ok=True)
        self.dialogues_dir.mkdir(parents=True, exist_ok=True)
        self.episodes_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"初始化 MemoryCore，工作流ID: {workflow_id}")
        logger.info(f"Memory根目录: {self.memory_root}")
        logger.info(f"LocalStore目录: {self.local_store_dir}")
        logger.info(f"实体库路径: {self.entity_library_path}")
        logger.info(f"Scene目录: {self.scene_dir}")
        logger.info(f"Dialogues目录: {self.dialogues_dir}")
        logger.info(f"Episodes目录: {self.episodes_dir}")
        logger.info(f"Scene prompt版本: {self.scene_prompt_version}")
        logger.info(f"Action prompt版本: {self.action_prompt_version}")
        logger.info(f"Memory owner: {self.memory_owner_name}")
        
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
        
        # 6. 发布初始化事件
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
            from load_model.OpenAIcall import get_llm
            llm_func = get_llm(self.llm_temperature)

        if embed_func is None:
            logger.info("使用默认本地 Embedding")
            from load_model.BGEcall import get_embed_model
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
            data_path=str(self.entity_library_path)
        )
        
        # 对齐实体库与 KG 中的实体
        self._align_entity_library_with_kg(service)
        
        return service
    # ============================================================================
    # 与附属数据库的对齐操作
    # ============================================================================
    def _align_entity_library_with_kg(self, service: EntityResolutionService) -> None:
        """
        初始化 EntityLibrary 与 KG 的同步（极简版）。

        当前模式下 KG 数据以 Neo4j 为主存储，本地 EntityLibrary 仅为解析辅助索引。
        这里直接以 KG 为准重建 EntityLibrary，避免与旧文件持久层耦合。
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

            rebuilt = service.entity_library.rebuild_from_kg(kg_data)
            if not rebuilt:
                logger.warning("EntityLibrary rebuild_from_kg failed")
                return

            if service.data_path:
                service.entity_library.save_to_path(service.data_path)
            logger.info("EntityLibrary 已按 KG 重建完成: %s 个实体", len(kg_data["entities"]))
        except Exception as e:
            logger.warning(f"初始化 EntityLibrary 同步时出错: {e}")
    
    # ============================================================================
    # 数据导入
    # ============================================================================
    def load_from_episode_path(self, path: Path) -> Dict[str, Any]:
        """
        新的数据导入接口：从 episodes 路径导入并在系统内部构建 scene + atomic facts。
        """
        from .workflow.build.load_from_episode import (
            load_from_episode_path as workflow_load_from_episode_path,
        )

        logger.info(f"调用 load_from_episode_path 接口，路径: {path}")
        return workflow_load_from_episode_path(path=path, memory_core=self)

    
    # ============================================================================
    # 公开接口
    # ============================================================================
    def search_content(self, dialogue_id: str, episode_id: str) -> Dict[str, Any]:
        """
        内容检索公开接口。
        输入 dialogue_id 和 episode_id，返回对应对话片段的具体内容。
        """
        from .workflow.search.content_search import search_content as workflow_search_content

        logger.info(
            "调用 search_content 接口: dialogue_id=%s, episode_id=%s",
            dialogue_id,
            episode_id,
        )
        return workflow_search_content(
            dialogue_id=dialogue_id,
            episode_id=episode_id,
            scene_dir=self.scene_dir,
            dialogues_dir=self.dialogues_dir,
            episodes_dir=self.episodes_dir,
        )

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
        基于 scene.facts[*].embedding（或 Atomic fact 文本回退 embedding）进行向量检索。
        """
        from .workflow.search.action_search import search_details as workflow_search_details

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
        )

    def search_actions(self, action_query: str, topk: int = 5) -> Dict[str, Any]:
        """
        兼容旧接口名，等价于 search_details。
        """
        return self.search_details(detail_query=action_query, topk=topk)

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
