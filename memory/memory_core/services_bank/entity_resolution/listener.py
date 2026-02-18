#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实体解析监听器

职责：
- 监听实体合并事件，更新 EntityLibrary
- 保持 EntityResolutionService 与 KG 操作同步

设计原则：
- 单一职责：只处理实体合并事件
- 可独立测试
- 与 EntityResolutionService 解耦
"""

import logging
import time
from typing import Any, TYPE_CHECKING

# 类型检查时导入EntityLibrary，避免循环导入
if TYPE_CHECKING:
    from .library import EntityLibrary
else:
    # 运行时使用字符串类型提示
    EntityLibrary = "EntityLibrary"  # type: ignore

logger = logging.getLogger(__name__)


class EntityMergeListener:
    """实体合并监听器"""
    
    def __init__(self, entity_library: EntityLibrary):
        """
        初始化实体合并监听器
        
        Args:
            entity_library: EntityLibrary 实例，用于更新实体库
        """
        self.entity_library = entity_library
        logger.info("初始化 EntityMergeListener")
    
    def on_entity_merged(self, source_id: str, target_id: str, **kwargs) -> None:
        """
        监听实体合并事件
        
        当 MemoryCore 执行实体合并时调用此方法，用于更新 EntityLibrary
        
        Args:
            source_id: 源实体ID（将被合并）
            target_id: 目标实体ID（保留）
            **kwargs: 其他参数（如合并结果等）
        """
        logger.info(f"收到实体合并事件: {source_id} -> {target_id}")
        
        try:
            # 检查目标实体是否在 EntityLibrary 中
            if target_id not in self.entity_library.entities:
                # 如果目标实体不在 Library 中，先添加它
                logger.info(f"目标实体 {target_id} 不在 EntityLibrary 中，先添加")
                add_success = self.entity_library.add_entity(
                    entity_id=target_id,
                    canonical_name=target_id,
                    metadata={
                        "added_via": "entity_merge_event",
                        "source_entity": source_id,
                        "timestamp": time.time()
                    }
                )
                
                if not add_success:
                    logger.warning(f"无法添加目标实体到 EntityLibrary: {target_id}")
                    return
            
            # 更新 EntityLibrary：将源实体ID添加为目标实体的别名
            success = self.entity_library.add_alias(
                entity_id=target_id,
                alias=source_id
            )
            
            if success:
                logger.info(f"EntityLibrary 更新成功: {source_id} 作为 {target_id} 的别名")
            else:
                # 如果添加别名失败，可能是别名已存在或其他原因
                # 检查源实体是否已经在 Library 中
                if source_id in self.entity_library.entities:
                    # 如果源实体在 Library 中，可能需要更新其记录
                    logger.info(f"源实体 {source_id} 已在 EntityLibrary 中，可能需要特殊处理")
                
                logger.warning(f"EntityLibrary 更新失败: {source_id} -> {target_id}")
                
        except Exception as e:
            logger.error(f"处理实体合并事件时出错: {e}")
    
    def __str__(self) -> str:
        """字符串表示"""
        return f"EntityMergeListener(library_entities={len(self.entity_library.entities)})"


# 便捷函数：创建监听器实例
def create_entity_merge_listener(entity_library: EntityLibrary) -> EntityMergeListener:
    """
    创建实体合并监听器
    
    Args:
        entity_library: EntityLibrary 实例
        
    Returns:
        EntityMergeListener 实例
    """
    return EntityMergeListener(entity_library)