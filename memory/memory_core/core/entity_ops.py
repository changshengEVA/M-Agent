#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实体结构级算子

负责对"实体节点"进行结构性修改。
这些操作都会影响实体本身，且通常伴随关系/内容的联动变化。
"""

import logging
import uuid
from typing import Dict, Any, Optional

from .repo_context import RepoContext

logger = logging.getLogger(__name__)

# Core 接口统一返回格式
CoreResult = Dict[str, Any]


def merge_entities(
    target_id: str,
    source_id: str,
    repos: RepoContext,
    source_info: Optional[Dict[str, Any]] = None
) -> CoreResult:
    """
    合并两个实体
    
    在已确认两个实体指向同一现实对象的前提下，将 source_id 的所有信息合并进 target_id，
    并清理 source_id。
    
    Args:
        target_id: 目标实体 ID（保留）
        source_id: 被合并实体 ID（将被移除）
        repos: 持久化操作组件集合
        source_info: 来源信息（当前阶段不使用）
        
    Returns:
        CoreResult 结构:
        {
            "success": bool,
            "changed": bool,          # KG 是否发生结构变化
            "details": dict           # 包含合并详情
        }
    """
    logger.info(f"开始合并实体: {source_id} -> {target_id}")
    
    # 检查实体是否存在
    if not repos.entity.exists(source_id):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"源实体不存在: {source_id}",
                "operation": "merge_entities"
            }
        }
    
    if not repos.entity.exists(target_id):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"目标实体不存在: {target_id}",
                "operation": "merge_entities"
            }
        }
    
    # 检查是否合并到自身
    if target_id == source_id:
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": "不能将实体合并到自身",
                "operation": "merge_entities"
            }
        }
    
    try:
        # 1. 加载源实体数据
        success, source_data = repos.entity.load(source_id)
        if not success:
            return {
                "success": False,
                "changed": False,
                "details": {
                    "error": f"无法加载源实体: {source_id}",
                    "operation": "merge_entities"
                }
            }
        
        # 2. 加载目标实体数据
        success, target_data = repos.entity.load(target_id)
        if not success:
            return {
                "success": False,
                "changed": False,
                "details": {
                    "error": f"无法加载目标实体: {target_id}",
                    "operation": "merge_entities"
                }
            }
        
        # 3. 合并特征
        source_features = source_data.get('features', [])
        target_features = target_data.get('features', [])
        
        # 去重合并特征
        feature_map = {}
        for feature in target_features:
            feature_text = feature.get('feature', '')
            if feature_text:
                feature_map[feature_text] = feature
        
        for feature in source_features:
            feature_text = feature.get('feature', '')
            if feature_text:
                if feature_text not in feature_map:
                    feature_map[feature_text] = feature
                else:
                    # 合并来源信息
                    existing_feature = feature_map[feature_text]
                    existing_sources = existing_feature.get('sources', [])
                    new_sources = feature.get('sources', [])
                    
                    existing_keys = {
                        (
                            s.get('dialogue_id'),
                            s.get('episode_id'),
                            s.get('scene_id')
                        )
                        for s in existing_sources if isinstance(s, dict)
                    }
                    for source in new_sources:
                        if not isinstance(source, dict):
                            continue
                        key = (
                            source.get('dialogue_id'),
                            source.get('episode_id'),
                            source.get('scene_id')
                        )
                        if key not in existing_keys:
                            existing_sources.append(source)
                            existing_keys.add(key)
                    
                    existing_feature['sources'] = existing_sources
                    # 保留置信度更高的值
                    if feature.get('confidence', 0) > existing_feature.get('confidence', 0):
                        existing_feature['confidence'] = feature['confidence']
        
        target_data['features'] = list(feature_map.values())
        
        # 4. 合并属性
        source_attributes = source_data.get('attributes', [])
        target_attributes = target_data.get('attributes', [])
        
        # 按字段名合并属性
        attribute_map = {}
        for attr in target_attributes:
            field = attr.get('field', '')
            if field:
                attribute_map[field] = attr
        
        for attr in source_attributes:
            field = attr.get('field', '')
            if field:
                if field not in attribute_map:
                    if not isinstance(attr.get('values'), list):
                        attr['values'] = [attr.get('value')]
                    attribute_map[field] = attr
                else:
                    # 合并来源信息
                    existing_attr = attribute_map[field]
                    existing_sources = existing_attr.get('sources', [])
                    new_sources = attr.get('sources', [])
                    
                    existing_keys = {
                        (
                            s.get('dialogue_id'),
                            s.get('episode_id'),
                            s.get('scene_id')
                        )
                        for s in existing_sources if isinstance(s, dict)
                    }
                    for source in new_sources:
                        if not isinstance(source, dict):
                            continue
                        key = (
                            source.get('dialogue_id'),
                            source.get('episode_id'),
                            source.get('scene_id')
                        )
                        if key not in existing_keys:
                            existing_sources.append(source)
                            existing_keys.add(key)
                    
                    existing_attr['sources'] = existing_sources
                    values = existing_attr.get('values')
                    if not isinstance(values, list):
                        values = []
                        if 'value' in existing_attr:
                            values.append(existing_attr.get('value'))
                    if attr.get('value') not in values:
                        values.append(attr.get('value'))
                    existing_attr['values'] = values
                    # 保留置信度更高的值
                    if attr.get('confidence', 0) > existing_attr.get('confidence', 0):
                        existing_attr['value'] = attr['value']
                        existing_attr['confidence'] = attr['confidence']
        
        target_data['attributes'] = list(attribute_map.values())
        
        # 5. 合并来源信息
        target_sources = target_data.get('sources', [])
        source_sources = source_data.get('sources', [])
        
        target_source_keys = {
            (
                s.get('dialogue_id'),
                s.get('episode_id'),
                s.get('scene_id')
            )
            for s in target_sources if isinstance(s, dict)
        }
        for source in source_sources:
            if not isinstance(source, dict):
                continue
            key = (
                source.get('dialogue_id'),
                source.get('episode_id'),
                source.get('scene_id')
            )
            if key not in target_source_keys:
                target_sources.append(source)
                target_source_keys.add(key)
        
        target_data['sources'] = target_sources
        
        # 6. 保存合并后的目标实体
        if not repos.entity.save(target_data):
            return {
                "success": False,
                "changed": False,
                "details": {
                    "error": f"保存合并后的实体失败: {target_id}",
                    "operation": "merge_entities"
                }
            }
        
        # 7. 重定向关系
        relation_result = repos.relation.update_endpoint(source_id, target_id)
        
        # 8. 删除源实体
        if not repos.entity.delete(source_id):
            logger.warning(f"删除源实体失败: {source_id}")
        
        logger.info(f"实体合并完成: {source_id} -> {target_id}")
        
        return {
            "success": True,
            "changed": True,
            "details": {
                "operation": "merge_entities",
                "source_entity": source_id,
                "target_entity": target_id,
                "features_merged": len(source_features),
                "attributes_merged": len(source_attributes),
                "relation_redirect": relation_result
            }
        }
        
    except Exception as e:
        logger.error(f"合并实体失败 {source_id} -> {target_id}: {e}")
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": str(e),
                "operation": "merge_entities"
            }
        }


def delete_entity_and_edges(
    entity_id: str,
    repos: RepoContext,
    source_info: Optional[Dict[str, Any]] = None
) -> CoreResult:
    """
    删除实体及其所有相邻关系
    
    从 KG 中彻底删除指定实体，同时删除所有以该实体为端点的关系。
    
    Args:
        entity_id: 目标实体 ID
        repos: 持久化操作组件集合
        source_info: 来源信息（当前阶段不使用）
        
    Returns:
        CoreResult 结构
    """
    logger.info(f"开始删除实体及其关系: {entity_id}")
    
    # 检查实体是否存在
    if not repos.entity.exists(entity_id):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"实体不存在: {entity_id}",
                "operation": "delete_entity_and_edges"
            }
        }
    
    try:
        # 1. 查找所有相关关系
        outgoing_relations = repos.relation.find_by_subject(entity_id)
        incoming_relations = repos.relation.find_by_object(entity_id)
        all_relations = outgoing_relations + incoming_relations
        
        # 2. 删除所有相关关系
        deleted_relations = []
        for relation in all_relations:
            relation_id = relation.get('id')
            if relation_id:
                if repos.relation.delete(relation_id):
                    deleted_relations.append(relation_id)
        
        # 3. 删除实体
        if not repos.entity.delete(entity_id):
            return {
                "success": False,
                "changed": False,
                "details": {
                    "error": f"删除实体失败: {entity_id}",
                    "operation": "delete_entity_and_edges",
                    "deleted_relations": deleted_relations
                }
            }
        
        logger.info(f"实体删除完成: {entity_id}, 删除了 {len(deleted_relations)} 个关系")
        
        return {
            "success": True,
            "changed": True,
            "details": {
                "operation": "delete_entity_and_edges",
                "entity_id": entity_id,
                "outgoing_relations": len(outgoing_relations),
                "incoming_relations": len(incoming_relations),
                "total_relations": len(all_relations),
                "deleted_relations": deleted_relations
            }
        }
        
    except Exception as e:
        logger.error(f"删除实体及其关系失败 {entity_id}: {e}")
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": str(e),
                "operation": "delete_entity_and_edges"
            }
        }


def rename_entity(
    old_id: str,
    new_id: str,
    repos: RepoContext,
    source_info: Optional[Dict[str, Any]] = None
) -> CoreResult:
    """
    重命名实体
    
    将实体的标识符从 old_id 变更为 new_id，并同步更新所有相关关系。
    
    Args:
        old_id: 原实体 ID
        new_id: 新实体 ID
        repos: 持久化操作组件集合
        source_info: 来源信息（当前阶段不使用）
        
    Returns:
        CoreResult 结构
    """
    logger.info(f"开始重命名实体: {old_id} -> {new_id}")
    
    # 检查原实体是否存在
    if not repos.entity.exists(old_id):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"原实体不存在: {old_id}",
                "operation": "rename_entity"
            }
        }
    
    # 检查新实体是否已存在
    if repos.entity.exists(new_id):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"新实体已存在: {new_id}",
                "operation": "rename_entity"
            }
        }
    
    # 检查是否重命名为自身
    if old_id == new_id:
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": "新旧实体ID相同",
                "operation": "rename_entity"
            }
        }
    
    try:
        # 1. 加载原实体数据
        success, entity_data = repos.entity.load(old_id)
        if not success:
            return {
                "success": False,
                "changed": False,
                "details": {
                    "error": f"无法加载实体: {old_id}",
                    "operation": "rename_entity"
                }
            }
        
        # 2. 更新实体ID
        entity_data['id'] = new_id
        
        # 3. 更新特征中的entity_id字段
        for feature in entity_data.get('features', []):
            feature['entity_id'] = new_id
        
        # 4. 更新属性中的entity字段
        for attribute in entity_data.get('attributes', []):
            attribute['entity'] = new_id
        
        # 5. 保存为新实体
        if not repos.entity.save(entity_data):
            return {
                "success": False,
                "changed": False,
                "details": {
                    "error": f"保存新实体失败: {new_id}",
                    "operation": "rename_entity"
                }
            }
        
        # 6. 重定向关系
        relation_result = repos.relation.update_endpoint(old_id, new_id)
        
        # 7. 删除原实体
        if not repos.entity.delete(old_id):
            logger.warning(f"删除原实体失败: {old_id}")
        
        logger.info(f"实体重命名完成: {old_id} -> {new_id}")
        
        return {
            "success": True,
            "changed": True,
            "details": {
                "operation": "rename_entity",
                "old_id": old_id,
                "new_id": new_id,
                "relation_redirect": relation_result
            }
        }
        
    except Exception as e:
        logger.error(f"重命名实体失败 {old_id} -> {new_id}: {e}")
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": str(e),
                "operation": "rename_entity"
            }
        }


def add_entity(
    entity_id: str,
    repos: RepoContext,
    entity_type: Optional[str] = None,
    source_info: Optional[Dict[str, Any]] = None
) -> CoreResult:
    """
    创建实体
    
    在 KG 中创建一个新的实体节点，初始化为最小合法结构（不包含任何关系、特征或属性）。
    
    Args:
        entity_id: 新实体 ID
        entity_type: 实体类型（可选）
        repos: 持久化操作组件集合
        source_info: 来源信息（当前阶段不使用）
        
    Returns:
        CoreResult 结构
    """
    logger.info(f"开始创建实体: {entity_id}")
    
    # 检查实体是否已存在
    if repos.entity.exists(entity_id):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"实体已存在: {entity_id}",
                "operation": "add_entity"
            }
        }
    
    try:
        # 生成唯一UID
        entity_uid = str(uuid.uuid4())
        
        # 创建最小合法实体结构
        entity_data = {
            "id": entity_id,
            "uid": entity_uid,
            "sources": [],
            "features": [],
            "attributes": []
        }
        
        # 添加实体类型（如果提供）
        if entity_type:
            entity_data['type'] = entity_type
        
        # 添加来源信息（如果提供）
        if source_info:
            entity_data['sources'] = [source_info]
        
        # 保存实体
        if repos.entity.save(entity_data):
            logger.info(f"实体创建成功: {entity_id}, UID: {entity_uid}")
            return {
                "success": True,
                "changed": True,
                "details": {
                    "operation": "add_entity",
                    "entity_id": entity_id,
                    "entity_uid": entity_uid,
                    "entity_type": entity_type,
                    "has_features": False,
                    "has_attributes": False
                }
            }
        else:
            return {
                "success": False,
                "changed": False,
                "details": {
                    "error": f"保存实体失败: {entity_id}",
                    "operation": "add_entity"
                }
            }
        
    except Exception as e:
        logger.error(f"创建实体失败 {entity_id}: {e}")
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": str(e),
                "operation": "add_entity"
            }
        }
