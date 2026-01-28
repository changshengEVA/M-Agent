#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
特征和属性存储模块

负责特征和属性的存储管理
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class FeatureAttributeStorage:
    """特征和属性存储管理类"""
    
    def __init__(self, entity_storage):
        """
        初始化特征和属性存储管理器
        
        Args:
            entity_storage: EntityStorage实例
        """
        self.entity_storage = entity_storage
    
    def save_feature(self, feature_data: Dict, source_info: Optional[Dict] = None) -> bool:
        """
        保存特征到对应的实体文件
        
        Args:
            feature_data: 特征数据，包含 entity_id, feature, scene_id 等字段
            source_info: 基本来源信息（可选）
            
        Returns:
            保存成功返回True，否则返回False
        """
        try:
            entity_id = feature_data.get('entity_id')
            if not entity_id:
                logger.warning("特征数据缺少'entity_id'字段")
                return False
            
            # 创建特征的来源信息，包含 scene_id（如果存在）
            feature_source_info = source_info.copy() if source_info else {}
            scene_id = feature_data.get('scene_id')
            if scene_id is not None:
                feature_source_info['scene_id'] = scene_id
            
            # 添加来源信息到特征数据
            if feature_source_info:
                # 确保 sources 字段存在
                if 'sources' not in feature_data:
                    feature_data['sources'] = []
                
                # 检查是否已存在相同来源（考虑dialogue_id, episode_id和scene_id）
                source_found = False
                for source in feature_data['sources']:
                    # 如果所有关键字段都匹配，则认为是相同来源
                    if (source.get('dialogue_id') == feature_source_info.get('dialogue_id') and
                        source.get('episode_id') == feature_source_info.get('episode_id') and
                        source.get('scene_id') == feature_source_info.get('scene_id')):
                        source_found = True
                        break
                
                if not source_found:
                    feature_data['sources'].append(feature_source_info)
            
            # 加载现有实体数据
            existing_data = self.entity_storage.load_entity(entity_id)
            
            if existing_data is None:
                # 如果实体文件不存在，创建基本实体结构
                existing_data = self.entity_storage.create_basic_entity(entity_id, source_info)
            
            # 确保 features 字段存在
            if 'features' not in existing_data:
                existing_data['features'] = []
            
            # 添加新特征
            feature_text = feature_data.get('feature')
            
            # 检查是否已存在相同特征
            feature_found = False
            for feat in existing_data['features']:
                if feat.get('feature') == feature_text and feat.get('entity_id') == entity_id:
                    # 合并来源信息
                    if source_info and 'sources' in feat:
                        # 检查是否已存在相同来源（考虑dialogue_id, episode_id和scene_id）
                        existing_source_found = False
                        for source in feat['sources']:
                            if (source.get('dialogue_id') == source_info.get('dialogue_id') and
                                source.get('episode_id') == source_info.get('episode_id') and
                                source.get('scene_id') == source_info.get('scene_id')):
                                existing_source_found = True
                                break
                        
                        if not existing_source_found:
                            feat['sources'].append(source_info)
                    
                    # 更新现有特征
                    feat.update(feature_data)
                    feature_found = True
                    break
            
            if not feature_found:
                # 添加新特征
                existing_data['features'].append(feature_data)
            
            # 保存更新后的实体文件
            entity_file = self.entity_storage.get_entity_file_path(entity_id)
            with open(entity_file, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=2)
            
            logger.debug(f"保存特征: {entity_id} -> {feature_text}")
            return True
            
        except Exception as e:
            logger.error(f"保存特征失败: {e}")
            return False
    
    def save_attribute(self, attribute_data: Dict, source_info: Optional[Dict] = None) -> bool:
        """
        保存属性到对应的实体文件
        
        Args:
            attribute_data: 属性数据，包含 entity, field, value, confidence, scene_id 等字段
            source_info: 基本来源信息（可选）
            
        Returns:
            保存成功返回True，否则返回False
        """
        try:
            entity = attribute_data.get('entity')
            if not entity:
                logger.warning("属性数据缺少'entity'字段")
                return False
            
            # 创建属性的来源信息，包含 scene_id（如果存在）
            attribute_source_info = source_info.copy() if source_info else {}
            scene_id = attribute_data.get('scene_id')
            if scene_id is not None:
                attribute_source_info['scene_id'] = scene_id
            
            # 添加来源信息到属性数据
            if attribute_source_info:
                # 确保 sources 字段存在
                if 'sources' not in attribute_data:
                    attribute_data['sources'] = []
                
                # 检查是否已存在相同来源（考虑dialogue_id, episode_id和scene_id）
                source_found = False
                for source in attribute_data['sources']:
                    # 如果所有关键字段都匹配，则认为是相同来源
                    if (source.get('dialogue_id') == attribute_source_info.get('dialogue_id') and
                        source.get('episode_id') == attribute_source_info.get('episode_id') and
                        source.get('scene_id') == attribute_source_info.get('scene_id')):
                        source_found = True
                        break
                
                if not source_found:
                    attribute_data['sources'].append(attribute_source_info)
            
            # 加载现有实体数据
            existing_data = self.entity_storage.load_entity(entity)
            
            if existing_data is None:
                # 如果实体文件不存在，创建基本实体结构
                existing_data = self.entity_storage.create_basic_entity(entity, source_info)
            
            # 确保 attributes 字段存在
            if 'attributes' not in existing_data:
                existing_data['attributes'] = []
            
            # 添加新属性
            field = attribute_data.get('field')
            value = attribute_data.get('value')
            
            # 检查是否已存在相同字段的属性
            attribute_found = False
            for attr in existing_data['attributes']:
                if attr.get('field') == field:
                    # 合并来源信息
                    if source_info and 'sources' in attr:
                        # 检查是否已存在相同来源（考虑dialogue_id, episode_id和scene_id）
                        existing_source_found = False
                        for source in attr['sources']:
                            if (source.get('dialogue_id') == source_info.get('dialogue_id') and
                                source.get('episode_id') == source_info.get('episode_id') and
                                source.get('scene_id') == source_info.get('scene_id')):
                                existing_source_found = True
                                break
                        
                        if not existing_source_found:
                            attr['sources'].append(source_info)
                    
                    # 选择置信度更高的值
                    attr_conf = attr.get('confidence', 0)
                    new_conf = attribute_data.get('confidence', 0)
                    if new_conf > attr_conf:
                        attr['value'] = value
                        attr['confidence'] = new_conf
                    
                    # 更新其他字段
                    attr.update({k: v for k, v in attribute_data.items() if k not in ['value', 'confidence', 'sources']})
                    attribute_found = True
                    break
            
            if not attribute_found:
                # 添加新属性
                existing_data['attributes'].append(attribute_data)
            
            # 保存更新后的实体文件
            entity_file = self.entity_storage.get_entity_file_path(entity)
            with open(entity_file, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=2)
            
            logger.debug(f"保存属性: {entity}.{field} = {value}")
            return True
            
        except Exception as e:
            logger.error(f"保存属性失败: {e}")
            return False
    
    def get_entity_features(self, entity_id: str) -> List[Dict]:
        """
        获取实体的所有特征
        
        Args:
            entity_id: 实体ID
            
        Returns:
            特征列表
        """
        entity_data = self.entity_storage.load_entity(entity_id)
        if entity_data is None:
            return []
        
        return entity_data.get('features', [])
    
    def get_entity_attributes(self, entity_id: str) -> List[Dict]:
        """
        获取实体的所有属性
        
        Args:
            entity_id: 实体ID
            
        Returns:
            属性列表
        """
        entity_data = self.entity_storage.load_entity(entity_id)
        if entity_data is None:
            return []
        
        return entity_data.get('attributes', [])
    
    def merge_features(self, features_a: List[Dict], features_b: List[Dict], entity_id: str) -> List[Dict]:
        """
        合并两个特征列表
        
        Args:
            features_a: 特征列表A
            features_b: 特征列表B
            entity_id: 目标实体ID
            
        Returns:
            合并后的特征列表
        """
        merged_features = features_a.copy()
        
        for feature_b in features_b:
            # 更新特征中的entity_id为目标实体ID
            feature_b['entity_id'] = entity_id
            
            # 检查是否已存在相同特征
            feature_exists = False
            for feature_a in merged_features:
                if (feature_a.get('feature') == feature_b.get('feature') and
                    feature_a.get('entity_id') == entity_id):
                    # 合并来源信息
                    if 'sources' in feature_b:
                        sources_a_feat = feature_a.get('sources', [])
                        sources_b_feat = feature_b.get('sources', [])
                        # 简单的合并逻辑，实际应该使用SourceManager
                        for source in sources_b_feat:
                            if source not in sources_a_feat:
                                sources_a_feat.append(source)
                        feature_a['sources'] = sources_a_feat
                    
                    # 更新其他字段
                    feature_a.update({k: v for k, v in feature_b.items() if k != 'sources'})
                    feature_exists = True
                    break
            
            if not feature_exists:
                merged_features.append(feature_b)
        
        return merged_features
    
    def merge_attributes(self, attributes_a: List[Dict], attributes_b: List[Dict], entity_id: str) -> List[Dict]:
        """
        合并两个属性列表
        
        Args:
            attributes_a: 属性列表A
            attributes_b: 属性列表B
            entity_id: 目标实体ID
            
        Returns:
            合并后的属性列表
        """
        merged_attributes = attributes_a.copy()
        
        for attribute_b in attributes_b:
            # 更新属性中的entity为目标实体ID
            attribute_b['entity'] = entity_id
            
            # 检查是否已存在相同字段的属性
            attribute_exists = False
            for attribute_a in merged_attributes:
                if attribute_a.get('field') == attribute_b.get('field'):
                    # 合并来源信息
                    if 'sources' in attribute_b:
                        sources_a_attr = attribute_a.get('sources', [])
                        sources_b_attr = attribute_b.get('sources', [])
                        # 简单的合并逻辑，实际应该使用SourceManager
                        for source in sources_b_attr:
                            if source not in sources_a_attr:
                                sources_a_attr.append(source)
                        attribute_a['sources'] = sources_a_attr
                    
                    # 选择置信度更高的值
                    attr_a_conf = attribute_a.get('confidence', 0)
                    attr_b_conf = attribute_b.get('confidence', 0)
                    if attr_b_conf > attr_a_conf:
                        attribute_a['value'] = attribute_b['value']
                        attribute_a['confidence'] = attr_b_conf
                    
                    attribute_exists = True
                    break
            
            if not attribute_exists:
                merged_attributes.append(attribute_b)
        
        return merged_attributes