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
        
        # 2. 初始化 KGBase
        self.kg_base = self._init_kg_base()
        
        # 3. 初始化 LLM 和 Embed 函数
        self.llm_func, self.embed_func = self._init_llm_embed(llm_func, embed_func)
        
        # 4. 初始化服务注册列表
        self.services = []
        
        # 5. 初始化 EntityResolutionService（不再传入kg_base）
        self.entity_resolution_service = self._init_entity_resolution_service()
        
        # 6. 注册 entity_resolution_service
        self.register_service(self.entity_resolution_service)
        
        logger.info("MemoryCore 初始化完成")
    
    def _init_kg_base(self) -> KGBase:
        """初始化 KGBase 实例"""
        logger.info(f"初始化 KGBase: entity_dir={self.entity_dir}, relation_dir={self.relation_dir}")
        return KGBase(
            entity_dir=self.entity_dir,
            relation_dir=self.relation_dir
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
        """对齐实体库与 KG 中的实体"""
        try:
            kg_entity_list = self.kg_base.list_entity_ids()
            logger.info(f"KG 中有 {len(kg_entity_list)} 个实体，开始对齐实体库")
            
            if kg_entity_list:
                stats = service.align_library_with_kg_entities(kg_entity_list)
                logger.info(f"实体库对齐完成: {stats}")
                
                # 保存 Library 数据到文件
                try:
                    if hasattr(service, 'data_path') and service.data_path:
                        save_success = service.entity_library.save_to_path(service.data_path)
                        if save_success:
                            logger.info(f"Library 数据已保存到: {service.data_path}")
                        else:
                            logger.warning(f"Library 数据保存失败: {service.data_path}")
                    else:
                        logger.debug("未配置 data_path，跳过 Library 保存")
                except Exception as save_e:
                    logger.warning(f"保存 Library 数据时出错: {save_e}")
            else:
                logger.info("KG 中暂无实体，跳过对齐")
        except Exception as e:
            logger.warning(f"对齐实体库时出错: {e}")
    
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
    
    # ============================================================================
    # 服务注册与事件广播
    # ============================================================================
    
    def register_service(self, service: Any) -> None:
        """
        注册服务
        
        Args:
            service: 服务实例，需要实现事件处理接口（如 on_entity_merged）
        """
        self.services.append(service)
        logger.info(f"注册服务: {service.__class__.__name__}")
    
    def merge_entities(self, source_id: str, target_id: str) -> Dict[str, Any]:
        """
        统一 merge 入口
        
        MemoryCore 成为唯一结构修改入口，执行 KG 合并并广播事件
        
        Args:
            source_id: 源实体ID（将被合并）
            target_id: 目标实体ID（保留）
            
        Returns:
            KG 合并操作结果
        """
        logger.info(f"执行实体合并: {source_id} -> {target_id}")
        
        # 调用 KGBase 执行合并（注意参数顺序：target_id, source_id）
        result = self.kg_base.merge_entities(target_id=target_id, source_id=source_id)
        
        # 如果合并成功，广播事件
        if result.get("success", False):
            self._notify_services("entity_merged", source_id=source_id, target_id=target_id, result=result)
        
        return result
    
    def _notify_services(self, event_name: str, **kwargs) -> None:
        """
        事件广播函数
        
        通知所有注册的服务处理事件
        
        Args:
            event_name: 事件名称（如 "entity_merged"）
            **kwargs: 事件参数
        """
        logger.info(f"广播事件: {event_name}, 参数: {kwargs}")
        
        for service in self.services:
            handler_name = f"on_{event_name}"
            if hasattr(service, handler_name):
                try:
                    handler = getattr(service, handler_name)
                    handler(**kwargs)
                    logger.debug(f"服务 {service.__class__.__name__} 处理事件 {event_name} 成功")
                except Exception as e:
                    logger.error(f"服务 {service.__class__.__name__} 处理事件 {event_name} 时出错: {e}")
    
    def resolve_entity(self, entity_id: str, context: Optional[Dict[str, Any]] = None) -> Any:
        """
        接管实体解析流程
        
        新增：统一解析入口，由 MemoryCore 协调解析与合并
        
        Args:
            entity_id: 待解析的实体ID
            context: 上下文信息
            
        Returns:
            解析判定结果
        """
        logger.info(f"MemoryCore 接管实体解析: {entity_id}")
        
        # 调用 EntityResolutionService 进行判定
        decision = self.entity_resolution_service.resolve_entity(entity_id, context)
        
        # 如果判定为等价实体，执行合并
        if decision.is_same_as_existing() and decision.target_entity_id:
            logger.info(f"判定为等价实体，执行合并: {decision.source_entity_id} -> {decision.target_entity_id}")
            
            # 先应用判定结果（更新 EntityLibrary）
            apply_result = self.entity_resolution_service.apply_decision(decision)
            
            # 执行 KG 合并
            merge_result = self.merge_entities(
                source_id=decision.source_entity_id,
                target_id=decision.target_entity_id
            )
            
            # 返回组合结果
            return {
                "decision": decision,
                "apply_result": apply_result,
                "merge_result": merge_result
            }
        
        # 如果是新建实体，只应用判定结果
        elif decision.is_new_entity():
            logger.info(f"判定为新建实体: {decision.source_entity_id}")
            apply_result = self.entity_resolution_service.apply_decision(decision)
            return {
                "decision": decision,
                "apply_result": apply_result,
                "merge_result": None
            }
        
        # 其他情况
        return {"decision": decision}
    
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