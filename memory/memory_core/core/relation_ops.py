#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
关系结构级算子

负责对"关系边"进行确定性结构修改。
不涉及关系是否合理，仅处理结构层面的变化。
"""

import logging
from typing import Dict, Any, Optional, List

from .repo_context import RepoContext

logger = logging.getLogger(__name__)

# Core 接口统一返回格式
CoreResult = Dict[str, Any]


def redirect_relations(
    old_entity_id: str,
    new_entity_id: str,
    repos: RepoContext,
    source_info: Optional[Dict[str, Any]] = None
) -> CoreResult:
    """
    关系端点重定向
    
    将所有指向 old_entity_id 的关系端点重定向至 new_entity_id。
    
    Args:
        old_entity_id: 原实体 ID
        new_entity_id: 新实体 ID
        repos: 持久化操作组件集合
        source_info: 来源信息（当前阶段不使用）
        
    Returns:
        CoreResult 结构:
        {
            "success": bool,
            "changed": bool,          # KG 是否发生结构变化
            "details": dict           # 包含重定向详情
        }
    """
    logger.info(f"开始重定向关系端点: {old_entity_id} -> {new_entity_id}")
    
    # 检查原实体是否存在
    if not repos.entity.exists(old_entity_id):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"原实体不存在: {old_entity_id}",
                "operation": "redirect_relations"
            }
        }
    
    # 检查新实体是否存在
    if not repos.entity.exists(new_entity_id):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"新实体不存在: {new_entity_id}",
                "operation": "redirect_relations"
            }
        }
    
    # 检查是否重定向到自身
    if old_entity_id == new_entity_id:
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": "新旧实体ID相同",
                "operation": "redirect_relations"
            }
        }
    
    try:
        # 使用关系仓库的 update_endpoint 方法
        result = repos.relation.update_endpoint(old_entity_id, new_entity_id)
        
        if result.get("success", False):
            changed = result.get("updated_count", 0) > 0 or result.get("deleted_count", 0) > 0
            
            logger.info(f"关系端点重定向完成: {old_entity_id} -> {new_entity_id}")
            logger.info(f"更新了 {result.get('updated_count', 0)} 个关系，删除了 {result.get('deleted_count', 0)} 个关系")
            
            return {
                "success": True,
                "changed": changed,
                "details": {
                    "operation": "redirect_relations",
                    "old_entity_id": old_entity_id,
                    "new_entity_id": new_entity_id,
                    "updated_count": result.get("updated_count", 0),
                    "deleted_count": result.get("deleted_count", 0),
                    "updated_relations": result.get("updated_relations", []),
                    "deleted_relations": result.get("deleted_relations", []),
                    "message": result.get("message", "")
                }
            }
        else:
            return {
                "success": False,
                "changed": False,
                "details": {
                    "error": "关系端点更新失败",
                    "operation": "redirect_relations",
                    "old_entity_id": old_entity_id,
                    "new_entity_id": new_entity_id
                }
            }
        
    except Exception as e:
        logger.error(f"重定向关系端点失败 {old_entity_id} -> {new_entity_id}: {e}")
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": str(e),
                "operation": "redirect_relations"
            }
        }


def delete_relations_of_entity(
    entity_id: str,
    repos: RepoContext,
    source_info: Optional[Dict[str, Any]] = None
) -> CoreResult:
    """
    删除与某实体相关的所有关系
    
    删除所有以该实体作为 subject 或 object 的关系。
    
    Args:
        entity_id: 目标实体 ID
        repos: 持久化操作组件集合
        source_info: 来源信息（当前阶段不使用）
        
    Returns:
        CoreResult 结构
    """
    logger.info(f"开始删除与实体相关的所有关系: {entity_id}")
    
    # 检查实体是否存在
    if not repos.entity.exists(entity_id):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"实体不存在: {entity_id}",
                "operation": "delete_relations_of_entity"
            }
        }
    
    try:
        # 1. 查找所有相关关系
        outgoing_relations = repos.relation.find_by_subject(entity_id)
        incoming_relations = repos.relation.find_by_object(entity_id)
        all_relations = outgoing_relations + incoming_relations
        
        if not all_relations:
            logger.info(f"实体 {entity_id} 没有相关关系")
            return {
                "success": True,
                "changed": False,
                "details": {
                    "operation": "delete_relations_of_entity",
                    "entity_id": entity_id,
                    "message": "实体没有相关关系",
                    "outgoing_relations": 0,
                    "incoming_relations": 0,
                    "total_relations": 0,
                    "deleted_relations": []
                }
            }
        
        # 2. 删除所有相关关系
        deleted_relations = []
        failed_deletions = []
        
        for relation in all_relations:
            relation_id = relation.get('id')
            if relation_id:
                if repos.relation.delete(relation_id):
                    deleted_relations.append({
                        "relation_id": relation_id,
                        "subject": relation.get('subject'),
                        "relation": relation.get('relation'),
                        "object": relation.get('object')
                    })
                else:
                    failed_deletions.append(relation_id)
        
        # 3. 检查删除结果
        if failed_deletions:
            logger.warning(f"部分关系删除失败: {failed_deletions}")
        
        changed = len(deleted_relations) > 0
        
        logger.info(f"删除实体关系完成: {entity_id}, 成功删除了 {len(deleted_relations)} 个关系")
        
        return {
            "success": True,
            "changed": changed,
            "details": {
                "operation": "delete_relations_of_entity",
                "entity_id": entity_id,
                "outgoing_relations": len(outgoing_relations),
                "incoming_relations": len(incoming_relations),
                "total_relations": len(all_relations),
                "deleted_count": len(deleted_relations),
                "failed_count": len(failed_deletions),
                "deleted_relations": deleted_relations,
                "failed_relations": failed_deletions
            }
        }
        
    except Exception as e:
        logger.error(f"删除实体关系失败 {entity_id}: {e}")
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": str(e),
                "operation": "delete_relations_of_entity"
            }
        }


def add_relation(
    subject: str,
    relation: str,
    object: str,
    repos: RepoContext,
    confidence: float = 1.0,
    source_info: Optional[Dict[str, Any]] = None
) -> CoreResult:
    """
    添加一条关系
    
    在 KG 中创建一条新的关系边，连接两个实体。
    
    Args:
        subject: 主语实体 ID
        relation: 关系类型
        object: 宾语实体 ID
        repos: 持久化操作组件集合
        confidence: 关系置信度，默认 1.0
        source_info: 来源信息（可选）
        
    Returns:
        CoreResult 结构:
        {
            "success": bool,
            "changed": bool,          # KG 是否发生结构变化
            "details": dict           # 包含关系详情
        }
    """
    logger.info(f"开始添加关系: {subject} -[{relation}]-> {object}")
    
    # 检查主语实体是否存在
    if not repos.entity.exists(subject):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"主语实体不存在: {subject}",
                "operation": "add_relation"
            }
        }
    
    # 检查宾语实体是否存在
    if not repos.entity.exists(object):
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": f"宾语实体不存在: {object}",
                "operation": "add_relation"
            }
        }
    
    # 检查是否指向自身
    if subject == object:
        logger.warning(f"关系主语和宾语相同: {subject}")
        # 允许自环关系，但记录警告
    try:
        # 检查是否已存在相同的关系（同样的主谓宾）
        existing_relations = repos.relation.find_by_subject(subject)
        duplicate_relation = None
        for rel in existing_relations:
            if rel.get('relation') == relation and rel.get('object') == object:
                duplicate_relation = rel
                break
        
        if duplicate_relation:
            # 关系已存在，不重复添加
            logger.info(f"关系已存在，跳过重复添加: {subject} -[{relation}]-> {object}")
            
            # 可选：合并来源信息和更新置信度
            # 这里可以添加合并逻辑，但为了简单起见，我们只返回成功但 changed=False
            
            return {
                "success": True,
                "changed": False,
                "details": {
                    "operation": "add_relation",
                    "subject": subject,
                    "relation": relation,
                    "object": object,
                    "confidence": confidence,
                    "existing_relation_id": duplicate_relation.get("id", "unknown"),
                    "message": "关系已存在，未重复添加"
                }
            }
        
        # 构建关系记录
        relation_record: Dict[str, Any] = {
            "subject": subject,
            "relation": relation,
            "object": object,
            "confidence": confidence
        }
        
        # 添加来源信息
        if source_info:
            relation_record["sources"] = [source_info]
        
        # 保存关系
        success = repos.relation.save(relation_record)
        
        if success:
            logger.info(f"关系添加成功: {subject} -[{relation}]-> {object}")
            return {
                "success": True,
                "changed": True,
                "details": {
                    "operation": "add_relation",
                    "subject": subject,
                    "relation": relation,
                    "object": object,
                    "confidence": confidence,
                    "relation_id": relation_record.get("id", "unknown")
                }
            }
        else:
            logger.warning(f"关系保存失败: {subject} -[{relation}]-> {object}")
            return {
                "success": False,
                "changed": False,
                "details": {
                    "error": "关系仓库保存失败",
                    "operation": "add_relation",
                    "subject": subject,
                    "relation": relation,
                    "object": object
                }
            }
            
            
    except Exception as e:
        logger.error(f"添加关系失败 {subject} -[{relation}]-> {object}: {e}")
        return {
            "success": False,
            "changed": False,
            "details": {
                "error": str(e),
                "operation": "add_relation"
            }
        }


def _get_entity_relations(
    entity_id: str,
    repos: RepoContext
) -> Dict[str, List[Dict[str, Any]]]:
    """
    获取实体的所有关系（内部辅助函数）
    
    Args:
        entity_id: 实体ID
        repos: 持久化操作组件集合
        
    Returns:
        包含出边和入边关系的字典
    """
    outgoing = repos.relation.find_by_subject(entity_id)
    incoming = repos.relation.find_by_object(entity_id)
    
    return {
        "outgoing": outgoing,
        "incoming": incoming,
        "all": outgoing + incoming
    }