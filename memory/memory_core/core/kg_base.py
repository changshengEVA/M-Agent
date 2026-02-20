#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Core 统一入口

core 的门面层（Facade）
workflow 只能通过该类调用 core 能力。
"""

import logging
from typing import Dict, Any, Optional, List
from pathlib import Path

from .repo_context import RepoContext
from .entity_ops import (
    add_entity as core_add_entity,
    merge_entities as core_merge_entities,
    delete_entity_and_edges as core_delete_entity_and_edges,
    rename_entity as core_rename_entity
)
from .relation_ops import (
    redirect_relations as core_redirect_relations,
    delete_relations_of_entity as core_delete_relations_of_entity,
    add_relation as core_add_relation,
    find_relations_by_entities as core_find_relations_by_entities,
    delete_all_relations_by_entities as core_delete_all_relations_by_entities,
    delete_relation as core_delete_relation
)
from .content_ops import (
    append_feature as core_append_feature,
    append_attribute as core_append_attribute,
    move_features as core_move_features,
    move_attributes as core_move_attributes
)
from .integrity_ops import (
    remove_dangling_relations as core_remove_dangling_relations,
    assert_entity_exists as core_assert_entity_exists,
    validate_kg_integrity as core_validate_kg_integrity
)

logger = logging.getLogger(__name__)

# Core 接口统一返回格式
CoreResult = Dict[str, Any]


class KGBase:
    """
    Core 统一入口类
    
    提供对知识图谱核心算子的统一访问接口。
    workflow 只能通过该类调用 core 能力。
    """
    
    def __init__(
        self,
        entity_dir: Path,
        relation_dir: Path,
        repos: Optional[RepoContext] = None,
        event_bus: Optional["EventBus"] = None
    ):
        """
        初始化 KGBase
        
        Args:
            entity_dir: 实体文件目录路径
            relation_dir: 关系文件目录路径
            repos: 可选的 RepoContext 实例，如果为 None 则自动创建
            event_bus: 可选的事件总线实例，用于发布事件
        """
        self.entity_dir = entity_dir
        self.relation_dir = relation_dir
        self.event_bus = event_bus
        
        # 创建或使用提供的 RepoContext
        if repos is None:
            self.repos = RepoContext.from_directories(
                entity_dir=entity_dir,
                relation_dir=relation_dir
            )
        else:
            self.repos = repos
        
        logger.info(f"初始化 KGBase: 实体目录={entity_dir}, 关系目录={relation_dir}")
    
    def _publish_event(self, event_type: str, payload: Dict[str, Any]):
        """
        发布事件到事件总线（如果存在）
        
        Args:
            event_type: 事件类型字符串
            payload: 事件负载字典
        """
        if self.event_bus is not None:
            self.event_bus.publish(event_type, payload)
    
    # ============================================================================
    # 实体操作接口（对外暴露的方法）
    # ============================================================================
    
    def add_entity(
        self,
        entity_id: str,
        entity_type: Optional[str] = None,
        source_info: Optional[Dict[str, Any]] = None
    ) -> CoreResult:
        """
        创建实体
        
        在 KG 中创建一个新的实体节点，初始化为最小合法结构。
        
        Args:
            entity_id: 新实体 ID
            entity_type: 实体类型（可选）
            source_info: 来源信息（当前阶段不使用）
            
        Returns:
            CoreResult 结构
        """
        logger.info(f"KGBase: 创建实体 {entity_id}")
        result = core_add_entity(
            entity_id=entity_id,
            repos=self.repos,
            entity_type=entity_type,
            source_info=source_info
        )
        if result.get("success"):
            self._publish_event("ENTITY_ADDED", {"entity_id": entity_id})
        return result
    
    def merge_entities(
        self,
        target_id: str,
        source_id: str,
        source_info: Optional[Dict[str, Any]] = None
    ) -> CoreResult:
        """
        合并两个实体
        
        在已确认两个实体指向同一现实对象的前提下，将 source_id 的所有信息合并进 target_id，
        并清理 source_id。
        
        Args:
            target_id: 目标实体 ID（保留）
            source_id: 被合并实体 ID（将被移除）
            source_info: 来源信息（当前阶段不使用）
            
        Returns:
            CoreResult 结构
        """
        logger.info(f"KGBase: 合并实体 {source_id} -> {target_id}")
        result = core_merge_entities(
            target_id=target_id,
            source_id=source_id,
            repos=self.repos,
            source_info=source_info
        )
        if result.get("success"):
            self._publish_event("ENTITY_MERGED", {"target_id": target_id, "source_id": source_id})
        return result
    
    def delete_entity(
        self,
        entity_id: str,
        source_info: Optional[Dict[str, Any]] = None
    ) -> CoreResult:
        """
        删除实体及其所有相邻关系
        
        从 KG 中彻底删除指定实体，同时删除所有以该实体为端点的关系。
        
        Args:
            entity_id: 目标实体 ID
            source_info: 来源信息（当前阶段不使用）
            
        Returns:
            CoreResult 结构
        """
        logger.info(f"KGBase: 删除实体 {entity_id}")
        result = core_delete_entity_and_edges(
            entity_id=entity_id,
            repos=self.repos,
            source_info=source_info
        )
        if result.get("success"):
            self._publish_event("ENTITY_DELETED", {"entity_id": entity_id})
        return result
    
    def rename_entity(
        self,
        old_id: str,
        new_id: str,
        source_info: Optional[Dict[str, Any]] = None
    ) -> CoreResult:
        """
        重命名实体
        
        将实体的标识符从 old_id 变更为 new_id，并同步更新所有相关关系。
        
        Args:
            old_id: 原实体 ID
            new_id: 新实体 ID
            source_info: 来源信息（当前阶段不使用）
            
        Returns:
            CoreResult 结构
        """
        logger.info(f"KGBase: 重命名实体 {old_id} -> {new_id}")
        result = core_rename_entity(
            old_id=old_id,
            new_id=new_id,
            repos=self.repos,
            source_info=source_info
        )
        if result.get("success"):
            self._publish_event("ENTITY_RENAMED", {"old_id": old_id, "new_id": new_id})
        return result
    
    # ============================================================================
    # 关系操作接口
    # ============================================================================
    
    def redirect_relations(
        self,
        old_entity_id: str,
        new_entity_id: str,
        source_info: Optional[Dict[str, Any]] = None
    ) -> CoreResult:
        """
        关系端点重定向
        
        将所有指向 old_entity_id 的关系端点重定向至 new_entity_id。
        
        Args:
            old_entity_id: 原实体 ID
            new_entity_id: 新实体 ID
            source_info: 来源信息（当前阶段不使用）
            
        Returns:
            CoreResult 结构
        """
        logger.info(f"KGBase: 重定向关系端点 {old_entity_id} -> {new_entity_id}")
        result = core_redirect_relations(
            old_entity_id=old_entity_id,
            new_entity_id=new_entity_id,
            repos=self.repos,
            source_info=source_info
        )
        if result.get("success"):
            self._publish_event("RELATIONS_REDIRECTED", {"old_entity_id": old_entity_id, "new_entity_id": new_entity_id})
        return result
    
    def delete_relations_of_entity(
        self,
        entity_id: str,
        source_info: Optional[Dict[str, Any]] = None
    ) -> CoreResult:
        """
        删除与某实体相关的所有关系
        
        删除所有以该实体作为 subject 或 object 的关系。
        
        Args:
            entity_id: 目标实体 ID
            source_info: 来源信息（当前阶段不使用）
            
        Returns:
            CoreResult 结构
        """
        logger.info(f"KGBase: 删除实体关系 {entity_id}")
        result = core_delete_relations_of_entity(
            entity_id=entity_id,
            repos=self.repos,
            source_info=source_info
        )
        if result.get("success"):
            self._publish_event("RELATION_DELETED", {"entity_id": entity_id})
        return result
    
    def add_relation(
        self,
        subject: str,
        relation: str,
        object: str,
        confidence: float = 1.0,
        source_info: Optional[Dict[str, Any]] = None
    ) -> CoreResult:
        """
        添加一条关系
        
        在 KG 中创建一条新的关系边，连接两个实体。
        
        Args:
            subject: 主语实体 ID
            relation: 关系类型
            object: 宾语实体 ID
            confidence: 关系置信度，默认 1.0
            source_info: 来源信息（可选）
            
        Returns:
            CoreResult 结构
        """
        logger.info(f"KGBase: 添加关系 {subject} -[{relation}]-> {object}")
        result = core_add_relation(
            subject=subject,
            relation=relation,
            object=object,
            repos=self.repos,
            confidence=confidence,
            source_info=source_info
        )
        if result.get("success"):
            self._publish_event("RELATION_ADDED", {"subject": subject, "relation": relation, "object": object})
        return result
    
    def find_relations_by_entities(
        self,
        entity1_id: str,
        entity2_id: str,
        source_info: Optional[Dict[str, Any]] = None
    ) -> CoreResult:
        """
        查找两个实体之间的所有关系
        
        输入两个实体ID，返回两个实体之间的所有关系。
        
        Args:
            entity1_id: 第一个实体ID
            entity2_id: 第二个实体ID
            source_info: 来源信息（当前阶段不使用）
            
        Returns:
            CoreResult 结构
        """
        logger.info(f"KGBase: 查找实体间关系 {entity1_id} <-> {entity2_id}")
        return core_find_relations_by_entities(
            entity1_id=entity1_id,
            entity2_id=entity2_id,
            repos=self.repos,
            source_info=source_info
        )
    
    def delete_all_relations_by_entities(
        self,
        entity1_id: str,
        entity2_id: str,
        source_info: Optional[Dict[str, Any]] = None
    ) -> CoreResult:
        """
        删除两个实体之间的所有关系
        
        输入两个实体ID，删除两个实体之间的所有关系。
        
        Args:
            entity1_id: 第一个实体ID
            entity2_id: 第二个实体ID
            source_info: 来源信息（当前阶段不使用）
            
        Returns:
            CoreResult 结构
        """
        logger.info(f"KGBase: 删除实体间关系 {entity1_id} <-> {entity2_id}")
        result = core_delete_all_relations_by_entities(
            entity1_id=entity1_id,
            entity2_id=entity2_id,
            repos=self.repos,
            source_info=source_info
        )
        if result.get("success"):
            self._publish_event("RELATION_DELETED", {"entity1_id": entity1_id, "entity2_id": entity2_id})
        return result
    
    def delete_relation(
        self,
        relation_id: str,
        source_info: Optional[Dict[str, Any]] = None
    ) -> CoreResult:
        """
        删除指定关系
        
        输入关系ID，删除这条关系。
        
        Args:
            relation_id: 关系ID
            source_info: 来源信息（当前阶段不使用）
            
        Returns:
            CoreResult 结构
        """
        logger.info(f"KGBase: 删除关系 {relation_id}")
        result = core_delete_relation(
            relation_id=relation_id,
            repos=self.repos,
            source_info=source_info
        )
        if result.get("success"):
            self._publish_event("RELATION_DELETED", {"relation_id": relation_id})
        return result
    
    # ============================================================================
    # 内容操作接口
    # ============================================================================
    
    def append_feature(
        self,
        entity_id: str,
        feature_record: Dict[str, Any],
        source_info: Optional[Dict[str, Any]] = None
    ) -> CoreResult:
        """
        向实体追加一条特征
        
        Args:
            entity_id: 目标实体 ID
            feature_record: 特征记录
            source_info: 来源信息（当前阶段不使用）
            
        Returns:
            CoreResult 结构
        """
        logger.info(f"KGBase: 向实体 {entity_id} 追加特征")
        result = core_append_feature(
            entity_id=entity_id,
            feature_record=feature_record,
            repos=self.repos,
            source_info=source_info
        )
        if result.get("success"):
            self._publish_event("ENTITY_UPDATED", {"entity_id": entity_id})
        return result
    
    def append_attribute(
        self,
        entity_id: str,
        attribute_record: Dict[str, Any],
        source_info: Optional[Dict[str, Any]] = None
    ) -> CoreResult:
        """
        向实体追加一条属性
        
        Args:
            entity_id: 目标实体 ID
            attribute_record: 属性记录
            source_info: 来源信息（当前阶段不使用）
            
        Returns:
            CoreResult 结构
        """
        logger.info(f"KGBase: 向实体 {entity_id} 追加属性")
        result = core_append_attribute(
            entity_id=entity_id,
            attribute_record=attribute_record,
            repos=self.repos,
            source_info=source_info
        )
        if result.get("success"):
            self._publish_event("ENTITY_UPDATED", {"entity_id": entity_id})
        return result
    
    def move_features(
        self,
        from_entity: str,
        to_entity: str,
        source_info: Optional[Dict[str, Any]] = None
    ) -> CoreResult:
        """
        迁移实体特征
        
        将源实体的特征迁移至目标实体，通常作为合并或重构操作的子步骤。
        
        Args:
            from_entity: 源实体 ID
            to_entity: 目标实体 ID
            source_info: 来源信息（当前阶段不使用）
            
        Returns:
            CoreResult 结构
        """
        logger.info(f"KGBase: 迁移特征 {from_entity} -> {to_entity}")
        result = core_move_features(
            from_entity=from_entity,
            to_entity=to_entity,
            repos=self.repos,
            source_info=source_info
        )
        if result.get("success"):
            self._publish_event("ENTITY_UPDATED", {"from_entity": from_entity, "to_entity": to_entity})
        return result
    
    def move_attributes(
        self,
        from_entity: str,
        to_entity: str,
        source_info: Optional[Dict[str, Any]] = None
    ) -> CoreResult:
        """
        迁移实体属性
        
        将源实体的属性迁移至目标实体，通常作为合并或重构操作的子步骤。
        
        Args:
            from_entity: 源实体 ID
            to_entity: 目标实体 ID
            source_info: 来源信息（当前阶段不使用）
            
        Returns:
            CoreResult 结构
        """
        logger.info(f"KGBase: 迁移属性 {from_entity} -> {to_entity}")
        result = core_move_attributes(
            from_entity=from_entity,
            to_entity=to_entity,
            repos=self.repos,
            source_info=source_info
        )
        if result.get("success"):
            self._publish_event("ENTITY_UPDATED", {"from_entity": from_entity, "to_entity": to_entity})
        return result
    
    # ============================================================================
    # 完整性维护接口
    # ============================================================================
    
    def remove_dangling_relations(self) -> CoreResult:
        """
        清理悬挂关系
        
        删除指向不存在实体的关系，保证图结构完整性。
        
        Returns:
            CoreResult 结构
        """
        logger.info("KGBase: 清理悬挂关系")
        return core_remove_dangling_relations(self.repos)
    
    def assert_entity_exists(
        self,
        entity_id: str
    ) -> CoreResult:
        """
        检查实体是否存在
        
        检查指定实体是否存在于 KG 中，不存在则返回失败结果。
        
        Args:
            entity_id: 实体ID
            
        Returns:
            CoreResult 结构
        """
        logger.debug(f"KGBase: 检查实体是否存在 {entity_id}")
        return core_assert_entity_exists(entity_id, self.repos)
    
    def validate_kg_integrity(self) -> CoreResult:
        """
        验证知识图谱完整性
        
        检查 KG 的完整性，包括悬挂关系、孤立实体和数据格式验证。
        
        Returns:
            CoreResult 结构
        """
        logger.info("KGBase: 验证知识图谱完整性")
        return core_validate_kg_integrity(self.repos)
    
    # ============================================================================
    # 辅助方法
    # ============================================================================
    
    def get_repo_context(self) -> RepoContext:
        """
        获取 RepoContext 实例
        
        Returns:
            当前使用的 RepoContext 实例
        """
        return self.repos
    
    def get_entity_count(self) -> int:
        """
        获取实体数量
        
        Returns:
            实体数量
        """
        return len(self.repos.entity.list_ids())
    
    def list_entity_ids(self) -> List[str]:
        """
        获取所有实体ID列表
        
        Returns:
            实体ID列表
        """
        logger.debug("KGBase: 获取所有实体ID列表")
        return self.repos.entity.list_ids()
    
    def get_relation_count(self) -> int:
        """
        获取关系数量
        
        Returns:
            关系数量
        """
        return len(self.repos.relation.list_all())
    
    def get_kg_stats(self) -> Dict[str, Any]:
        """
        获取知识图谱统计信息
        
        Returns:
            包含统计信息的字典
        """
        entity_ids = self.repos.entity.list_ids()
        relations = self.repos.relation.list_all()
        
        # 计算特征和属性总数
        total_features = 0
        total_attributes = 0
        
        for entity_id in entity_ids:
            success, entity_data = self.repos.entity.load(entity_id)
            if success:
                total_features += len(entity_data.get('features', []))
                total_attributes += len(entity_data.get('attributes', []))
        
        return {
            "entity_count": len(entity_ids),
            "relation_count": len(relations),
            "feature_count": total_features,
            "attribute_count": total_attributes,
            "entity_dir": str(self.entity_dir),
            "relation_dir": str(self.relation_dir)
        }