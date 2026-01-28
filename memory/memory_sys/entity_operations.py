#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实体操作工具类

负责实体的合并、删除、修改等操作
"""

import json
import logging
from typing import Dict, List, Any, Optional, Union
from pathlib import Path

logger = logging.getLogger(__name__)


class EntityOperations:
    """实体操作管理类"""
    
    def __init__(self, entity_storage, relation_storage, feature_attribute_storage, source_manager):
        """
        初始化实体操作管理器
        
        Args:
            entity_storage: EntityStorage实例
            relation_storage: RelationStorage实例
            feature_attribute_storage: FeatureAttributeStorage实例
            source_manager: SourceManager实例
        """
        self.entity_storage = entity_storage
        self.relation_storage = relation_storage
        self.feature_attribute_storage = feature_attribute_storage
        self.source_manager = source_manager
        
        logger.info("初始化实体操作管理器")
    
    def combine_entities(self, entity_a_id: str, entity_b_id: str) -> Dict:
        """
        合并两个实体，将实体B的属性、关系、特征添加到实体A上
        
        Args:
            entity_a_id: 目标实体ID（合并到该实体）
            entity_b_id: 源实体ID（从该实体合并数据）
            
        Returns:
            包含合并结果的字典
        """
        try:
            logger.info(f"开始合并实体: {entity_b_id} -> {entity_a_id}")
            
            # 检查实体A和B是否相同
            if entity_a_id == entity_b_id:
                return {
                    "success": False,
                    "message": "不能合并相同的实体",
                    "entity_a": entity_a_id,
                    "entity_b": entity_b_id
                }
            
            # 检查实体文件是否存在
            if not self.entity_storage.entity_exists(entity_a_id):
                return {
                    "success": False,
                    "message": f"实体A不存在: {entity_a_id}",
                    "entity_a": entity_a_id,
                    "entity_b": entity_b_id
                }
            
            if not self.entity_storage.entity_exists(entity_b_id):
                return {
                    "success": False,
                    "message": f"实体B不存在: {entity_b_id}",
                    "entity_a": entity_a_id,
                    "entity_b": entity_b_id
                }
            
            # 加载实体数据
            entity_a_data = self.entity_storage.load_entity(entity_a_id)
            entity_b_data = self.entity_storage.load_entity(entity_b_id)
            
            if not entity_a_data or not entity_b_data:
                return {
                    "success": False,
                    "message": "加载实体数据失败",
                    "entity_a": entity_a_id,
                    "entity_b": entity_b_id
                }
            
            # 确保实体ID正确
            entity_a_data['id'] = entity_a_id
            entity_b_data['id'] = entity_b_id
            
            # 统计信息
            stats = {
                "features_added": 0,
                "features_merged": 0,
                "attributes_added": 0,
                "attributes_merged": 0,
                "sources_added": 0,
                "relations_updated": 0,
                "relations_deleted": 0,
                "relations_merged": 0
            }
            
            # 1. 合并基本字段（如果A中不存在或B的置信度更高）
            if 'type' in entity_b_data and ('type' not in entity_a_data or
                                          entity_b_data.get('confidence', 0) > entity_a_data.get('confidence', 0)):
                entity_a_data['type'] = entity_b_data['type']
            
            if 'confidence' in entity_b_data:
                entity_a_confidence = entity_a_data.get('confidence')
                entity_b_confidence = entity_b_data.get('confidence')
                if entity_b_confidence is not None and (entity_a_confidence is None or entity_b_confidence > entity_a_confidence):
                    entity_a_data['confidence'] = entity_b_confidence
            
            # 2. 合并来源信息
            sources_a = entity_a_data.get('sources', [])
            sources_b = entity_b_data.get('sources', [])
            merged_sources = self.source_manager.merge_sources(sources_a, sources_b)
            stats['sources_added'] = len(merged_sources) - len(sources_a)
            entity_a_data['sources'] = merged_sources
            
            # 3. 合并特征
            features_a = entity_a_data.get('features', [])
            features_b = entity_b_data.get('features', [])
            merged_features = self.feature_attribute_storage.merge_features(features_a, features_b, entity_a_id)
            stats['features_added'] = len(merged_features) - len(features_a)
            stats['features_merged'] = len(features_a) + len(features_b) - len(merged_features)
            entity_a_data['features'] = merged_features
            
            # 4. 合并属性
            attributes_a = entity_a_data.get('attributes', [])
            attributes_b = entity_b_data.get('attributes', [])
            merged_attributes = self.feature_attribute_storage.merge_attributes(attributes_a, attributes_b, entity_a_id)
            stats['attributes_added'] = len(merged_attributes) - len(attributes_a)
            stats['attributes_merged'] = len(attributes_a) + len(attributes_b) - len(merged_attributes)
            entity_a_data['attributes'] = merged_attributes
            
            # 5. 更新关系文件
            updated_relations, deleted_relations = self.relation_storage.update_relation_entities(entity_b_id, entity_a_id)
            stats['relations_updated'] = len(updated_relations)
            stats['relations_deleted'] = len(deleted_relations)
            stats['relations_merged'] = 0  # 在update_relation_entities中已经处理了合并
            
            # 6. 保存更新后的实体A文件
            self.entity_storage.save_entity(entity_a_data)
            
            # 7. 删除实体B文件
            self.entity_storage.delete_entity(entity_b_id)
            
            logger.info(f"实体合并完成: {entity_b_id} -> {entity_a_id}")
            logger.info(f"合并统计: 特征添加{stats['features_added']}个, 合并{stats['features_merged']}个; "
                       f"属性添加{stats['attributes_added']}个, 合并{stats['attributes_merged']}个; "
                       f"来源添加{stats['sources_added']}个; 关系更新{stats['relations_updated']}个, "
                       f"删除{stats['relations_deleted']}个")
            
            return {
                "success": True,
                "message": f"成功合并实体 {entity_b_id} 到 {entity_a_id}",
                "entity_a": entity_a_id,
                "entity_b": entity_b_id,
                "stats": stats,
                "updated_relations": updated_relations[:10],  # 只返回前10个
                "deleted_relations": deleted_relations[:10]   # 只返回前10个
            }
            
        except Exception as e:
            logger.error(f"合并实体失败 {entity_b_id} -> {entity_a_id}: {e}")
            return {
                "success": False,
                "message": f"合并实体失败: {str(e)}",
                "entity_a": entity_a_id,
                "entity_b": entity_b_id
            }
    
    def delete_entity(self, entity_id: str) -> Dict:
        """
        删除实体及其相关数据
        
        Args:
            entity_id: 要删除的实体ID
            
        Returns:
            包含删除结果的字典
        """
        try:
            logger.info(f"开始删除实体: {entity_id}")
            
            # 检查实体是否存在
            if not self.entity_storage.entity_exists(entity_id):
                return {
                    "success": False,
                    "message": f"实体不存在: {entity_id}",
                    "entity_id": entity_id
                }
            
            # 统计信息
            stats = {
                "entity_deleted": False,
                "relations_updated": 0,
                "relations_deleted": 0
            }
            
            # 1. 更新或删除涉及该实体的关系
            # 这里需要实现关系清理逻辑
            # 暂时简单处理：删除所有涉及该实体的关系
            relation_files = self.relation_storage.get_all_relation_files()
            deleted_relations = []
            
            for relation_file in relation_files:
                try:
                    with open(relation_file, 'r', encoding='utf-8') as f:
                        relation_data = json.load(f)
                    
                    subject = relation_data.get('subject')
                    obj = relation_data.get('object')
                    
                    if subject == entity_id or obj == entity_id:
                        relation_file.unlink()
                        deleted_relations.append(str(relation_file.name))
                        
                except Exception as e:
                    logger.warning(f"处理关系文件 {relation_file} 时出错: {e}")
                    continue
            
            stats['relations_deleted'] = len(deleted_relations)
            
            # 2. 删除实体文件
            entity_deleted = self.entity_storage.delete_entity(entity_id)
            stats['entity_deleted'] = entity_deleted
            
            if entity_deleted:
                logger.info(f"实体删除完成: {entity_id}")
                logger.info(f"删除统计: 实体删除成功, 关系删除{stats['relations_deleted']}个")
                
                return {
                    "success": True,
                    "message": f"成功删除实体 {entity_id}",
                    "entity_id": entity_id,
                    "stats": stats,
                    "deleted_relations": deleted_relations[:10]  # 只返回前10个
                }
            else:
                logger.error(f"删除实体失败: {entity_id}")
                return {
                    "success": False,
                    "message": f"删除实体失败: {entity_id}",
                    "entity_id": entity_id,
                    "stats": stats
                }
                
        except Exception as e:
            logger.error(f"删除实体失败 {entity_id}: {e}")
            return {
                "success": False,
                "message": f"删除实体失败: {str(e)}",
                "entity_id": entity_id
            }
    
    def update_entity(self, entity_id: str, updates: Dict) -> Dict:
        """
        更新实体信息
        
        Args:
            entity_id: 要更新的实体ID
            updates: 更新内容字典
            
        Returns:
            包含更新结果的字典
        """
        try:
            logger.info(f"开始更新实体: {entity_id}")
            
            # 检查实体是否存在
            if not self.entity_storage.entity_exists(entity_id):
                return {
                    "success": False,
                    "message": f"实体不存在: {entity_id}",
                    "entity_id": entity_id
                }
            
            # 加载现有实体数据
            entity_data = self.entity_storage.load_entity(entity_id)
            if not entity_data:
                return {
                    "success": False,
                    "message": f"加载实体数据失败: {entity_id}",
                    "entity_id": entity_id
                }
            
            # 应用更新
            updated_fields = []
            for key, value in updates.items():
                if key != 'id':  # 不允许修改实体ID
                    entity_data[key] = value
                    updated_fields.append(key)
            
            # 保存更新后的实体
            success = self.entity_storage.save_entity(entity_data)
            
            if success:
                logger.info(f"实体更新完成: {entity_id}, 更新字段: {updated_fields}")
                return {
                    "success": True,
                    "message": f"成功更新实体 {entity_id}",
                    "entity_id": entity_id,
                    "updated_fields": updated_fields
                }
            else:
                logger.error(f"保存实体更新失败: {entity_id}")
                return {
                    "success": False,
                    "message": f"保存实体更新失败: {entity_id}",
                    "entity_id": entity_id
                }
                
        except Exception as e:
            logger.error(f"更新实体失败 {entity_id}: {e}")
            return {
                "success": False,
                "message": f"更新实体失败: {str(e)}",
                "entity_id": entity_id
            }
    
    def get_entity_info(self, entity_id: str) -> Dict:
        """
        获取实体的详细信息
        
        Args:
            entity_id: 实体ID
            
        Returns:
            包含实体信息的字典
        """
        try:
            # 检查实体是否存在
            if not self.entity_storage.entity_exists(entity_id):
                return {
                    "success": False,
                    "message": f"实体不存在: {entity_id}",
                    "entity_id": entity_id
                }
            
            # 加载实体数据
            entity_data = self.entity_storage.load_entity(entity_id)
            if not entity_data:
                return {
                    "success": False,
                    "message": f"加载实体数据失败: {entity_id}",
                    "entity_id": entity_id
                }
            
            # 获取相关关系
            related_relations = []
            relation_files = self.relation_storage.get_all_relation_files()
            
            for relation_file in relation_files:
                try:
                    with open(relation_file, 'r', encoding='utf-8') as f:
                        relation_data = json.load(f)
                    
                    subject = relation_data.get('subject')
                    obj = relation_data.get('object')
                    
                    if subject == entity_id or obj == entity_id:
                        related_relations.append({
                            "file": str(relation_file.name),
                            "subject": subject,
                            "relation": relation_data.get('relation'),
                            "object": obj,
                            "confidence": relation_data.get('confidence')
                        })
                        
                except Exception:
                    continue
            
            # 统计信息
            features = entity_data.get('features', [])
            attributes = entity_data.get('attributes', [])
            sources = entity_data.get('sources', [])
            
            return {
                "success": True,
                "entity_id": entity_id,
                "entity_data": entity_data,
                "stats": {
                    "feature_count": len(features),
                    "attribute_count": len(attributes),
                    "source_count": len(sources),
                    "relation_count": len(related_relations)
                },
                "related_relations": related_relations[:20]  # 只返回前20个关系
            }
            
        except Exception as e:
            logger.error(f"获取实体信息失败 {entity_id}: {e}")
            return {
                "success": False,
                "message": f"获取实体信息失败: {str(e)}",
                "entity_id": entity_id
            }