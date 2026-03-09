#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RepoContext 数据类

用于向 core 层算子显式提供所有需要的持久化操作能力。
core 不直接依赖具体的 repository 实现，而是通过 RepoContext 统一访问。
"""

from dataclasses import dataclass
from typing import Optional
from pathlib import Path

# 导入持久化仓库类
try:
    from ..persistence.entity_repository import EntityRepository
    from ..persistence.relation_repository import RelationRepository
    from ..persistence.feature_repository import FeatureRepository
    from ..persistence.attribute_repository import AttributeRepository
except ImportError:
    # 用于测试环境
    from memory.memory_core.persistence.entity_repository import EntityRepository
    from memory.memory_core.persistence.relation_repository import RelationRepository
    from memory.memory_core.persistence.feature_repository import FeatureRepository
    from memory.memory_core.persistence.attribute_repository import AttributeRepository


@dataclass
class RepoContext:
    """
    持久化操作组件集合
    
    用于向 core 层算子显式提供所有需要的持久化操作能力。
    core 只能通过 RepoContext 访问持久化层。
    """
    entity: EntityRepository       # 实体的创建 / 读取 / 删除
    relation: RelationRepository   # 关系的存储与重定向
    feature: FeatureRepository     # 特征的追加与读取
    attribute: AttributeRepository # 属性的追加与读取
    
    @classmethod
    def from_directories(
        cls,
        entity_dir: Path,
        relation_dir: Path,
        entity_repository: Optional[EntityRepository] = None,
        relation_repository: Optional[RelationRepository] = None,
        feature_repository: Optional[FeatureRepository] = None,
        attribute_repository: Optional[AttributeRepository] = None
    ) -> 'RepoContext':
        """
        从目录路径创建 RepoContext
        
        Args:
            entity_dir: 实体文件目录路径
            relation_dir: 关系文件目录路径
            entity_repository: 可选的 EntityRepository 实例
            relation_repository: 可选的 RelationRepository 实例
            feature_repository: 可选的 FeatureRepository 实例
            attribute_repository: 可选的 AttributeRepository 实例
            
        Returns:
            配置好的 RepoContext 实例
        """
        # 创建实体仓库
        if entity_repository is None:
            entity_repository = EntityRepository(entity_dir)
        
        # 创建关系仓库
        if relation_repository is None:
            relation_repository = RelationRepository(
                relation_dir,
                entity_repository=entity_repository
            )
        
        # 创建特征仓库
        if feature_repository is None:
            feature_repository = FeatureRepository(entity_repository)
        
        # 创建属性仓库
        if attribute_repository is None:
            attribute_repository = AttributeRepository(entity_repository)
        
        return cls(
            entity=entity_repository,
            relation=relation_repository,
            feature=feature_repository,
            attribute=attribute_repository
        )
