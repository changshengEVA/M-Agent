#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实体内容级算子

负责对实体内部内容（特征 / 属性）进行结构性操作。
不判断内容是否正确或可信。
"""

import logging
from typing import Dict, Any, Optional, List

from .repo_context import RepoContext

logger = logging.getLogger(__name__)

# Core 接口统一返回格式
CoreResult = Dict[str, Any]


def append_feature(
    entity_id: str,
    feature_record: Dict[str, Any],
    repos: RepoContext,
    source_info: Optional[Dict[str, Any]] = None
) -> CoreResult:
    """
    向实体追加一条特征
    
    Args:
        entity_id: 目标实体 ID
        feature_record: 特征记录
        repos: 持久化操作组件集合
        source_info: 来源信息（当前阶段不使用）
        
    Returns:
        CoreResult 结构:
        {
            "success": bool,
            "changed": bool,          # KG 是否发生结构变化
            "details": dict           # 包含追加详情
        }
    """
    logger.info(f"开始向实体追加特征: {entity_id}")
    
    # 检查实体是否存在
    if not repos.entity.exists(entity_id):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"实体不存在: {entity_id}",
                "operation": "append_feature"
            }
        }
    
    # 检查特征记录是否包含必要字段
    if 'feature' not in feature_record:
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": "特征记录缺少'feature'字段",
                "operation": "append_feature"
            }
        }
    
    try:
        # 使用特征仓库的 append 方法
        result = repos.feature.append(entity_id, feature_record)
        
        if result.get("success", False):
            changed = result.get("action") in ["added", "updated"]
            
            logger.info(f"特征追加完成: {entity_id} - {feature_record.get('feature', 'unknown')}")
            
            return {
                "success": True,
                "changed": changed,
                "details": {
                    "operation": "append_feature",
                    "entity_id": entity_id,
                    "feature": feature_record.get('feature'),
                    "action": result.get("action", "unknown"),
                    "message": result.get("message", "")
                }
            }
        else:
            return {
                "success": False,
                "changed": False,
                "details": {
                    "error": result.get("message", "特征追加失败"),
                    "operation": "append_feature",
                    "entity_id": entity_id
                }
            }
        
    except Exception as e:
        logger.error(f"向实体追加特征失败 {entity_id}: {e}")
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": str(e),
                "operation": "append_feature"
            }
        }


def append_attribute(
    entity_id: str,
    attribute_record: Dict[str, Any],
    repos: RepoContext,
    source_info: Optional[Dict[str, Any]] = None
) -> CoreResult:
    """
    向实体追加一条属性
    
    Args:
        entity_id: 目标实体 ID
        attribute_record: 属性记录
        repos: 持久化操作组件集合
        source_info: 来源信息（当前阶段不使用）
        
    Returns:
        CoreResult 结构
    """
    logger.info(f"开始向实体追加属性: {entity_id}")
    
    # 检查实体是否存在
    if not repos.entity.exists(entity_id):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"实体不存在: {entity_id}",
                "operation": "append_attribute"
            }
        }
    
    # 检查属性记录是否包含必要字段
    if 'field' not in attribute_record or 'value' not in attribute_record:
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": "属性记录缺少'field'或'value'字段",
                "operation": "append_attribute"
            }
        }
    
    try:
        # 使用属性仓库的 set 方法
        result = repos.attribute.set(entity_id, attribute_record)
        
        if result.get("success", False):
            changed = result.get("action") in ["added", "updated"]
            
            logger.info(f"属性追加完成: {entity_id} - {attribute_record.get('field', 'unknown')}")
            
            return {
                "success": True,
                "changed": changed,
                "details": {
                    "operation": "append_attribute",
                    "entity_id": entity_id,
                    "field": attribute_record.get('field'),
                    "value": attribute_record.get('value'),
                    "action": result.get("action", "unknown"),
                    "message": result.get("message", "")
                }
            }
        else:
            return {
                "success": False,
                "changed": False,
                "details": {
                    "error": result.get("message", "属性追加失败"),
                    "operation": "append_attribute",
                    "entity_id": entity_id
                }
            }
        
    except Exception as e:
        logger.error(f"向实体追加属性失败 {entity_id}: {e}")
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": str(e),
                "operation": "append_attribute"
            }
        }


def move_features(
    from_entity: str,
    to_entity: str,
    repos: RepoContext,
    source_info: Optional[Dict[str, Any]] = None
) -> CoreResult:
    """
    迁移实体特征
    
    将源实体的特征迁移至目标实体，通常作为合并或重构操作的子步骤。
    
    Args:
        from_entity: 源实体 ID
        to_entity: 目标实体 ID
        repos: 持久化操作组件集合
        source_info: 来源信息（当前阶段不使用）
        
    Returns:
        CoreResult 结构
    """
    logger.info(f"开始迁移实体特征: {from_entity} -> {to_entity}")
    
    # 检查源实体是否存在
    if not repos.entity.exists(from_entity):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"源实体不存在: {from_entity}",
                "operation": "move_features"
            }
        }
    
    # 检查目标实体是否存在
    if not repos.entity.exists(to_entity):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"目标实体不存在: {to_entity}",
                "operation": "move_features"
            }
        }
    
    # 检查是否迁移到自身
    if from_entity == to_entity:
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": "源实体和目标实体相同",
                "operation": "move_features"
            }
        }
    
    try:
        # 1. 获取源实体的所有特征
        source_features = repos.feature.list(from_entity)
        
        if not source_features:
            logger.info(f"源实体 {from_entity} 没有特征可迁移")
            return {
                "success": True,
                "changed": False,
                "details": {
                    "operation": "move_features",
                    "from_entity": from_entity,
                    "to_entity": to_entity,
                    "moved_count": 0,
                    "message": "源实体没有特征可迁移"
                }
            }
        
        # 2. 迁移每个特征
        moved_count = 0
        failed_count = 0
        moved_features = []
        failed_features = []
        
        for feature in source_features:
            # 更新特征中的 entity_id 字段
            feature['entity_id'] = to_entity
            
            # 追加到目标实体
            result = repos.feature.append(to_entity, feature)
            
            if result.get("success", False):
                moved_count += 1
                moved_features.append(feature.get('feature', 'unknown'))
            else:
                failed_count += 1
                failed_features.append(feature.get('feature', 'unknown'))
        
        # 3. 如果所有特征都成功迁移，可以删除源实体的特征
        # 注意：这里不删除源实体本身，只迁移特征
        
        changed = moved_count > 0
        
        logger.info(f"特征迁移完成: {from_entity} -> {to_entity}, 成功迁移 {moved_count} 个特征")
        
        return {
            "success": True,
            "changed": changed,
            "details": {
                "operation": "move_features",
                "from_entity": from_entity,
                "to_entity": to_entity,
                "total_features": len(source_features),
                "moved_count": moved_count,
                "failed_count": failed_count,
                "moved_features": moved_features,
                "failed_features": failed_features
            }
        }
        
    except Exception as e:
        logger.error(f"迁移实体特征失败 {from_entity} -> {to_entity}: {e}")
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": str(e),
                "operation": "move_features"
            }
        }


def move_attributes(
    from_entity: str,
    to_entity: str,
    repos: RepoContext,
    source_info: Optional[Dict[str, Any]] = None
) -> CoreResult:
    """
    迁移实体属性
    
    将源实体的属性迁移至目标实体，通常作为合并或重构操作的子步骤。
    
    Args:
        from_entity: 源实体 ID
        to_entity: 目标实体 ID
        repos: 持久化操作组件集合
        source_info: 来源信息（当前阶段不使用）
        
    Returns:
        CoreResult 结构
    """
    logger.info(f"开始迁移实体属性: {from_entity} -> {to_entity}")
    
    # 检查源实体是否存在
    if not repos.entity.exists(from_entity):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"源实体不存在: {from_entity}",
                "operation": "move_attributes"
            }
        }
    
    # 检查目标实体是否存在
    if not repos.entity.exists(to_entity):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"目标实体不存在: {to_entity}",
                "operation": "move_attributes"
            }
        }
    
    # 检查是否迁移到自身
    if from_entity == to_entity:
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": "源实体和目标实体相同",
                "operation": "move_attributes"
            }
        }
    
    try:
        # 1. 获取源实体的所有属性
        source_attributes = repos.attribute.list(from_entity)
        
        if not source_attributes:
            logger.info(f"源实体 {from_entity} 没有属性可迁移")
            return {
                "success": True,
                "changed": False,
                "details": {
                    "operation": "move_attributes",
                    "from_entity": from_entity,
                    "to_entity": to_entity,
                    "moved_count": 0,
                    "message": "源实体没有属性可迁移"
                }
            }
        
        # 2. 迁移每个属性
        moved_count = 0
        failed_count = 0
        moved_attributes = []
        failed_attributes = []
        
        for attribute in source_attributes:
            # 更新属性中的 entity 字段
            attribute['entity'] = to_entity
            
            # 追加到目标实体
            result = repos.attribute.set(to_entity, attribute)
            
            if result.get("success", False):
                moved_count += 1
                moved_attributes.append(attribute.get('field', 'unknown'))
            else:
                failed_count += 1
                failed_attributes.append(attribute.get('field', 'unknown'))
        
        changed = moved_count > 0
        
        logger.info(f"属性迁移完成: {from_entity} -> {to_entity}, 成功迁移 {moved_count} 个属性")
        
        return {
            "success": True,
            "changed": changed,
            "details": {
                "operation": "move_attributes",
                "from_entity": from_entity,
                "to_entity": to_entity,
                "total_attributes": len(source_attributes),
                "moved_count": moved_count,
                "failed_count": failed_count,
                "moved_attributes": moved_attributes,
                "failed_attributes": failed_attributes
            }
        }
        
    except Exception as e:
        logger.error(f"迁移实体属性失败 {from_entity} -> {to_entity}: {e}")
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": str(e),
                "operation": "move_attributes"
            }
        }