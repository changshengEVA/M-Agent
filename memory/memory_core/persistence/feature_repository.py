#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
特征仓库模块

负责直接对实体文件的Feature字段部分进行操作：追加性的证据事实
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Union

logger = logging.getLogger(__name__)

# 导入schemas中定义的类型
try:
    from ..schemas.kg_schemas import (
        FeatureRecord, validate_feature_record
    )
except ImportError:
    # 用于测试环境
    from memory.memory_core.schemas.kg_schemas import (
        FeatureRecord, validate_feature_record
    )

# 动态导入EntityRepository，避免循环导入
try:
    from .entity_repository import EntityRepository
except ImportError:
    # 用于测试环境
    from entity_repository import EntityRepository


class FeatureRepository:
    """特征仓库类"""
    
    def __init__(self, entity_repository: EntityRepository):
        """
        初始化特征仓库
        
        Args:
            entity_repository: EntityRepository实例
        """
        self.entity_repository = entity_repository
        logger.info("初始化特征仓库")
    
    def append(self, entity_id: str, feature_record: FeatureRecord) -> Dict[str, Any]:
        """
        将一条Feature信息添加进一个实体当中，并返回状态信息
        
        Args:
            entity_id: 实体ID
            feature_record: 特征记录
            
        Returns:
            执行状态字典
        """
        try:
            # 验证特征记录格式
            if not validate_feature_record(feature_record):
                return {
                    "success": False,
                    "message": "特征记录格式验证失败",
                    "entity_id": entity_id
                }
            
            # 确保特征记录包含entity_id
            feature_record['entity_id'] = entity_id
            
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
            
            # 确保features字段存在
            if 'features' not in entity_data:
                entity_data['features'] = []
            
            # 检查是否已存在相同特征
            feature_text = feature_record['feature']
            feature_exists = False
            
            for i, existing_feature in enumerate(entity_data['features']):
                if existing_feature.get('feature') == feature_text:
                    # 合并来源信息
                    if 'sources' in feature_record:
                        sources_existing = existing_feature.get('sources', [])
                        sources_new = feature_record.get('sources', [])
                        
                        # 合并来源（简单的去重逻辑）
                        for source in sources_new:
                            if source not in sources_existing:
                                sources_existing.append(source)
                        
                        existing_feature['sources'] = sources_existing
                    
                    # 更新其他字段（保留现有值，除非新记录有更高置信度）
                    existing_confidence = existing_feature.get('confidence', 0)
                    new_confidence = feature_record.get('confidence', 0)
                    
                    if new_confidence > existing_confidence:
                        # 更新置信度更高的字段
                        for key, value in feature_record.items():
                            if key != 'sources':  # sources已单独处理
                                existing_feature[key] = value
                    else:
                        # 只更新非置信度相关字段
                        for key, value in feature_record.items():
                            if key not in ['confidence', 'sources']:
                                existing_feature[key] = value
                    
                    # 更新实体中的特征
                    entity_data['features'][i] = existing_feature
                    feature_exists = True
                    break
            
            if not feature_exists:
                # 添加新特征
                entity_data['features'].append(feature_record)
            
            # 保存更新后的实体数据
            if self.entity_repository.save(entity_data):
                return {
                    "success": True,
                    "message": f"成功添加特征到实体 {entity_id}",
                    "entity_id": entity_id,
                    "feature": feature_text,
                    "action": "updated" if feature_exists else "added"
                }
            else:
                return {
                    "success": False,
                    "message": f"保存实体 {entity_id} 失败",
                    "entity_id": entity_id
                }
            
        except Exception as e:
            logger.error(f"添加特征到实体失败 {entity_id}: {e}")
            return {
                "success": False,
                "message": f"添加特征失败: {str(e)}",
                "entity_id": entity_id
            }
    
    def list(self, entity_id: str) -> List[FeatureRecord]:
        """
        罗列返回一个实体的所有Feature信息
        
        Args:
            entity_id: 实体ID
            
        Returns:
            该实体的所有特征记录列表
        """
        try:
            success, entity_data = self.entity_repository.load(entity_id)
            if not success:
                logger.warning(f"实体 {entity_id} 不存在，无法获取特征")
                return []
            
            features = entity_data.get('features', [])
            
            # 过滤验证通过的特征记录
            valid_features = []
            for feature in features:
                if validate_feature_record(feature):
                    valid_features.append(feature)
                else:
                    logger.warning(f"特征记录格式验证失败: {entity_id} - {feature.get('feature', 'unknown')}")
            
            logger.debug(f"获取实体 {entity_id} 的 {len(valid_features)} 个有效特征")
            return valid_features
            
        except Exception as e:
            logger.error(f"获取实体特征失败 {entity_id}: {e}")
            return []