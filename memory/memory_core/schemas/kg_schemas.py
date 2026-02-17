#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KG数据结构定义

定义知识图谱中实体、关系、特征、属性等数据结构的模式
"""

from typing import Dict, List, Any, Optional, TypedDict, Union
from datetime import datetime


# ============================================================================
# 基础类型定义
# ============================================================================

class SourceInfo(TypedDict, total=False):
    """来源信息"""
    dialogue_id: str
    episode_id: str
    generated_at: str  # ISO 8601格式时间戳
    scene_id: Optional[str]


# ============================================================================
# 实体相关模式
# ============================================================================

class FeatureRecord(TypedDict, total=False):
    """特征记录"""
    entity_id: str
    feature: str  # 特征描述文本
    scene_id: Optional[str]
    confidence: float  # 置信度，0.0-1.0
    sources: List[SourceInfo]


class AttributeRecord(TypedDict, total=False):
    """属性记录"""
    entity: str
    field: str  # 属性字段名，如"年龄"、"职业"
    value: Union[str, int, float, bool, List, Dict]  # 属性值
    confidence: float  # 置信度，0.0-1.0
    sources: List[SourceInfo]


class EntityData(TypedDict, total=False):
    """实体数据结构"""
    id: str  # 实体ID，也是文件名（不含.json后缀）
    uid: str  # 实体唯一标识符（UUID），用于内部引用
    type: str  # 实体类型，如"person"、"organization"、"location"
    confidence: float  # 实体整体置信度，0.0-1.0
    sources: List[SourceInfo]  # 实体来源信息
    features: List[FeatureRecord]  # 特征列表
    attributes: List[AttributeRecord]  # 属性列表


# ============================================================================
# 关系相关模式
# ============================================================================

class RelationRecord(TypedDict, total=False):
    """关系记录"""
    id: str  # 关系ID，也是文件名（不含.json后缀）
    subject: str  # 主语实体ID
    relation: str  # 关系类型，如"works_at"、"located_in"
    object: str  # 宾语实体ID
    confidence: float  # 关系置信度，0.0-1.0
    sources: List[SourceInfo]  # 关系来源信息


# ============================================================================
# 仓库方法类型提示
# ============================================================================

class EntityRepositoryMethods:
    """EntityRepository方法类型提示"""
    
    @staticmethod
    def exists(entity_id: str) -> bool:
        """
        检查实体是否存在
        
        Args:
            entity_id: 实体ID
            
        Returns:
            实体文件是否存在
        """
        pass
    
    @staticmethod
    def load(entity_id: str) -> tuple[bool, Optional[EntityData]]:
        """
        加载实体数据
        
        Args:
            entity_id: 实体ID
            
        Returns:
            (成功状态, 实体数据) 如果失败返回(False, None)
        """
        pass
    
    @staticmethod
    def save(entity_data: EntityData) -> bool:
        """
        保存实体数据
        
        Args:
            entity_data: 完整的实体数据
            
        Returns:
            保存成功返回True，否则返回False
        """
        pass
    
    @staticmethod
    def delete(entity_id: str) -> bool:
        """
        删除实体文件
        
        Args:
            entity_id: 实体ID
            
        Returns:
            删除成功返回True，否则返回False
        """
        pass
    
    @staticmethod
    def list_ids() -> List[str]:
        """
        列出所有实体ID
        
        Returns:
            实体ID列表
        """
        pass
    
    @staticmethod
    def append_feature(entity_id: str, feature_record: FeatureRecord) -> bool:
        """
        添加特征到实体
        
        Args:
            entity_id: 实体ID
            feature_record: 特征记录
            
        Returns:
            添加成功返回True，否则返回False
        """
        pass
    
    @staticmethod
    def append_attribute(entity_id: str, attribute_record: AttributeRecord) -> bool:
        """
        添加属性到实体
        
        Args:
            entity_id: 实体ID
            attribute_record: 属性记录
            
        Returns:
            添加成功返回True，否则返回False
        """
        pass


class RelationRepositoryMethods:
    """RelationRepository方法类型提示"""
    
    @staticmethod
    def save(relation_record: RelationRecord) -> bool:
        """
        保存关系记录
        
        Args:
            relation_record: 关系记录
            
        Returns:
            保存成功返回True，否则返回False
        """
        pass
    
    @staticmethod
    def delete(relation_id: str) -> bool:
        """
        删除关系文件
        
        Args:
            relation_id: 关系ID（文件名）
            
        Returns:
            删除成功返回True，否则返回False
        """
        pass
    
    @staticmethod
    def list_all() -> List[RelationRecord]:
        """
        列出所有关系记录
        
        Returns:
            所有关系记录的列表
        """
        pass
    
    @staticmethod
    def find_by_subject(entity_id: str) -> List[RelationRecord]:
        """
        查找实体作为主语的所有关系
        
        Args:
            entity_id: 实体ID
            
        Returns:
            该实体作为主语的所有关系记录
        """
        pass
    
    @staticmethod
    def find_by_object(entity_id: str) -> List[RelationRecord]:
        """
        查找实体作为宾语的所有关系
        
        Args:
            entity_id: 实体ID
            
        Returns:
            该实体作为宾语的所有关系记录
        """
        pass
    
    @staticmethod
    def update_endpoint(old_entity_id: str, new_entity_id: str) -> Dict[str, Any]:
        """
        更新关系端点
        
        Args:
            old_entity_id: 旧的实体ID
            new_entity_id: 新的实体ID
            
        Returns:
            执行结果字典，包含更新和删除的统计信息
        """
        pass


class FeatureRepositoryMethods:
    """FeatureRepository方法类型提示"""
    
    @staticmethod
    def append(entity_id: str, feature_record: FeatureRecord) -> Dict[str, Any]:
        """
        添加特征到实体
        
        Args:
            entity_id: 实体ID
            feature_record: 特征记录
            
        Returns:
            执行状态字典，包含成功状态和详细信息
        """
        pass
    
    @staticmethod
    def list(entity_id: str) -> List[FeatureRecord]:
        """
        列出实体的所有特征
        
        Args:
            entity_id: 实体ID
            
        Returns:
            该实体的所有特征记录列表
        """
        pass


class AttributeRepositoryMethods:
    """AttributeRepository方法类型提示"""
    
    @staticmethod
    def set(entity_id: str, attribute_record: AttributeRecord) -> Dict[str, Any]:
        """
        设置实体属性
        
        Args:
            entity_id: 实体ID
            attribute_record: 属性记录
            
        Returns:
            执行状态字典，包含成功状态和详细信息
        """
        pass
    
    @staticmethod
    def list(entity_id: str) -> List[AttributeRecord]:
        """
        列出实体的所有属性
        
        Args:
            entity_id: 实体ID
            
        Returns:
            该实体的所有属性记录列表
        """
        pass


# ============================================================================
# 数据验证函数
# ============================================================================

def validate_entity_data(data: Dict[str, Any]) -> bool:
    """
    验证实体数据是否符合模式
    
    Args:
        data: 待验证的实体数据
        
    Returns:
        验证通过返回True，否则返回False
    """
    try:
        # 检查必要字段
        if 'id' not in data:
            return False
        
        # 检查字段类型
        if not isinstance(data.get('id', ''), str):
            return False
        
        if 'uid' in data and not isinstance(data['uid'], str):
            return False
        
        if 'type' in data and not isinstance(data['type'], str):
            return False
        
        if 'confidence' in data and not isinstance(data['confidence'], (int, float)):
            return False
        
        # 检查列表字段
        if 'sources' in data and not isinstance(data['sources'], list):
            return False
        
        if 'features' in data and not isinstance(data['features'], list):
            return False
        
        if 'attributes' in data and not isinstance(data['attributes'], list):
            return False
        
        return True
    except:
        return False


def validate_relation_data(data: Dict[str, Any]) -> bool:
    """
    验证关系数据是否符合模式
    
    Args:
        data: 待验证的关系数据
        
    Returns:
        验证通过返回True，否则返回False
    """
    try:
        # 检查必要字段
        required_fields = ['subject', 'relation', 'object']
        for field in required_fields:
            if field not in data:
                return False
        
        # 检查字段类型
        if not isinstance(data.get('subject', ''), str):
            return False
        
        if not isinstance(data.get('relation', ''), str):
            return False
        
        if not isinstance(data.get('object', ''), str):
            return False
        
        if 'confidence' in data and not isinstance(data['confidence'], (int, float)):
            return False
        
        # 检查列表字段
        if 'sources' in data and not isinstance(data['sources'], list):
            return False
        
        return True
    except:
        return False


def validate_feature_record(data: Dict[str, Any]) -> bool:
    """
    验证特征记录是否符合模式
    
    Args:
        data: 待验证的特征记录
        
    Returns:
        验证通过返回True，否则返回False
    """
    try:
        # 检查必要字段
        if 'feature' not in data:
            return False
        
        # 检查字段类型
        if not isinstance(data.get('feature', ''), str):
            return False
        
        if 'entity_id' in data and not isinstance(data['entity_id'], str):
            return False
        
        if 'confidence' in data and not isinstance(data['confidence'], (int, float)):
            return False
        
        # 检查列表字段
        if 'sources' in data and not isinstance(data['sources'], list):
            return False
        
        return True
    except:
        return False


def validate_attribute_record(data: Dict[str, Any]) -> bool:
    """
    验证属性记录是否符合模式
    
    Args:
        data: 待验证的属性记录
        
    Returns:
        验证通过返回True，否则返回False
    """
    try:
        # 检查必要字段
        required_fields = ['field', 'value']
        for field in required_fields:
            if field not in data:
                return False
        
        # 检查字段类型
        if not isinstance(data.get('field', ''), str):
            return False
        
        if 'entity' in data and not isinstance(data['entity'], str):
            return False
        
        if 'confidence' in data and not isinstance(data['confidence'], (int, float)):
            return False
        
        # 检查列表字段
        if 'sources' in data and not isinstance(data['sources'], list):
            return False
        
        return True
    except:
        return False


# ============================================================================
# 示例数据
# ============================================================================

EXAMPLE_ENTITY_DATA: EntityData = {
    "id": "ZQR",
    "uid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "type": "person",
    "confidence": 1.0,
    "sources": [
        {
            "dialogue_id": "dlg_2025-10-21_22-24-25",
            "episode_id": "ep_001",
            "generated_at": "2026-01-27T20:52:09.800086Z"
        }
    ],
    "features": [
        {
            "entity_id": "ZQR",
            "feature": "determined to succeed in exams",
            "scene_id": "scene_00001",
            "confidence": 0.9,
            "sources": [
                {
                    "dialogue_id": "dlg_2025-10-21_22-24-25",
                    "episode_id": "ep_001",
                    "generated_at": "2026-01-27T20:52:09.800086Z",
                    "scene_id": "scene_00001"
                }
            ]
        }
    ],
    "attributes": [
        {
            "entity": "ZQR",
            "field": "role",
            "value": "实习生",
            "confidence": 0.95,
            "sources": [
                {
                    "dialogue_id": "dlg_2025-11-17_19-04-53",
                    "episode_id": "ep_001",
                    "generated_at": "2026-01-27T20:52:17.431710Z"
                }
            ]
        }
    ]
}

EXAMPLE_RELATION_DATA: RelationRecord = {
    "id": "3bca4668-80d0-4542-827b-5fe7f1b76162",
    "subject": "ZQR",
    "relation": "works_at",
    "object": "启元实验室",
    "confidence": 0.95,
    "sources": [
        {
            "dialogue_id": "dlg_2025-11-17_19-04-53",
            "episode_id": "ep_001",
            "generated_at": "2026-01-27T20:52:17.431710Z"
        }
    ]
}