#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实体库管理模块

负责实体库的加载、保存和实体匹配功能
实体库结构：JSON列表，每个条目包含：
{
    "ID": "实体名称",
    "alias_names": ["别名1", "别名2", ...],
    "embedding": [0.1, 0.2, ...]  # 可选，实体名称的嵌入向量
}
"""

import json
import logging
import numpy as np
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Union
from dataclasses import dataclass, field
import hashlib

logger = logging.getLogger(__name__)


@dataclass
class EntityRecord:
    """实体记录"""
    id: str
    alias_names: List[str] = field(default_factory=list)
    embedding: Optional[List[float]] = None
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        result = {
            "ID": self.id,
            "alias_names": self.alias_names
        }
        if self.embedding is not None:
            result["embedding"] = self.embedding
        return result
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'EntityRecord':
        """从字典创建"""
        return cls(
            id=data.get("ID", ""),
            alias_names=data.get("alias_names", []),
            embedding=data.get("embedding")
        )
    
    def get_all_names(self) -> List[str]:
        """获取所有名称（包括ID和别名）"""
        names = [self.id]
        names.extend(self.alias_names)
        return names
    
    def add_alias(self, alias: str) -> bool:
        """添加别名，如果不存在则添加"""
        if alias not in self.alias_names and alias != self.id:
            self.alias_names.append(alias)
            return True
        return False


class EntityLibrary:
    """实体库管理类"""
    
    def __init__(self, library_path: Union[str, Path]):
        """
        初始化实体库
        
        Args:
            library_path: 实体库文件路径
        """
        self.library_path = Path(library_path)
        self.entities: Dict[str, EntityRecord] = {}  # ID -> EntityRecord
        self.name_to_id: Dict[str, str] = {}  # 名称（包括别名）-> ID
        self.embeddings: Dict[str, List[float]] = {}  # ID -> 嵌入向量
        
        # 加载或初始化实体库
        self._load_or_init()
    
    def _load_or_init(self) -> None:
        """加载或初始化实体库"""
        if self.library_path.exists():
            try:
                with open(self.library_path, 'r', encoding='utf-8') as f:
                    library_data = json.load(f)
                
                if not isinstance(library_data, list):
                    logger.warning(f"实体库文件格式错误，应为列表，实际为 {type(library_data)}，重新初始化")
                    library_data = []
                
                # 加载实体记录
                for item in library_data:
                    try:
                        record = EntityRecord.from_dict(item)
                        if record.id:
                            self.entities[record.id] = record
                            # 建立名称到ID的映射
                            for name in record.get_all_names():
                                self.name_to_id[name] = record.id
                            # 存储嵌入向量
                            if record.embedding:
                                self.embeddings[record.id] = record.embedding
                    except Exception as e:
                        logger.warning(f"加载实体记录失败: {item}, 错误: {e}")
                
                logger.info(f"加载实体库成功，共 {len(self.entities)} 个实体")
                
            except Exception as e:
                logger.error(f"加载实体库文件失败 {self.library_path}: {e}")
                self._init_empty_library()
        else:
            self._init_empty_library()
    
    def _init_empty_library(self) -> None:
        """初始化空实体库"""
        self.entities = {}
        self.name_to_id = {}
        self.embeddings = {}
        logger.info("初始化空实体库")
    
    def save(self) -> bool:
        """保存实体库到文件"""
        try:
            # 准备保存数据
            library_data = []
            for record in self.entities.values():
                library_data.append(record.to_dict())
            
            # 确保目录存在
            self.library_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 保存文件
            with open(self.library_path, 'w', encoding='utf-8') as f:
                json.dump(library_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"保存实体库成功，共 {len(library_data)} 个实体")
            return True
            
        except Exception as e:
            logger.error(f"保存实体库失败 {self.library_path}: {e}")
            return False
    
    def entity_exists(self, entity_name: str) -> bool:
        """检查实体名称或别名是否存在于实体库中"""
        return entity_name in self.name_to_id
    
    def get_entity_id(self, entity_name: str) -> Optional[str]:
        """通过名称（ID或别名）获取实体ID"""
        return self.name_to_id.get(entity_name)
    
    def get_entity_record(self, entity_id: str) -> Optional[EntityRecord]:
        """获取实体记录"""
        return self.entities.get(entity_id)
    
    def add_entity(self, entity_id: str, embedding: Optional[List[float]] = None) -> bool:
        """
        添加新实体到实体库
        
        Args:
            entity_id: 实体ID
            embedding: 实体嵌入向量（可选）
            
        Returns:
            是否成功添加
        """
        if entity_id in self.entities:
            logger.debug(f"实体已存在: {entity_id}")
            return False
        
        # 创建新记录
        record = EntityRecord(id=entity_id, embedding=embedding)
        self.entities[entity_id] = record
        self.name_to_id[entity_id] = entity_id
        
        # 存储嵌入向量
        if embedding:
            self.embeddings[entity_id] = embedding
        
        logger.info(f"添加新实体到实体库: {entity_id}")
        return True
    
    def add_alias(self, entity_id: str, alias: str) -> bool:
        """
        为实体添加别名
        
        Args:
            entity_id: 实体ID
            alias: 别名
            
        Returns:
            是否成功添加
        """
        if entity_id not in self.entities:
            logger.warning(f"实体不存在，无法添加别名: {entity_id}")
            return False
        
        if alias in self.name_to_id:
            logger.warning(f"别名已存在: {alias} -> {self.name_to_id[alias]}")
            return False
        
        # 添加别名
        record = self.entities[entity_id]
        if record.add_alias(alias):
            self.name_to_id[alias] = entity_id
            logger.info(f"为实体 {entity_id} 添加别名: {alias}")
            return True
        
        return False
    
    def find_similar_entities(
        self, 
        entity_id: str, 
        embedding: List[float], 
        threshold: float = 0.7
    ) -> List[Tuple[str, float]]:
        """
        查找相似的实体
        
        Args:
            entity_id: 实体ID
            embedding: 实体嵌入向量
            threshold: 相似度阈值
            
        Returns:
            相似实体列表，每个元素为 (实体ID, 相似度)
        """
        similar_entities = []
        
        if not self.embeddings:
            return similar_entities
        
        # 计算与所有实体的相似度
        for other_id, other_embedding in self.embeddings.items():
            if other_id == entity_id:
                continue
            
            # 计算余弦相似度
            similarity = self._cosine_similarity(embedding, other_embedding)
            if similarity >= threshold:
                similar_entities.append((other_id, similarity))
        
        # 按相似度降序排序
        similar_entities.sort(key=lambda x: x[1], reverse=True)
        
        return similar_entities
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """计算余弦相似度"""
        if not vec1 or not vec2:
            return 0.0
        
        # 确保向量长度相同
        min_len = min(len(vec1), len(vec2))
        v1 = np.array(vec1[:min_len])
        v2 = np.array(vec2[:min_len])
        
        # 计算余弦相似度
        dot_product = np.dot(v1, v2)
        norm1 = np.linalg.norm(v1)
        norm2 = np.linalg.norm(v2)
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        similarity = dot_product / (norm1 * norm2)
        return float(similarity)
    
    def get_all_entities(self) -> List[Dict]:
        """获取所有实体信息"""
        return [record.to_dict() for record in self.entities.values()]
    
    def get_entity_count(self) -> int:
        """获取实体数量"""
        return len(self.entities)
    
    def clear(self) -> None:
        """清空实体库"""
        self.entities.clear()
        self.name_to_id.clear()
        self.embeddings.clear()
        logger.info("清空实体库")