#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一致性与完整性维护

用于保证 KG 在结构层面处于合法状态。
"""

import logging
from typing import Dict, Any, List

from .repo_context import RepoContext

logger = logging.getLogger(__name__)

# Core 接口统一返回格式
CoreResult = Dict[str, Any]


def remove_dangling_relations(
    repos: RepoContext
) -> CoreResult:
    """
    清理悬挂关系
    
    删除指向不存在实体的关系，保证图结构完整性。
    
    Args:
        repos: 持久化操作组件集合
        
    Returns:
        CoreResult 结构:
        {
            "success": bool,
            "changed": bool,          # KG 是否发生结构变化
            "details": dict           # 包含清理详情
        }
    """
    logger.info("开始清理悬挂关系")
    
    try:
        # 1. 获取所有关系
        all_relations = repos.relation.list_all()
        
        if not all_relations:
            logger.info("没有关系需要检查")
            return {
                "success": True,
                "changed": False,
                "details": {
                    "operation": "remove_dangling_relations",
                    "total_relations": 0,
                    "dangling_relations": 0,
                    "removed_relations": [],
                    "message": "没有关系需要检查"
                }
            }
        
        # 2. 检查每个关系
        dangling_relations = []
        removed_relations = []
        
        for relation in all_relations:
            subject = relation.get('subject')
            obj = relation.get('object')
            relation_id = relation.get('id')
            
            # 检查主语和宾语实体是否存在
            subject_exists = repos.entity.exists(subject) if subject else False
            object_exists = repos.entity.exists(obj) if obj else False
            
            if not subject_exists or not object_exists:
                dangling_relations.append({
                    "relation_id": relation_id,
                    "subject": subject,
                    "subject_exists": subject_exists,
                    "object": obj,
                    "object_exists": object_exists,
                    "relation_type": relation.get('relation')
                })
        
        # 3. 删除悬挂关系
        for dangling in dangling_relations:
            relation_id = dangling.get("relation_id")
            if relation_id:
                if repos.relation.delete(relation_id):
                    removed_relations.append(dangling)
                    logger.info(f"删除悬挂关系: {relation_id}")
        
        changed = len(removed_relations) > 0
        
        logger.info(f"悬挂关系清理完成: 检查了 {len(all_relations)} 个关系，发现 {len(dangling_relations)} 个悬挂关系，删除了 {len(removed_relations)} 个")
        
        return {
            "success": True,
            "changed": changed,
            "details": {
                "operation": "remove_dangling_relations",
                "total_relations": len(all_relations),
                "dangling_relations_found": len(dangling_relations),
                "dangling_relations_removed": len(removed_relations),
                "dangling_relations": dangling_relations,
                "removed_relations": removed_relations
            }
        }
        
    except Exception as e:
        logger.error(f"清理悬挂关系失败: {e}")
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": str(e),
                "operation": "remove_dangling_relations"
            }
        }


def assert_entity_exists(
    entity_id: str,
    repos: RepoContext
) -> CoreResult:
    """
    检查实体是否存在
    
    检查指定实体是否存在于 KG 中，不存在则返回失败结果。
    
    Args:
        entity_id: 实体ID
        repos: 持久化操作组件集合
        
    Returns:
        CoreResult 结构
    """
    logger.debug(f"检查实体是否存在: {entity_id}")
    
    try:
        exists = repos.entity.exists(entity_id)
        
        if exists:
            logger.debug(f"实体存在: {entity_id}")
            return {
                "success": True,
                "changed": False,
                "details": {
                    "operation": "assert_entity_exists",
                    "entity_id": entity_id,
                    "exists": True,
                    "message": f"实体存在: {entity_id}"
                }
            }
        else:
            logger.debug(f"实体不存在: {entity_id}")
            return {
                "success": False,
                "changed": False,
                "details": {
                    "operation": "assert_entity_exists",
                    "entity_id": entity_id,
                    "exists": False,
                    "error": f"实体不存在: {entity_id}"
                }
            }
        
    except Exception as e:
        logger.error(f"检查实体存在性失败 {entity_id}: {e}")
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": str(e),
                "operation": "assert_entity_exists"
            }
        }


def validate_kg_integrity(
    repos: RepoContext
) -> CoreResult:
    """
    验证知识图谱完整性（扩展功能）
    
    检查 KG 的完整性，包括：
    1. 悬挂关系
    2. 孤立实体（没有关系的实体）
    3. 数据格式验证
    
    Args:
        repos: 持久化操作组件集合
        
    Returns:
        CoreResult 结构
    """
    logger.info("开始验证知识图谱完整性")
    
    try:
        # 1. 获取所有实体和关系
        all_entities = repos.entity.list_ids()
        all_relations = repos.relation.list_all()
        
        # 2. 检查悬挂关系
        dangling_relations = []
        for relation in all_relations:
            subject = relation.get('subject')
            obj = relation.get('object')
            
            if subject not in all_entities or obj not in all_entities:
                dangling_relations.append({
                    "relation_id": relation.get('id'),
                    "subject": subject,
                    "object": obj,
                    "relation_type": relation.get('relation')
                })
        
        # 3. 检查孤立实体
        connected_entities = set()
        for relation in all_relations:
            connected_entities.add(relation.get('subject'))
            connected_entities.add(relation.get('object'))
        
        isolated_entities = []
        for entity_id in all_entities:
            if entity_id not in connected_entities:
                isolated_entities.append(entity_id)
        
        # 4. 数据格式验证
        invalid_entities = []
        for entity_id in all_entities:
            success, entity_data = repos.entity.load(entity_id)
            if not success:
                invalid_entities.append({
                    "entity_id": entity_id,
                    "error": "无法加载实体数据"
                })
        
        # 5. 汇总结果
        has_issues = (len(dangling_relations) > 0 or 
                     len(isolated_entities) > 0 or 
                     len(invalid_entities) > 0)
        
        logger.info(f"知识图谱完整性验证完成: {len(all_entities)} 个实体, {len(all_relations)} 个关系")
        logger.info(f"发现问题: {len(dangling_relations)} 个悬挂关系, {len(isolated_entities)} 个孤立实体, {len(invalid_entities)} 个无效实体")
        
        return {
            "success": True,
            "changed": False,
            "details": {
                "operation": "validate_kg_integrity",
                "total_entities": len(all_entities),
                "total_relations": len(all_relations),
                "dangling_relations": dangling_relations,
                "isolated_entities": isolated_entities,
                "invalid_entities": invalid_entities,
                "has_issues": has_issues,
                "summary": {
                    "dangling_relations_count": len(dangling_relations),
                    "isolated_entities_count": len(isolated_entities),
                    "invalid_entities_count": len(invalid_entities)
                }
            }
        }
        
    except Exception as e:
        logger.error(f"验证知识图谱完整性失败: {e}")
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": str(e),
                "operation": "validate_kg_integrity"
            }
        }