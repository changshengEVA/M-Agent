#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实体解析判定结果的数据结构定义

该文件定义 entity_resolution 内部与外部的统一判定表达，
承载判定类型、目标实体、策略来源与证据说明。

重要约定：
- 该文件 **不包含任何业务逻辑**
- 判定结构是"结论 + 证据"，而非"操作指令"
"""

import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class ResolutionType(Enum):
    """
    实体解析判定类型
    
    表示对新出现的 entity_id 的判定结果
    根据修改需求，只有两种状态：NEW_ENTITY 和 SAME_AS_EXISTING
    """
    NEW_ENTITY = "NEW_ENTITY"           # KG 中不存在对应实体
    SAME_AS_EXISTING = "SAME_AS_EXISTING"  # entity_id 与某个已有实体等价


@dataclass
class ResolutionDecision:
    """
    实体解析判定结果
    
    用于承载解析策略的判定结论及相关证据信息
    """
    # 核心判定结果
    resolution_type: ResolutionType
    
    # 目标实体信息（当 resolution_type 为 SAME_AS_EXISTING 时有效）
    target_entity_id: Optional[str] = None
    
    # 策略来源与证据说明
    strategy_name: str = ""  # 产生此判定的策略名称
    confidence: float = 0.0  # 判定置信度 [0.0, 1.0]
    evidence: Dict[str, Any] = field(default_factory=dict)  # 策略特定的证据数据
    
    # 元信息
    source_entity_id: str = ""  # 被解析的源 entity_id
    timestamp: float = 0.0  # 判定时间戳
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，便于序列化"""
        return {
            "resolution_type": self.resolution_type.value,
            "target_entity_id": self.target_entity_id,
            "strategy_name": self.strategy_name,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "source_entity_id": self.source_entity_id,
            "timestamp": self.timestamp
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ResolutionDecision':
        """从字典创建实例"""
        resolution_type_str = data.get("resolution_type", "NEW_ENTITY")
        
        # 处理兼容性：如果旧数据中有UNDECIDED，则转换为NEW_ENTITY
        if resolution_type_str == "UNDECIDED":
            resolution_type_str = "NEW_ENTITY"
        
        try:
            resolution_type = ResolutionType(resolution_type_str)
        except ValueError:
            # 如果解析失败，默认为NEW_ENTITY
            resolution_type = ResolutionType.NEW_ENTITY
        
        return cls(
            resolution_type=resolution_type,
            target_entity_id=data.get("target_entity_id"),
            strategy_name=data.get("strategy_name", ""),
            confidence=data.get("confidence", 0.0),
            evidence=data.get("evidence", {}),
            source_entity_id=data.get("source_entity_id", ""),
            timestamp=data.get("timestamp", 0.0)
        )
    
    def is_new_entity(self) -> bool:
        """是否为新建实体判定"""
        return self.resolution_type == ResolutionType.NEW_ENTITY
    
    def is_same_as_existing(self) -> bool:
        """是否为等价实体判定"""
        return self.resolution_type == ResolutionType.SAME_AS_EXISTING
    
    def get_action_description(self) -> str:
        """获取人类可读的操作描述"""
        if self.is_new_entity():
            return f"新建实体: {self.source_entity_id}"
        else:
            return f"实体等价: {self.source_entity_id} -> {self.target_entity_id}"
    
    def __str__(self) -> str:
        """字符串表示"""
        if self.is_new_entity():
            return f"ResolutionDecision(NEW_ENTITY: {self.source_entity_id}, confidence={self.confidence:.2f})"
        else:
            return f"ResolutionDecision(SAME_AS_EXISTING: {self.source_entity_id} -> {self.target_entity_id}, confidence={self.confidence:.2f})"


def create_new_entity_decision(
    entity_id: str,
    strategy_name: str = "",
    confidence: float = 1.0,
    evidence: Optional[Dict[str, Any]] = None
) -> ResolutionDecision:
    """
    创建新建实体判定
    
    Args:
        entity_id: 源实体ID
        strategy_name: 策略名称
        confidence: 置信度
        evidence: 证据数据
        
    Returns:
        ResolutionDecision 实例
    """
    return ResolutionDecision(
        resolution_type=ResolutionType.NEW_ENTITY,
        source_entity_id=entity_id,
        strategy_name=strategy_name,
        confidence=confidence,
        evidence=evidence or {},
        timestamp=0.0  # 将在使用时设置
    )


def create_same_as_existing_decision(
    source_entity_id: str,
    target_entity_id: str,
    strategy_name: str = "",
    confidence: float = 1.0,
    evidence: Optional[Dict[str, Any]] = None
) -> ResolutionDecision:
    """
    创建等价实体判定
    
    Args:
        source_entity_id: 源实体ID
        target_entity_id: 目标实体ID
        strategy_name: 策略名称
        confidence: 置信度
        evidence: 证据数据
        
    Returns:
        ResolutionDecision 实例
    """
    return ResolutionDecision(
        resolution_type=ResolutionType.SAME_AS_EXISTING,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        strategy_name=strategy_name,
        confidence=confidence,
        evidence=evidence or {},
        timestamp=0.0  # 将在使用时设置
    )

