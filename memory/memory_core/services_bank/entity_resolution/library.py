#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实体解析用派生索引（EntityLibrary）

职责：
- 从 KG 重建初始化实体派生索引（`rebuild_from_kg`）
- 提供实体候选查找能力（`search`）
- 管理实体主 ID、别名列表、embedding、规范化名称等解析辅助信息

重要约定：
- EntityLibrary 不是权威数据源
- 内容可被随时丢弃并从 KG 重建
- 不反向写入 KG
- 仅作为 entity_resolution 的判定支持层
"""

import logging
import time
import json
import os
from typing import Dict, List, Optional, Tuple, Any, Set
from dataclasses import dataclass, field
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class EntityRecord:
    """实体记录，用于解析索引"""
    entity_id: str  # 实体主ID
    canonical_name: str  # 规范化名称
    aliases: List[str] = field(default_factory=list)  # 别名列表
    embedding: Optional[List[float]] = None  # 实体名称的嵌入向量
    entity_type: Optional[str] = None  # 实体类型
    metadata: Dict[str, Any] = field(default_factory=dict)  # 其他元数据
    
    def get_all_names(self) -> List[str]:
        """获取所有名称（包括规范化名称和别名）"""
        names = [self.canonical_name]
        names.extend(self.aliases)
        return names
    
    def add_alias(self, alias: str) -> bool:
        """添加别名，如果不存在则添加"""
        if alias not in self.aliases and alias != self.canonical_name:
            self.aliases.append(alias)
            return True
        return False


class EntityLibrary:
    """实体解析用派生索引"""
    
    def __init__(self, embed_func=None, data_path: Optional[str] = None):
        """
        初始化实体库
        
        Args:
            embed_func: 嵌入向量生成函数，接收文本返回嵌入向量列表
            data_path: 实体库数据文件路径，如果提供则从该路径加载数据
        """
        self.entities: Dict[str, EntityRecord] = {}  # entity_id -> EntityRecord
        self.name_to_entity: Dict[str, str] = {}  # 名称（包括别名）-> entity_id
        self.embeddings: Dict[str, List[float]] = {}  # entity_id -> embedding
        self.embed_func = embed_func  # 嵌入向量生成函数
        self.last_rebuild_time: float = 0.0
        
        logger.info("初始化 EntityLibrary")
        
        # 如果提供了数据路径，则尝试从该路径加载数据
        if data_path:
            self.load_from_path(data_path)
    
    def load_from_path(self, data_path: str) -> bool:
        """
        从指定路径加载实体库数据
        
        支持两种数据格式：
        1. 单个JSON文件（包含实体数据）
        2. 目录（包含多个JSON文件，每个文件对应一个实体）
        
        Args:
            data_path: 数据文件或目录路径
            
        Returns:
            是否成功加载
        """
        logger.info(f"开始从路径加载实体库数据: {data_path}")
        
        if not os.path.exists(data_path):
            logger.warning(f"数据路径不存在: {data_path}")
            return False
        
        try:
            # 清空现有索引
            self.entities.clear()
            self.name_to_entity.clear()
            self.embeddings.clear()
            
            entity_count = 0
            
            if os.path.isfile(data_path):
                # 单个文件
                if data_path.endswith('.json'):
                    success = self._load_from_json_file(data_path)
                    if success:
                        entity_count = self.get_entity_count()
                else:
                    logger.warning(f"不支持的文件格式: {data_path}")
                    return False
            elif os.path.isdir(data_path):
                # 目录，加载所有JSON文件
                json_files = [f for f in os.listdir(data_path) if f.endswith('.json')]
                if not json_files:
                    logger.warning(f"目录中没有JSON文件: {data_path}")
                    return False
                
                for json_file in json_files:
                    file_path = os.path.join(data_path, json_file)
                    self._load_from_json_file(file_path)
                
                entity_count = self.get_entity_count()
            else:
                logger.warning(f"无效的数据路径: {data_path}")
                return False
            
            self.last_rebuild_time = time.time()
            logger.info(f"从路径加载实体库数据完成，共 {entity_count} 个实体")
            return True
            
        except Exception as e:
            logger.error(f"从路径加载实体库数据失败: {e}")
            return False
    
    def save_to_path(self, data_path: str) -> bool:
        """
        将实体库数据保存到指定路径
        
        支持两种保存格式：
        1. 单个JSON文件（包含所有实体数据）
        2. 目录（每个实体保存为一个JSON文件）
        
        Args:
            data_path: 数据文件或目录路径
            
        Returns:
            是否成功保存
        """
        logger.info(f"开始将实体库数据保存到路径: {data_path}")
        
        if not self.entities:
            logger.warning("实体库为空，无需保存")
            return False
        
        try:
            if data_path.endswith('.json'):
                # 保存为单个JSON文件
                return self._save_to_single_json_file(data_path)
            else:
                # 保存为目录（每个实体一个文件）
                return self._save_to_directory(data_path)
                
        except Exception as e:
            logger.error(f"保存实体库数据到路径失败: {e}")
            return False
    
    def _save_to_single_json_file(self, file_path: str) -> bool:
        """
        将实体库数据保存到单个JSON文件
        
        Args:
            file_path: JSON文件路径
            
        Returns:
            是否成功保存
        """
        try:
            # 准备要保存的数据
            entities_data = []
            for entity_id, record in self.entities.items():
                entity_data = {
                    "ID": record.entity_id,
                    "name": record.canonical_name,
                    "alias_names": record.aliases,
                    "embedding": record.embedding,
                    "entity_type": record.entity_type,
                    "metadata": record.metadata
                }
                entities_data.append(entity_data)
            
            # 创建目录（如果不存在）
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            # 写入文件
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(entities_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"实体库数据保存到单个JSON文件完成: {file_path}, 共 {len(entities_data)} 个实体")
            return True
            
        except Exception as e:
            logger.error(f"保存到单个JSON文件失败 {file_path}: {e}")
            return False
    
    def _save_to_directory(self, dir_path: str) -> bool:
        """
        将实体库数据保存到目录（每个实体一个JSON文件）
        
        Args:
            dir_path: 目录路径
            
        Returns:
            是否成功保存
        """
        try:
            # 创建目录（如果不存在）
            os.makedirs(dir_path, exist_ok=True)
            
            saved_count = 0
            for entity_id, record in self.entities.items():
                # 构建文件名（使用实体ID，确保文件名安全）
                safe_entity_id = "".join(c for c in entity_id if c.isalnum() or c in ('_', '-'))
                file_name = f"{safe_entity_id}.json"
                file_path = os.path.join(dir_path, file_name)
                
                # 准备实体数据
                entity_data = {
                    "ID": record.entity_id,
                    "name": record.canonical_name,
                    "alias_names": record.aliases,
                    "embedding": record.embedding,
                    "entity_type": record.entity_type,
                    "metadata": record.metadata
                }
                
                # 写入文件
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(entity_data, f, ensure_ascii=False, indent=2)
                
                saved_count += 1
            
            logger.info(f"实体库数据保存到目录完成: {dir_path}, 共 {saved_count} 个实体文件")
            return True
            
        except Exception as e:
            logger.error(f"保存到目录失败 {dir_path}: {e}")
            return False
    
    def _load_from_json_file(self, file_path: str) -> bool:
        """
        从单个JSON文件加载实体数据
        
        Args:
            file_path: JSON文件路径
            
        Returns:
            是否成功加载
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 解析实体数据
            # 格式1: 单个实体对象（如参考数据格式）
            if isinstance(data, dict):
                entity_id = data.get('ID') or data.get('id')
                if not entity_id:
                    logger.warning(f"JSON文件中未找到实体ID: {file_path}")
                    return False
                
                # 获取规范化名称
                canonical_name = data.get('name', entity_id)
                
                # 获取别名列表
                aliases = data.get('alias_names', [])
                if not isinstance(aliases, list):
                    aliases = []
                
                # 获取embedding
                embedding = data.get('embedding')
                if embedding and not isinstance(embedding, list):
                    embedding = None
                
                # 创建实体记录
                record = EntityRecord(
                    entity_id=entity_id,
                    canonical_name=canonical_name,
                    aliases=aliases,
                    embedding=embedding,
                    entity_type=data.get('entity_type'),
                    metadata=data.get('metadata', {})
                )
                
                # 添加到索引
                self.entities[entity_id] = record
                
                # 建立名称到实体的映射
                for name in record.get_all_names():
                    if name in self.name_to_entity:
                        logger.debug(f"名称冲突: {name} 已映射到 {self.name_to_entity[name]}, 现在也映射到 {entity_id}")
                    self.name_to_entity[name] = entity_id
                
                # 存储嵌入向量
                if record.embedding:
                    self.embeddings[entity_id] = record.embedding
                
                logger.debug(f"从文件加载实体: {entity_id} ({canonical_name})")
                return True
            
            # 格式2: 实体列表
            elif isinstance(data, list):
                success_count = 0
                for entity_data in data:
                    if isinstance(entity_data, dict):
                        entity_id = entity_data.get('ID') or entity_data.get('id')
                        if not entity_id:
                            continue
                        
                        # 获取规范化名称
                        canonical_name = entity_data.get('name', entity_id)
                        
                        # 获取别名列表
                        aliases = entity_data.get('alias_names', [])
                        if not isinstance(aliases, list):
                            aliases = []
                        
                        # 获取embedding
                        embedding = entity_data.get('embedding')
                        if embedding and not isinstance(embedding, list):
                            embedding = None
                        
                        # 创建实体记录
                        record = EntityRecord(
                            entity_id=entity_id,
                            canonical_name=canonical_name,
                            aliases=aliases,
                            embedding=embedding,
                            entity_type=entity_data.get('entity_type'),
                            metadata=entity_data.get('metadata', {})
                        )
                        
                        # 添加到索引
                        self.entities[entity_id] = record
                        
                        # 建立名称到实体的映射
                        for name in record.get_all_names():
                            if name in self.name_to_entity:
                                logger.debug(f"名称冲突: {name} 已映射到 {self.name_to_entity[name]}, 现在也映射到 {entity_id}")
                            self.name_to_entity[name] = entity_id
                        
                        # 存储嵌入向量
                        if record.embedding:
                            self.embeddings[entity_id] = record.embedding
                        
                        success_count += 1
                
                logger.debug(f"从文件加载 {success_count} 个实体: {file_path}")
                return success_count > 0
            
            else:
                logger.warning(f"不支持的JSON格式: {file_path}")
                return False
                
        except Exception as e:
            logger.error(f"加载JSON文件失败 {file_path}: {e}")
            return False
    
    def rebuild_from_kg(self, kg_data: Dict[str, Any]) -> bool:
        """
        从 KG 数据重建实体派生索引
        
        只获取实体ID列表，不处理别名和embedding（别名列表初始为空，embedding通过API初始化）
        
        Args:
            kg_data: KG 数据，期望包含实体列表
            
        Returns:
            是否成功重建
        """
        logger.info("开始从 KG 重建实体派生索引")
        
        try:
            # 清空现有索引
            self.entities.clear()
            self.name_to_entity.clear()
            self.embeddings.clear()
            
            # 解析 KG 数据
            entities = kg_data.get('entities', [])
            if not entities:
                logger.warning("KG 数据中未找到实体列表")
                return False
            
            entity_count = 0
            for entity_data in entities:
                try:
                    entity_id = entity_data.get('id')
                    if not entity_id:
                        continue
                    
                    # 获取规范化名称（优先使用 name 字段，否则使用 id）
                    canonical_name = entity_data.get('name', entity_id)
                    
                    # 创建实体记录（别名列表初始为空，embedding为None）
                    record = EntityRecord(
                        entity_id=entity_id,
                        canonical_name=canonical_name,
                        aliases=[],  # 别名列表初始为空
                        embedding=None,  # embedding通过API初始化
                        entity_type=entity_data.get('type'),
                        metadata=entity_data.get('metadata', {})
                    )
                    
                    # 添加到索引
                    self.entities[entity_id] = record
                    
                    # 只建立规范化名称到实体的映射（不处理别名）
                    if canonical_name in self.name_to_entity:
                        logger.debug(f"名称冲突: {canonical_name} 已映射到 {self.name_to_entity[canonical_name]}, 现在也映射到 {entity_id}")
                    self.name_to_entity[canonical_name] = entity_id
                    
                    entity_count += 1
                    
                except Exception as e:
                    logger.warning(f"处理实体数据失败: {entity_data}, 错误: {e}")
                    continue
            
            self.last_rebuild_time = time.time()
            logger.info(f"从 KG 重建实体派生索引完成，共 {entity_count} 个实体")
            logger.info(f"注意：别名列表初始为空，embedding需要通过API初始化")
            return True
            
        except Exception as e:
            logger.error(f"从 KG 重建实体派生索引失败: {e}")
            return False
    
    def rebuild_from_kg_base(self, kg_base: Any) -> bool:
        """
        从 KGBase 实例重建实体派生索引
        
        使用 KGBase 的 list_entity_ids() 方法获取实体ID列表
        
        Args:
            kg_base: KGBase 实例
            
        Returns:
            是否成功重建
        """
        logger.info("开始从 KGBase 重建实体派生索引")
        
        try:
            # 清空现有索引
            self.entities.clear()
            self.name_to_entity.clear()
            self.embeddings.clear()
            
            # 使用 KGBase 获取实体ID列表
            try:
                entity_ids = kg_base.list_entity_ids()
            except AttributeError:
                logger.error("KGBase 实例没有 list_entity_ids() 方法")
                return False
            except Exception as e:
                logger.error(f"调用 KGBase.list_entity_ids() 失败: {e}")
                return False
            
            if not entity_ids:
                logger.warning("KGBase 返回空实体ID列表")
                return False
            
            entity_count = 0
            for entity_id in entity_ids:
                try:
                    # 获取规范化名称（使用实体ID作为名称）
                    canonical_name = entity_id
                    
                    # 创建实体记录（别名列表初始为空，embedding为None）
                    record = EntityRecord(
                        entity_id=entity_id,
                        canonical_name=canonical_name,
                        aliases=[],  # 别名列表初始为空
                        embedding=None,  # embedding通过API初始化
                        entity_type=None,  # 可以扩展为从KGBase获取实体类型
                        metadata={}  # 可以扩展为从KGBase获取元数据
                    )
                    
                    # 添加到索引
                    self.entities[entity_id] = record
                    
                    # 只建立规范化名称到实体的映射（不处理别名）
                    if canonical_name in self.name_to_entity:
                        logger.debug(f"名称冲突: {canonical_name} 已映射到 {self.name_to_entity[canonical_name]}, 现在也映射到 {entity_id}")
                    self.name_to_entity[canonical_name] = entity_id
                    
                    entity_count += 1
                    
                except Exception as e:
                    logger.warning(f"处理实体失败 {entity_id}: {e}")
                    continue
            
            self.last_rebuild_time = time.time()
            logger.info(f"从 KGBase 重建实体派生索引完成，共 {entity_count} 个实体")
            logger.info(f"注意：别名列表初始为空，embedding需要通过API初始化")
            return True
            
        except Exception as e:
            logger.error(f"从 KGBase 重建实体派生索引失败: {e}")
            return False
    
    def search(self, query: str, max_results: int = 10) -> List[Tuple[str, float, str]]:
        """
        搜索实体候选
        
        Args:
            query: 查询字符串（实体名称或别名）
            max_results: 最大返回结果数
            
        Returns:
            候选实体列表，每个元素为 (entity_id, 相似度分数, 匹配名称)
        """
        results = []
        
        # 1. 精确匹配（名称完全一致）
        if query in self.name_to_entity:
            entity_id = self.name_to_entity[query]
            results.append((entity_id, 1.0, query))
        
        # 2. 模糊匹配（包含关系）
        for name, entity_id in self.name_to_entity.items():
            if query.lower() in name.lower() and name != query:
                # 计算相似度分数（简单基于包含关系）
                score = len(query) / len(name) if name else 0.0
                results.append((entity_id, score, name))
        
        # 3. 按分数排序并限制结果数
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:max_results]
    
    def search_by_embedding(
        self, 
        embedding: List[float], 
        threshold: float = 0.7,
        top_k: int = 5
    ) -> List[Tuple[str, float]]:
        """
        基于嵌入向量搜索相似实体
        
        Args:
            embedding: 查询嵌入向量
            threshold: 相似度阈值
            top_k: 返回前K个结果
            
        Returns:
            相似实体列表，每个元素为 (entity_id, 相似度)
        """
        if not self.embeddings:
            return []
        
        similarities = []
        for entity_id, entity_embedding in self.embeddings.items():
            if not entity_embedding:
                continue
            
            # 计算余弦相似度
            similarity = self._cosine_similarity(embedding, entity_embedding)
            if similarity >= threshold:
                similarities.append((entity_id, similarity))
        
        # 按相似度降序排序
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:top_k]
    
    def get_entity(self, entity_id: str) -> Optional[EntityRecord]:
        """获取实体记录"""
        return self.entities.get(entity_id)
    
    def entity_exists(self, entity_id: str) -> bool:
        """检查实体是否存在"""
        return entity_id in self.entities
    
    def name_exists(self, name: str) -> bool:
        """检查名称（包括别名）是否存在"""
        return name in self.name_to_entity
    
    def get_entity_by_name(self, name: str) -> Optional[EntityRecord]:
        """通过名称获取实体记录"""
        entity_id = self.name_to_entity.get(name)
        if entity_id:
            return self.entities.get(entity_id)
        return None
    
    def init_entity_embedding(self, entity_id: str) -> bool:
        """
        初始化实体的embedding
        
        使用embed_func生成实体的embedding并存储
        
        Args:
            entity_id: 实体ID
            
        Returns:
            是否成功初始化
        """
        if entity_id not in self.entities:
            logger.warning(f"实体不存在: {entity_id}")
            return False
        
        if not self.embed_func:
            logger.warning(f"未提供embed_func，无法生成embedding: {entity_id}")
            return False
        
        try:
            record = self.entities[entity_id]
            # 使用规范化名称生成embedding
            embedding = self.embed_func(record.canonical_name)
            if embedding:
                record.embedding = embedding
                self.embeddings[entity_id] = embedding
                logger.info(f"初始化实体embedding成功: {entity_id}")
                return True
            else:
                logger.warning(f"生成embedding失败: {entity_id}")
                return False
        except Exception as e:
            logger.error(f"初始化实体embedding失败 {entity_id}: {e}")
            return False
    
    def init_all_embeddings(self) -> Dict[str, bool]:
        """
        初始化所有实体的embedding
        
        Returns:
            每个实体的初始化结果字典
        """
        if not self.embed_func:
            logger.warning("未提供embed_func，无法生成embedding")
            return {}
        
        results = {}
        for entity_id in self.entities:
            results[entity_id] = self.init_entity_embedding(entity_id)
        
        success_count = sum(1 for success in results.values() if success)
        logger.info(f"初始化所有实体embedding完成，成功 {success_count}/{len(results)} 个")
        return results
    
    def add_entity(self, entity_id: str, canonical_name: str, **kwargs) -> bool:
        """
        添加新实体到索引（用于运行时新增实体）
        
        Args:
            entity_id: 实体ID
            canonical_name: 规范化名称
            **kwargs: 其他参数（aliases, embedding, entity_type, metadata）
            
        Returns:
            是否成功添加
        """
        if entity_id in self.entities:
            logger.warning(f"实体已存在: {entity_id}")
            return False
        
        # 创建实体记录
        record = EntityRecord(
            entity_id=entity_id,
            canonical_name=canonical_name,
            aliases=kwargs.get('aliases', []),
            embedding=kwargs.get('embedding'),
            entity_type=kwargs.get('entity_type'),
            metadata=kwargs.get('metadata', {})
        )
        
        # 添加到索引
        self.entities[entity_id] = record
        
        # 建立名称到实体的映射
        for name in record.get_all_names():
            if name in self.name_to_entity:
                logger.debug(f"名称冲突: {name} 已映射到 {self.name_to_entity[name]}, 现在也映射到 {entity_id}")
            self.name_to_entity[name] = entity_id
        
        # 存储嵌入向量
        if record.embedding:
            self.embeddings[entity_id] = record.embedding
        
        logger.info(f"添加新实体到索引: {entity_id} ({canonical_name})")
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
            logger.warning(f"实体不存在: {entity_id}")
            return False
        
        if alias in self.name_to_entity:
            logger.warning(f"别名已存在: {alias} -> {self.name_to_entity[alias]}")
            return False
        
        # 添加别名
        record = self.entities[entity_id]
        if record.add_alias(alias):
            self.name_to_entity[alias] = entity_id
            logger.info(f"为实体 {entity_id} 添加别名: {alias}")
            return True
        
        return False
    
    def get_all_entities(self) -> List[EntityRecord]:
        """获取所有实体记录"""
        return list(self.entities.values())
    
    def get_entity_count(self) -> int:
        """获取实体数量"""
        return len(self.entities)
    
    def get_alias_count(self) -> int:
        """获取别名总数"""
        total = 0
        for record in self.entities.values():
            total += len(record.aliases)
        return total
    
    def clear(self) -> None:
        """清空索引"""
        self.entities.clear()
        self.name_to_entity.clear()
        self.embeddings.clear()
        self.last_rebuild_time = 0.0
        logger.info("清空 EntityLibrary")
    
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
    
    def get_stats(self) -> Dict[str, Any]:
        """获取索引统计信息"""
        return {
            "entity_count": len(self.entities),
            "alias_count": self.get_alias_count(),
            "name_mapping_count": len(self.name_to_entity),
            "embedding_count": len(self.embeddings),
            "last_rebuild_time": self.last_rebuild_time,
            "last_rebuild_human": time.ctime(self.last_rebuild_time) if self.last_rebuild_time else "Never"
        }
    
    def __str__(self) -> str:
        """字符串表示"""
        stats = self.get_stats()
        return f"EntityLibrary(entities={stats['entity_count']}, aliases={stats['alias_count']})"