#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
entity_resolution 模块

实体解析模块，负责对"新出现的实体标识（entity_id）"进行判定，
判断其是否指向 KG 中的某个既有实体，并据此协调 EntityLibrary 的更新和实体合并。

模块定位：
- KG 写入前的实体判定与对齐层
- 判断"这是一个新实体，还是同一实体的另一种指代方式（别名）"

主要功能：
1. 新实体（NEW_ENTITY）判定：将 entity_id 注册到 EntityLibrary
2. 既有实体的别名（SAME_AS_EXISTING）判定：将 entity_id 加入别名列表，调用 kg_core 请求实体合并
3. 无法判定（UNDECIDED）：证据不足或策略冲突时保守处理
4. 实体库数据持久化：支持从文件路径加载和保存实体库数据
5. 实体对齐：对齐Library数据与KG实体数据列表，保持数据一致性

新增功能：
- EntityLibrary支持从固定路径加载数据（单个JSON文件或目录）
- EntityResolutionService支持data_path参数初始化
- align_library_with_kg_entities方法用于对齐Library与KG实体列表
"""

from .decision import (
    ResolutionType,
    ResolutionDecision,
    create_new_entity_decision,
    create_same_as_existing_decision
)

from .library import (
    EntityRecord,
    EntityLibrary
)

from .strategies import (
    ResolutionStrategy,
)

from .service import (
    ResolutionResult,
    EntityResolutionService,
    create_default_resolution_service
)

# 主要导出
__all__ = [
    # decision.py
    "ResolutionType",
    "ResolutionDecision",
    "create_new_entity_decision",
    "create_same_as_existing_decision",
    
    # library.py
    "EntityRecord",
    "EntityLibrary",
    
    # strategies.py
    "ResolutionStrategy",
    "AliasThenEmbeddingLLMStrategy",
    
    # service.py
    "ResolutionResult",
    "EntityResolutionService",
    "create_default_resolution_service",
]

# 模块版本
__version__ = "1.0.0"
__author__ = "KG System Team"
__description__ = "实体解析模块 - KG 写入前的实体判定与对齐层"