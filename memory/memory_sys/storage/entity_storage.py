#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实体存储模块

负责实体的存储、加载和合并操作
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class EntityStorage:
    """实体存储管理类"""
    
    def __init__(self, entity_dir: Path):
        """
        初始化实体存储管理器
        
        Args:
            entity_dir: 实体文件目录路径
        """
        self.entity_dir = entity_dir
        self.entity_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"初始化实体存储，目录: {self.entity_dir}")
    
    def _sanitize_entity_name(self, entity_name: str) -> str:
        """
        清理实体名称，使其适合作为文件名
        
        Args:
            entity_name: 原始实体名称
            
        Returns:
            清理后的实体名称
        """
        # 替换可能引起问题的字符
        sanitized = entity_name.strip()
        # 替换空格为下划线
        sanitized = sanitized.replace(' ', '_')
        # 替换其他可能的问题字符
        for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
            sanitized = sanitized.replace(char, '_')
        # 限制长度
        if len(sanitized) > 100:
            sanitized = sanitized[:100]
        return sanitized
    
    def get_entity_file_path(self, entity_id: str) -> Path:
        """
        获取实体文件路径
        
        Args:
            entity_id: 实体ID
            
        Returns:
            实体文件路径
        """
        sanitized_name = self._sanitize_entity_name(entity_id)
        return self.entity_dir / f"{sanitized_name}.json"
    
    def load_entity(self, entity_id: str) -> Optional[Dict]:
        """
        加载实体数据
        
        Args:
            entity_id: 实体ID
            
        Returns:
            实体数据字典，如果文件不存在则返回None
        """
        entity_file = self.get_entity_file_path(entity_id)
        
        if not entity_file.exists():
            return None
        
        try:
            with open(entity_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"读取实体文件失败 {entity_file}: {e}")
            return None
    
    def save_entity(self, entity_data: Dict, source_info: Optional[Dict] = None) -> bool:
        """
        保存实体到文件
        
        Args:
            entity_data: 实体数据，包含 id, type, confidence 等字段
            source_info: 来源信息（可选）
            
        Returns:
            保存成功返回True，否则返回False
        """
        try:
            entity_id = entity_data.get('id')
            if not entity_id:
                logger.warning("实体数据缺少'id'字段")
                return False
            
            entity_file = self.get_entity_file_path(entity_id)
            
            # 如果文件已存在，合并数据
            existing_data = {}
            if entity_file.exists():
                existing_data = self.load_entity(entity_id) or {}
            
            # 确保 sources 字段存在
            if 'sources' not in existing_data:
                existing_data['sources'] = []
            
            # 添加来源信息（如果提供）
            if source_info:
                # 检查是否已存在相同来源（考虑dialogue_id, episode_id和scene_id）
                source_found = False
                for source in existing_data['sources']:
                    # 如果所有关键字段都匹配，则认为是相同来源
                    if (source.get('dialogue_id') == source_info.get('dialogue_id') and
                        source.get('episode_id') == source_info.get('episode_id') and
                        source.get('scene_id') == source_info.get('scene_id')):
                        source_found = True
                        break
                
                if not source_found:
                    existing_data['sources'].append(source_info)
            
            # 合并基本数据（保留现有数据，用新数据更新）
            # 注意：不覆盖 sources 字段
            for key, value in entity_data.items():
                if key != 'sources':
                    existing_data[key] = value
            
            # 确保 features 字段存在（如果实体数据中有features）
            if 'features' in entity_data and 'features' not in existing_data:
                existing_data['features'] = []
            
            # 确保 attributes 字段存在（如果实体数据中有attributes）
            if 'attributes' in entity_data and 'attributes' not in existing_data:
                existing_data['attributes'] = []
            
            # 保存实体文件
            with open(entity_file, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=2)
            
            logger.debug(f"保存实体: {entity_id} -> {entity_file}")
            return True
            
        except Exception as e:
            logger.error(f"保存实体失败 {entity_data.get('id', 'unknown')}: {e}")
            return False
    
    def entity_exists(self, entity_id: str) -> bool:
        """
        检查实体是否存在
        
        Args:
            entity_id: 实体ID
            
        Returns:
            实体文件是否存在
        """
        entity_file = self.get_entity_file_path(entity_id)
        return entity_file.exists()
    
    def delete_entity(self, entity_id: str) -> bool:
        """
        删除实体文件
        
        Args:
            entity_id: 实体ID
            
        Returns:
            删除成功返回True，否则返回False
        """
        try:
            entity_file = self.get_entity_file_path(entity_id)
            if entity_file.exists():
                entity_file.unlink()
                logger.info(f"删除实体文件: {entity_file}")
                return True
            return False
        except Exception as e:
            logger.error(f"删除实体文件失败 {entity_id}: {e}")
            return False
    
    def get_all_entity_files(self) -> List[Path]:
        """
        获取所有实体文件
        
        Returns:
            实体文件路径列表
        """
        if not self.entity_dir.exists():
            return []
        
        return list(self.entity_dir.glob("*.json"))
    
    def get_entity_count(self) -> int:
        """
        获取实体数量
        
        Returns:
            实体文件数量
        """
        return len(self.get_all_entity_files())
    
    def create_basic_entity(self, entity_id: str, source_info: Optional[Dict] = None) -> Dict:
        """
        创建基本实体结构
        
        Args:
            entity_id: 实体ID
            source_info: 来源信息（可选）
            
        Returns:
            基本实体数据字典
        """
        entity_data = {
            "id": entity_id,
            "sources": [],
            "features": [],
            "attributes": []
        }
        
        if source_info:
            entity_data['sources'].append(source_info)
        
        return entity_data