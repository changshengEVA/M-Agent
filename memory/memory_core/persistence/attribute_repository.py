#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
属性仓库模块

负责直接对实体文件的属性部分进行操作：字段型事实
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Union

logger = logging.getLogger(__name__)

# 导入schemas中定义的类型
try:
    from ..schemas.kg_schemas import (
        AttributeRecord, validate_attribute_record
    )
except ImportError:
    # 用于测试环境
    from memory.memory_core.schemas.kg_schemas import (
        AttributeRecord, validate_attribute_record
    )

# 动态导入EntityRepository，避免循环导入
try:
    from .entity_repository import EntityRepository
except ImportError:
    # 用于测试环境
    from entity_repository import EntityRepository


class AttributeRepository:
    """属性仓库类"""
    
    def __init__(self, entity_repository: EntityRepository):
        """
        初始化属性仓库
        
        Args:
            entity_repository: EntityRepository实例
        """
        self.entity_repository = entity_repository
        logger.info("初始化属性仓库")
    
    def set(self, entity_id: str, attribute_record: AttributeRecord) -> Dict[str, Any]:
        """
        设置一条新的属性，返回执行状态
        
        Args:
            entity_id: 实体ID
            attribute_record: 属性记录
            
        Returns:
            执行状态字典
        """
        try:
            # 验证属性记录格式
            if not validate_attribute_record(attribute_record):
                return {
                    "success": False,
                    "message": "属性记录格式验证失败",
                    "entity_id": entity_id
                }
            
            # 确保属性记录包含entity字段
            attribute_record['entity'] = entity_id
            
            # 加载现有实体数据
            success, entity_data = self.entity_repository.load(entity_id)
            if not success:
                # 如果实体不存在，创建基本实体结构
                entity_data = {
                    "id": entity_id,
                    "sources": [],
                    "features": [],
                    "attributes": []
                }
                # 保存基本实体
                if not self.entity_repository.save(entity_data):
                    return {
                        "success": False,
                        "message": f"创建实体 {entity_id} 失败",
                        "entity_id": entity_id
                    }
            
            # 确保attributes字段存在
            if 'attributes' not in entity_data:
                entity_data['attributes'] = []
            
            # 检查是否已存在相同字段的属性
            field = attribute_record['field']
            attribute_exists = False
            
            for i, existing_attribute in enumerate(entity_data['attributes']):
                if existing_attribute.get('field') == field:
                    # 合并来源信息
                    if 'sources' in attribute_record:
                        sources_existing = existing_attribute.get('sources', [])
                        sources_new = attribute_record.get('sources', [])
                        
                        # 合并来源（简单的去重逻辑）
                        for source in sources_new:
                            if source not in sources_existing:
                                sources_existing.append(source)
                        
                        existing_attribute['sources'] = sources_existing
                    
                    # 比较置信度，选择置信度更高的值
                    existing_confidence = existing_attribute.get('confidence', 0)
                    new_confidence = attribute_record.get('confidence', 0)
                    
                    if new_confidence > existing_confidence:
                        # 新记录置信度更高，更新值
                        existing_attribute['value'] = attribute_record['value']
                        existing_attribute['confidence'] = new_confidence
                        
                        # 更新其他字段
                        for key, value in attribute_record.items():
                            if key not in ['value', 'confidence', 'sources']:
                                existing_attribute[key] = value
                    else:
                        # 现有记录置信度更高或相等，保留现有值
                        # 只更新非关键字段
                        for key, value in attribute_record.items():
                            if key not in ['value', 'confidence', 'sources', 'field']:
                                existing_attribute[key] = value
                    
                    # 更新实体中的属性
                    entity_data['attributes'][i] = existing_attribute
                    attribute_exists = True
                    break
            
            if not attribute_exists:
                # 添加新属性
                entity_data['attributes'].append(attribute_record)
            
            # 保存更新后的实体数据
            if self.entity_repository.save(entity_data):
                return {
                    "success": True,
                    "message": f"成功设置实体 {entity_id} 的属性字段 '{field}'",
                    "entity_id": entity_id,
                    "field": field,
                    "value": attribute_record['value'],
                    "action": "updated" if attribute_exists else "added"
                }
            else:
                return {
                    "success": False,
                    "message": f"保存实体 {entity_id} 失败",
                    "entity_id": entity_id
                }
            
        except Exception as e:
            logger.error(f"设置实体属性失败 {entity_id}: {e}")
            return {
                "success": False,
                "message": f"设置属性失败: {str(e)}",
                "entity_id": entity_id
            }
    
    def list(self, entity_id: str) -> List[AttributeRecord]:
        """
        罗列出该实体的所有属性
        
        Args:
            entity_id: 实体ID
            
        Returns:
            该实体的所有属性记录列表
        """
        try:
            success, entity_data = self.entity_repository.load(entity_id)
            if not success:
                logger.warning(f"实体 {entity_id} 不存在，无法获取属性")
                return []
            
            attributes = entity_data.get('attributes', [])
            
            # 过滤验证通过的属性记录
            valid_attributes = []
            for attr in attributes:
                if validate_attribute_record(attr):
                    valid_attributes.append(attr)
                else:
                    logger.warning(f"属性记录格式验证失败: {entity_id} - {attr.get('field', 'unknown')}")
            
            logger.debug(f"获取实体 {entity_id} 的 {len(valid_attributes)} 个有效属性")
            return valid_attributes
            
        except Exception as e:
            logger.error(f"获取实体属性失败 {entity_id}: {e}")
            return []