#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
事件类型常量定义

KGBase 发布的所有事件类型，以及系统级事件类型。
"""

from enum import Enum


class EventType(str, Enum):
    """
    事件类型枚举
    
    所有事件类型均为字符串，便于序列化和日志记录。
    """
    
    # ============================================================================
    # 实体类事件
    # ============================================================================
    
    ENTITY_ADDED = "ENTITY_ADDED"
    """实体添加事件"""
    
    ENTITY_DELETED = "ENTITY_DELETED"
    """实体删除事件"""
    
    ENTITY_MERGED = "ENTITY_MERGED"
    """实体合并事件"""
    
    ENTITY_RENAMED = "ENTITY_RENAMED"
    """实体重命名事件"""
    
    ENTITY_UPDATED = "ENTITY_UPDATED"
    """实体更新事件（特征/属性变更）"""
    
    # ============================================================================
    # 关系类事件
    # ============================================================================
    
    RELATION_ADDED = "RELATION_ADDED"
    """关系添加事件"""
    
    RELATION_DELETED = "RELATION_DELETED"
    """关系删除事件"""
    
    RELATIONS_REDIRECTED = "RELATIONS_REDIRECTED"
    """关系重定向事件"""
    
    # ============================================================================
    # 系统级事件
    # ============================================================================
    
    SYSTEM_INITIALIZED = "SYSTEM_INITIALIZED"
    """系统初始化完成事件"""
    
    # ============================================================================
    # 辅助方法
    # ============================================================================
    
    @classmethod
    def get_entity_events(cls) -> list:
        """获取所有实体相关事件"""
        return [
            cls.ENTITY_ADDED,
            cls.ENTITY_DELETED,
            cls.ENTITY_MERGED,
            cls.ENTITY_RENAMED,
            cls.ENTITY_UPDATED,
        ]
    
    @classmethod
    def get_relation_events(cls) -> list:
        """获取所有关系相关事件"""
        return [
            cls.RELATION_ADDED,
            cls.RELATION_DELETED,
            cls.RELATIONS_REDIRECTED,
        ]
    
    @classmethod
    def get_all_events(cls) -> list:
        """获取所有事件类型"""
        return [member.value for member in cls]