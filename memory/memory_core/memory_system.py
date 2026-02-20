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

# 导入 load_model 中的 OpenAI 模型函数
from load_model.OpenAIcall import get_llm, get_embed_model

logger = logging.getLogger(__name__)


class MemoryCore:
    """
    Memory Core 主类
    
    初始化步骤：
    1. 根据工作流ID确定数据路径
    2. 初始化 KGBase（实体目录和关系目录）
    3. 初始化 EntityResolutionService（使用 KGBase 和 LLM/Embed 模型）
    4. 暴露两个数据加载接口
    """
    
    def __init__(
        self,
        workflow_id: str,
        llm_func: Optional[Callable[[str], str]] = None,
        embed_func: Optional[Callable[[str], List[float]]] = None,
        llm_temperature: float = 0.0,
        similarity_threshold: float = 0.7,
        top_k: int = 3,
        use_threshold: bool = True
    ):
        """
        初始化 MemoryCore
        
        Args:
            workflow_id: 工作流ID，用于确定数据存储路径
            llm_func: 可选的LLM函数，如果为None则使用默认OpenAI LLM
            embed_func: 可选的嵌入函数，如果为None则使用默认OpenAI Embedding
            llm_temperature: LLM温度参数（仅当使用默认LLM时有效）
            similarity_threshold: 实体解析相似度阈值
            top_k: 实体解析返回前K个候选
            use_threshold: 是否使用阈值模式
        """
        self.workflow_id = workflow_id
        self.llm_temperature = llm_temperature
        self.similarity_threshold = similarity_threshold
        self.top_k = top_k
        self.use_threshold = use_threshold
        
        # 1. 构建数据路径
        self.kg_data_path = Path(f"data/memory/{workflow_id}/kg_data")
        self.entity_dir = self.kg_data_path / "entity"
        self.relation_dir = self.kg_data_path / "relation"
        self.entity_library_path = self.kg_data_path / "entity_library"
        
        # 确保目录存在
        self.entity_dir.mkdir(parents=True, exist_ok=True)
        self.relation_dir.mkdir(parents=True, exist_ok=True)
        self.entity_library_path.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"初始化 MemoryCore，工作流ID: {workflow_id}")
        logger.info(f"KG数据路径: {self.kg_data_path}")
        logger.info(f"实体目录: {self.entity_dir}")
        logger.info(f"关系目录: {self.relation_dir}")
        logger.info(f"实体库路径: {self.entity_library_path}")
        
        # 2. 初始化 EventBus
        self.event_bus = EventBus()
        logger.info("EventBus 初始化完成")
        
        # 3. 初始化 KGBase（传入 event_bus）
        self.kg_base = self._init_kg_base()
        
        # 4. 初始化 LLM 和 Embed 函数
        self.llm_func, self.embed_func = self._init_llm_embed(llm_func, embed_func)
        
        # 5. 初始化 EntityResolutionService并进行注册
        self.entity_resolution_service = self._init_entity_resolution_service()
        self.register_service(self.entity_resolution_service)

        self.feature_search = None
        
        # 8. 根据重构原则，MemoryCore 不再注册自身到 EventBus
        # 不再监听 ENTITY_ADDED 事件来自动触发解析
        # 解析将由显式的 Resolution Pass 调度
        
        # 9. 发布系统初始化事件
        self.event_bus.publish(EventType.SYSTEM_INITIALIZED, {})
        
        logger.info("MemoryCore 初始化完成")
    
    def _init_kg_base(self) -> KGBase:
        """初始化 KGBase 实例"""
        logger.info(f"初始化 KGBase: entity_dir={self.entity_dir}, relation_dir={self.relation_dir}")
        return KGBase(
            entity_dir=self.entity_dir,
            relation_dir=self.relation_dir,
            event_bus=self.event_bus
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
            llm_func = get_llm(self.llm_temperature)
        
        if embed_func is None:
            logger.info("使用默认 OpenAI Embedding")
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
    
    def _align_entity_library_with_kg(self, service: EntityResolutionService) -> None:
        """
        初始化 EntityLibrary 与 KG 的同步
        
        比对 library 和 kg_data 中的内容，如果对不上就直接调用 library 中的 rebuild_from_kg 进行重建，
        然后再用 resolve_unresolved_entities，这样就对齐了。
        """
        try:
            # 1. 获取 KG 中的所有实体 ID
            kg_entity_ids = set(self.kg_base.list_entity_ids())
            logger.info(f"KG 中有 {len(kg_entity_ids)} 个实体")
            
            # 2. 获取 EntityLibrary 中的所有实体 ID
            library_entity_ids = set()
            for entity_id in service.entity_library.entities.keys():
                library_entity_ids.add(entity_id)
            logger.info(f"EntityLibrary 中有 {len(library_entity_ids)} 个实体")
            
            # 3. 比较两者是否一致
            if kg_entity_ids == library_entity_ids:
                logger.info("EntityLibrary 与 KG 实体一致，无需重建")
                
                # 尝试加载已有的 Library 数据
                try:
                    if hasattr(service, 'data_path') and service.data_path:
                        load_success = service.entity_library.load_from_path(service.data_path)
                        if load_success:
                            logger.info(f"EntityLibrary 从文件加载成功: {service.data_path}")
                        else:
                            logger.info(f"EntityLibrary 文件不存在或加载失败，将从头构建: {service.data_path}")
                    else:
                        logger.debug("未配置 data_path，EntityLibrary 将从头构建")
                except Exception as load_e:
                    logger.warning(f"加载 EntityLibrary 数据时出错: {load_e}")
                    
            else:
                logger.warning(f"EntityLibrary 与 KG 实体不一致，需要重建")
                logger.info(f"KG 中有但 Library 中没有的实体: {kg_entity_ids - library_entity_ids}")
                logger.info(f"Library 中有但 KG 中没有的实体: {library_entity_ids - kg_entity_ids}")
                
                # 4. 构建 kg_data 用于重建
                kg_data = {"entities": []}
                for entity_id in kg_entity_ids:
                    success, entity_data = self.kg_base.repos.entity.load(entity_id)
                    if success:
                        # 确保实体数据包含必要的字段
                        entity_info = {
                            "id": entity_id,
                            "name": entity_data.get("name", entity_id),
                            "type": entity_data.get("type"),
                            "metadata": entity_data.get("metadata", {})
                        }
                        kg_data["entities"].append(entity_info)
                    else:
                        logger.warning(f"无法加载实体 {entity_id} 的详细信息")
                
                # 5. 调用 rebuild_from_kg 进行重建
                logger.info(f"开始从 KG 重建 EntityLibrary，共 {len(kg_data['entities'])} 个实体")
                rebuild_success = service.entity_library.rebuild_from_kg(kg_data)
                if rebuild_success:
                    logger.info("EntityLibrary 重建成功")
                    
                    # 6. 调用 resolve_unresolved_entities 对齐
                    logger.info("开始解析未解析的实体以完成对齐")
                    decisions = service.resolve_unresolved_entities()
                    logger.info(f"解析完成，共 {len(decisions)} 个决策")
                    
                    # 保存重建后的 Library 数据
                    if hasattr(service, 'data_path') and service.data_path:
                        save_success = service.save_library()
                        if save_success:
                            logger.info(f"EntityLibrary 数据保存成功: {service.data_path}")
                        else:
                            logger.warning(f"EntityLibrary 数据保存失败: {service.data_path}")
                else:
                    logger.error("EntityLibrary 重建失败")
                    
        except Exception as e:
            logger.warning(f"初始化 EntityLibrary 同步时出错: {e}")
    
    # ============================================================================
    # 公开接口
    # ============================================================================
    
    def load_from_dialogue_json(self, json_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        从单个对话 JSON 数据加载到 KG
        
        Args:
            json_data: 对话 JSON 数据（格式参照 kg_candidate 中的单个文件）
            
        Returns:
            操作结果字典
        """
        from .workflow.load_dialogue_data import load_from_dialogue_json
        
        logger.info("调用 load_from_dialogue_json 接口")
        return load_from_dialogue_json(
            json_data=json_data,
            kg_base=self.kg_base,
            entity_resolution_service=self.entity_resolution_service,
            memory_core=self  # 传递 MemoryCore 实例
        )
    
    def load_from_dialogue_path(self, path: Path) -> Dict[str, Any]:
        """
        从对话数据目录加载所有文件到 KG
        
        Args:
            path: kg_candidate 目录路径
            
        Returns:
            操作结果字典
        """
        from .workflow.load_dialogue_data import load_from_dialogue_path
        
        logger.info(f"调用 load_from_dialogue_path 接口，路径: {path}")
        return load_from_dialogue_path(
            path=path,
            kg_base=self.kg_base,
            entity_resolution_service=self.entity_resolution_service,
            memory_core=self  # 传递 MemoryCore 实例
        )
    
    def search_feature_by_entity_id():
        
        pass
    # ============================================================================
    # 服务注册与事件广播
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
            logger.info(f"注册服务到 EventBus: {service.__class__.__name__}")
        else:
            logger.warning(f"服务 {service.__class__.__name__} 不是 BaseService 子类，无法注册到 EventBus")
    
    
    def run_entity_resolution_pass(self) -> Dict[str, Any]:
        """
        执行实体解析阶段（Resolution Pass）
        
        行为如下：
        Step 1：调用 entity_resolution_service.resolve_unresolved_entities()
        Step 2：MemoryCore 根据 decision 决定是否执行 merge
        Step 3：不要手动更新 Library。merge 会触发 ENTITY_MERGED event，
                Service Listener 会自动同步 Library。
        
        Returns:
            解析阶段执行结果
        """
        logger.info("开始执行实体解析阶段（Resolution Pass）")
        
        # Step 1: 获取解析决策
        decisions = self.entity_resolution_service.resolve_unresolved_entities()
        logger.info(f"获取到 {len(decisions)} 个解析决策")
        
        results = {
            "total_decisions": len(decisions),
            "same_as_existing": 0,
            "new_entities": 0,
            "merged": 0,
            "merge_errors": [],
            "decisions": []
        }
        
        # Step 2: 根据 decision 决定是否执行 merge
        for decision in decisions:
            decision_dict = decision.to_dict()
            results["decisions"].append(decision_dict)
            
            if decision.is_same_as_existing() and decision.target_entity_id:
                results["same_as_existing"] += 1
                
                # 执行 KG 合并
                try:
                    logger.info(f"执行实体合并: {decision.source_entity_id} -> {decision.target_entity_id}")
                    merge_result = self.kg_base.merge_entities(
                        target_id=decision.target_entity_id,
                        source_id=decision.source_entity_id
                    )
                    
                    if merge_result.get("success", False):
                        results["merged"] += 1
                        logger.info(f"实体合并成功: {decision.source_entity_id} -> {decision.target_entity_id}")
                    else:
                        error_msg = f"合并失败: {merge_result.get('error', 'unknown')}"
                        results["merge_errors"].append({
                            "source": decision.source_entity_id,
                            "target": decision.target_entity_id,
                            "error": error_msg
                        })
                        logger.warning(f"实体合并失败: {error_msg}")
                        
                except Exception as e:
                    error_msg = f"合并异常: {str(e)}"
                    results["merge_errors"].append({
                        "source": decision.source_entity_id,
                        "target": decision.target_entity_id,
                        "error": error_msg
                    })
                    logger.error(f"实体合并异常: {e}")
                    
            elif decision.is_new_entity():
                results["new_entities"] += 1
                logger.info(f"新建实体判定: {decision.source_entity_id}（不执行任何操作）")
        
        # Step 3: 不要手动更新 Library（由事件驱动自动同步）
        logger.info(f"解析阶段完成: 总计 {results['total_decisions']} 个决策, "
                   f"等价实体 {results['same_as_existing']} 个, "
                   f"新建实体 {results['new_entities']} 个, "
                   f"成功合并 {results['merged']} 个")
        
        results["success"] = len(results["merge_errors"]) == 0
        return results
    
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