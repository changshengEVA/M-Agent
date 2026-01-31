#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实体仓库模块

负责直接对实体文件进行操作：查询、删除、读取、保存等
文件格式：entity/{entity_id}.json
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Union

# 导入schemas中定义的类型
try:
    from ..schemas.kg_schemas import (
        EntityData, FeatureRecord, AttributeRecord,
        validate_entity_data, validate_feature_record, validate_attribute_record
    )
except ImportError:
    # 用于测试环境
    from memory.memory_core.schemas.kg_schemas import (
        EntityData, FeatureRecord, AttributeRecord,
        validate_entity_data, validate_feature_record, validate_attribute_record
    )

logger = logging.getLogger(__name__)


class EntityRepository:
    """实体仓库类"""
    
    def __init__(self, entity_dir: Path):
        """
        初始化实体仓库
        
        Args:
            entity_dir: 实体文件目录路径
        """
        self.entity_dir = entity_dir
        self.entity_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"初始化实体仓库，目录: {self.entity_dir}")
    
    def _sanitize_entity_name(self, entity_id: str) -> str:
        """
        清理实体名称，使其适合作为文件名
        
        Args:
            entity_id: 实体ID
            
        Returns:
            清理后的实体名称
        """
        # 替换可能引起问题的字符
        sanitized = entity_id.strip()
        # 替换空格为下划线
        sanitized = sanitized.replace(' ', '_')
        # 替换其他可能的问题字符
        for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
            sanitized = sanitized.replace(char, '_')
        # 限制长度
        if len(sanitized) > 100:
            sanitized = sanitized[:100]
        return sanitized
    
    def _get_entity_file_path(self, entity_id: str) -> Path:
        """
        获取实体文件路径
        
        Args:
            entity_id: 实体ID
            
        Returns:
            实体文件路径
        """
        sanitized_name = self._sanitize_entity_name(entity_id)
        return self.entity_dir / f"{sanitized_name}.json"
    
    def exists(self, entity_id: str) -> bool:
        """
        接收entity_id，返回是否存在
        
        Args:
            entity_id: 实体ID
            
        Returns:
            实体文件是否存在
        """
        entity_file = self._get_entity_file_path(entity_id)
        return entity_file.exists()
    
    def load(self, entity_id: str) -> Tuple[bool, Optional[EntityData]]:
        """
        接收entity_id，返回该实体文件的所有的json信息，返回也包含状态信息（成功与否）
        
        Args:
            entity_id: 实体ID
            
        Returns:
            (成功状态, 实体数据) 如果文件不存在或读取失败，返回(False, None)
        """
        entity_file = self._get_entity_file_path(entity_id)
        
        if not entity_file.exists():
            logger.warning(f"实体文件不存在: {entity_file}")
            return False, None
        
        try:
            with open(entity_file, 'r', encoding='utf-8') as f:
                entity_data = json.load(f)
            
            # 验证数据格式
            if not validate_entity_data(entity_data):
                logger.warning(f"实体数据格式验证失败: {entity_id}")
                return False, None
            
            logger.debug(f"成功加载实体: {entity_id}")
            return True, entity_data
        except Exception as e:
            logger.error(f"读取实体文件失败 {entity_file}: {e}")
            return False, None
    
    def save(self, entity_data: EntityData) -> bool:
        """
        存入entity的信息，将entity的信息保存到相应的文件中
        
        Args:
            entity_data: 完整的合法实体信息
            
        Returns:
            保存成功返回True，否则返回False
        """
        try:
            # 验证数据格式
            if not validate_entity_data(entity_data):
                logger.warning("实体数据格式验证失败")
                return False
            
            entity_id = entity_data.get('id')
            if not entity_id:
                logger.warning("实体数据缺少'id'字段")
                return False
            
            entity_file = self._get_entity_file_path(entity_id)
            
            # 确保目录存在
            entity_file.parent.mkdir(parents=True, exist_ok=True)
            
            # 保存实体文件
            with open(entity_file, 'w', encoding='utf-8') as f:
                json.dump(entity_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"保存实体: {entity_id} -> {entity_file}")
            return True
            
        except Exception as e:
            logger.error(f"保存实体失败 {entity_data.get('id', 'unknown')}: {e}")
            return False
    
    def delete(self, entity_id: str) -> bool:
        """
        删除该id的实体的文件，返回状态表示是否成功
        
        Args:
            entity_id: 实体ID
            
        Returns:
            删除成功返回True，否则返回False
        """
        try:
            entity_file = self._get_entity_file_path(entity_id)
            if entity_file.exists():
                entity_file.unlink()
                logger.info(f"删除实体文件: {entity_file}")
                return True
            logger.warning(f"实体文件不存在，无法删除: {entity_file}")
            return False
        except Exception as e:
            logger.error(f"删除实体文件失败 {entity_id}: {e}")
            return False
    
    def list_ids(self) -> List[str]:
        """
        罗列所有的已知的实体ID
        
        Returns:
            实体ID列表
        """
        if not self.entity_dir.exists():
            return []
        
        entity_ids = []
        for entity_file in self.entity_dir.glob("*.json"):
            # 从文件名提取实体ID（去掉.json后缀）
            entity_id = entity_file.stem
            entity_ids.append(entity_id)
        
        logger.debug(f"找到 {len(entity_ids)} 个实体")
        return entity_ids
    
    def append_feature(self, entity_id: str, feature_record: FeatureRecord) -> bool:
        """
        接收entity_id的信息与feature_record的信息，并将feature的信息添加到实体文件中
        
        Args:
            entity_id: 实体ID
            feature_record: 特征记录
            
        Returns:
            添加成功返回True，否则返回False
        """
        try:
            # 验证特征记录格式
            if not validate_feature_record(feature_record):
                logger.warning(f"特征记录格式验证失败: {entity_id}")
                return False
            
            # 加载现有实体数据
            success, entity_data = self.load(entity_id)
            if not success:
                logger.warning(f"无法加载实体 {entity_id}，无法添加特征")
                return False
            
            # 确保features字段存在
            if 'features' not in entity_data:
                entity_data['features'] = []
            
            # 添加特征记录
            entity_data['features'].append(feature_record)
            
            # 保存更新后的实体数据
            return self.save(entity_data)
            
        except Exception as e:
            logger.error(f"添加特征到实体失败 {entity_id}: {e}")
            return False
    
    def append_attribute(self, entity_id: str, attribute_record: AttributeRecord) -> bool:
        """
        接收entity_id的信息与attribute_record的信息，并将attribute的信息添加到实体文件中
        
        Args:
            entity_id: 实体ID
            attribute_record: 属性记录
            
        Returns:
            添加成功返回True，否则返回False
        """
        try:
            # 验证属性记录格式
            if not validate_attribute_record(attribute_record):
                logger.warning(f"属性记录格式验证失败: {entity_id}")
                return False
            
            # 加载现有实体数据
            success, entity_data = self.load(entity_id)
            if not success:
                logger.warning(f"无法加载实体 {entity_id}，无法添加属性")
                return False
            
            # 确保attributes字段存在
            if 'attributes' not in entity_data:
                entity_data['attributes'] = []
            
            # 检查是否已存在相同field的属性，如果存在则更新
            field = attribute_record.get('field')
            if field:
                for i, attr in enumerate(entity_data['attributes']):
                    if attr.get('field') == field:
                        # 更新现有属性
                        entity_data['attributes'][i] = attribute_record
                        logger.debug(f"更新实体 {entity_id} 的属性字段: {field}")
                        return self.save(entity_data)
            
            # 添加新的属性记录
            entity_data['attributes'].append(attribute_record)
            
            # 保存更新后的实体数据
            return self.save(entity_data)
            
        except Exception as e:
            logger.error(f"添加属性到实体失败 {entity_id}: {e}")
            return False