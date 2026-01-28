#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_sys 模块 - 知识图谱管理系统（重构版）

提供知识图谱的创建、管理和实体合并功能，采用模块化设计。

主要类:
    KGManager: 知识图谱管理类，提供实体和关系的存储、查询和合并功能。
    EntityStorage: 实体存储管理类，负责实体的存储、加载和合并操作。
    RelationStorage: 关系存储管理类，负责关系的存储、查找和合并操作。
    FeatureAttributeStorage: 特征和属性存储管理类，负责特征和属性的存储管理。
    SourceManager: 来源信息管理类，负责来源信息的合并和去重逻辑。
    EntityOperations: 实体操作工具类，负责实体的合并、删除、修改等操作。

使用示例:
    >>> from memory.memory_sys import KGManager
    >>> kg_manager = KGManager("data/memory/test3/kg_data")
    >>> result = kg_manager.combine_entity("entity_a", "entity_b")
    >>> print(result["success"])
"""

from .kg_manager import KGManager
from .storage.entity_storage import EntityStorage
from .storage.relation_storage import RelationStorage
from .storage.feature_attribute_storage import FeatureAttributeStorage
from .source_manager import SourceManager
from .entity_operations import EntityOperations

__all__ = [
    "KGManager",
    "EntityStorage",
    "RelationStorage",
    "FeatureAttributeStorage",
    "SourceManager",
    "EntityOperations"
]
__version__ = "2.0.0"