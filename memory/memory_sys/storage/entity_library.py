#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实体库管理模块

负责实体库的加载、保存和实体匹配功能
实体库结构：每个实体保存在单独的JSON文件中，文件名为实体ID
文件内容格式：
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
            library_path: 实体库目录路径（不再是单个文件）
        """
        self.library_dir = Path(library_path)
        self.entities: Dict[str, EntityRecord] = {}  # ID -> EntityRecord
        self.name_to_id: Dict[str, str] = {}  # 名称（包括别名）-> ID
        self.embeddings: Dict[str, List[float]] = {}  # ID -> 嵌入向量
        
        # 加载或初始化实体库
        self._load_or_init()
    
    def _load_or_init(self) -> None:
        """加载或初始化实体库"""
        # 如果路径是文件（旧格式），尝试迁移
        if self.library_dir.exists() and self.library_dir.is_file():
            logger.warning(f"检测到旧格式的实体库文件: {self.library_dir}")
            self._migrate_from_old_format()
            return
        
        # 确保目录存在
        self.library_dir.mkdir(parents=True, exist_ok=True)
        
        # 加载目录中的所有实体文件
        try:
            entity_files = list(self.library_dir.glob("*.json"))
            logger.info(f"在目录 {self.library_dir} 中找到 {len(entity_files)} 个实体文件")
            
            for entity_file in entity_files:
                try:
                    with open(entity_file, 'r', encoding='utf-8') as f:
                        entity_data = json.load(f)
                    
                    record = EntityRecord.from_dict(entity_data)
                    if record.id:
                        # 验证文件名与实体ID匹配
                        expected_filename = f"{record.id}.json"
                        if entity_file.name != expected_filename:
                            logger.warning(f"实体文件名不匹配: {entity_file.name} (应为 {expected_filename})")
                            # 可以重命名文件，但暂时只记录警告
                        
                        self.entities[record.id] = record
                        # 建立名称到ID的映射
                        for name in record.get_all_names():
                            self.name_to_id[name] = record.id
                        # 存储嵌入向量
                        if record.embedding:
                            self.embeddings[record.id] = record.embedding
                            
                except Exception as e:
                    logger.warning(f"加载实体文件失败 {entity_file}: {e}")
                    continue
            
            logger.info(f"加载实体库成功，共 {len(self.entities)} 个实体")
            
        except Exception as e:
            logger.error(f"加载实体库目录失败 {self.library_dir}: {e}")
            self._init_empty_library()
    
    def _migrate_from_old_format(self) -> None:
        """从旧格式（单个JSON文件）迁移到新格式（每个实体一个文件）"""
        try:
            old_file = self.library_dir
            library_dir = old_file.parent / "entity_library"
            
            logger.info(f"从旧格式迁移实体库: {old_file} -> {library_dir}")
            
            # 检查文件是否为空或无效
            if old_file.stat().st_size == 0:
                logger.warning(f"旧实体库文件为空: {old_file}")
                # 创建空目录并删除旧文件
                library_dir.mkdir(parents=True, exist_ok=True)
                old_file.unlink()
                self.library_dir = library_dir
                self._init_empty_library()
                return
            
            # 读取旧文件
            try:
                with open(old_file, 'r', encoding='utf-8') as f:
                    library_data = json.load(f)
            except json.JSONDecodeError as e:
                logger.warning(f"旧实体库文件JSON格式无效: {old_file}, 错误: {e}")
                # 创建空目录并删除旧文件
                library_dir.mkdir(parents=True, exist_ok=True)
                old_file.unlink()
                self.library_dir = library_dir
                self._init_empty_library()
                return
            
            if not isinstance(library_data, list):
                logger.warning(f"旧实体库文件格式错误，应为列表，实际为 {type(library_data)}")
                library_data = []
            
            # 创建新目录
            library_dir.mkdir(parents=True, exist_ok=True)
            
            # 保存每个实体到单独文件
            migrated_count = 0
            for item in library_data:
                try:
                    record = EntityRecord.from_dict(item)
                    if record.id:
                        entity_file = library_dir / f"{record.id}.json"
                        with open(entity_file, 'w', encoding='utf-8') as f:
                            json.dump(record.to_dict(), f, ensure_ascii=False, indent=2)
                        
                        self.entities[record.id] = record
                        # 建立名称到ID的映射
                        for name in record.get_all_names():
                            self.name_to_id[name] = record.id
                        # 存储嵌入向量
                        if record.embedding:
                            self.embeddings[record.id] = record.embedding
                        
                        migrated_count += 1
                            
                except Exception as e:
                    logger.warning(f"迁移实体记录失败: {item}, 错误: {e}")
            
            # 更新库目录路径
            self.library_dir = library_dir
            
            # 备份旧文件（如果迁移成功）
            if migrated_count > 0:
                backup_file = old_file.with_suffix('.json.backup')
                try:
                    old_file.rename(backup_file)
                    logger.info(f"旧实体库文件已备份到: {backup_file}")
                except Exception as e:
                    logger.warning(f"备份旧文件失败: {e}")
            else:
                # 没有成功迁移，直接删除旧文件
                try:
                    old_file.unlink()
                    logger.info(f"删除无效的旧实体库文件: {old_file}")
                except Exception as e:
                    logger.warning(f"删除旧文件失败: {e}")
            
            logger.info(f"实体库迁移完成，共迁移 {migrated_count} 个实体")
            
        except Exception as e:
            logger.error(f"迁移实体库失败: {e}")
            # 如果迁移失败，尝试使用目录名
            try:
                library_dir = self.library_dir.parent / "entity_library"
                library_dir.mkdir(parents=True, exist_ok=True)
                self.library_dir = library_dir
                self._init_empty_library()
            except Exception:
                self._init_empty_library()
    
    def _init_empty_library(self) -> None:
        """初始化空实体库"""
        self.entities = {}
        self.name_to_id = {}
        self.embeddings = {}
        # 确保目录存在
        self.library_dir.mkdir(parents=True, exist_ok=True)
        logger.info("初始化空实体库")
    
    def save(self) -> bool:
        """保存实体库到目录（每个实体一个文件）"""
        try:
            # 确保目录存在
            self.library_dir.mkdir(parents=True, exist_ok=True)
            
            # 获取目录中现有的所有实体文件
            existing_files = set(f.name for f in self.library_dir.glob("*.json"))
            saved_files = set()
            
            # 保存每个实体到单独文件
            for record in self.entities.values():
                entity_file = self.library_dir / f"{record.id}.json"
                try:
                    with open(entity_file, 'w', encoding='utf-8') as f:
                        json.dump(record.to_dict(), f, ensure_ascii=False, indent=2)
                    saved_files.add(entity_file.name)
                    logger.debug(f"保存实体文件: {entity_file.name}")
                except Exception as e:
                    logger.error(f"保存实体文件失败 {entity_file}: {e}")
                    return False
            
            # 删除不再存在的实体文件
            files_to_delete = existing_files - saved_files
            for filename in files_to_delete:
                file_path = self.library_dir / filename
                try:
                    file_path.unlink()
                    logger.info(f"删除不再存在的实体文件: {filename}")
                except Exception as e:
                    logger.warning(f"删除实体文件失败 {file_path}: {e}")
            
            logger.info(f"保存实体库成功，共 {len(self.entities)} 个实体")
            return True
            
        except Exception as e:
            logger.error(f"保存实体库失败 {self.library_dir}: {e}")
            return False
    
    def save_entity(self, entity_id: str) -> bool:
        """
        保存单个实体到文件
        
        Args:
            entity_id: 实体ID
            
        Returns:
            是否成功保存
        """
        if entity_id not in self.entities:
            logger.warning(f"实体不存在，无法保存: {entity_id}")
            return False
        
        try:
            record = self.entities[entity_id]
            entity_file = self.library_dir / f"{entity_id}.json"
            
            # 确保目录存在
            self.library_dir.mkdir(parents=True, exist_ok=True)
            
            with open(entity_file, 'w', encoding='utf-8') as f:
                json.dump(record.to_dict(), f, ensure_ascii=False, indent=2)
            
            logger.debug(f"保存单个实体文件: {entity_file.name}")
            return True
            
        except Exception as e:
            logger.error(f"保存单个实体文件失败 {entity_id}: {e}")
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