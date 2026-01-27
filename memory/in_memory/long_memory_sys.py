#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
长期记忆管理系统

提供四个主要接口：
1. write_kg_facts(facts_json): 向 data/memory/{id}/kg_data 写入新的KG信息
2. write_scene(scene_json): 保留为空接口
3. recall_scene(query, top_k): 保留为空接口
4. query_kg(pattern): 保留为空接口

系统从 data/memory/{id}/kg_candidates/ 读取候选文件，将实体和关系信息分别存储到
data/memory/{id}/kg_data/entity/ 和 data/memory/{id}/kg_data/relation/ 目录。
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Union

# 导入工具函数
from memory.in_memory.utils.KG_utils import (
    save_entity,
    save_relation,
    save_attribute,
    save_feature,
    delete_kg_candidate_file,
    update_episode_kg_availability
)
from memory.in_memory.utils.sys_utils import load_kg
from memory.in_memory.utils import scene_utils
# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class LongMemorySystem:
    """长期记忆管理系统"""
    
    def __init__(self, memory_id: str = "default", base_path: str = "data/memory"):
        """
        初始化长期记忆系统
        
        Args:
            memory_id: 记忆ID，对应 data/memory/{id} 目录
            base_path: 基础路径，默认为 "data/memory"
        """
        self.memory_id = memory_id
        self.base_path = Path(base_path)
        self.memory_root = self.base_path / memory_id
        
        # 创建必要的目录结构
        self.kg_candidates_dir = self.memory_root / "kg_candidates"
        self.kg_data_dir = self.memory_root / "kg_data"
        self.kg_entity_dir = self.kg_data_dir / "entity"
        self.kg_relation_dir = self.kg_data_dir / "relation"
        self.scenes_dir = self.memory_root / "scenes_data"
        
        # 确保目录存在
        self._ensure_directories()
        
        # 加载现有的KG数据
        self.kg_data = load_kg(self.kg_data_dir)
        if self.kg_data["success"]:
            self.entities = self.kg_data.get("entities", [])
            self.relations = self.kg_data.get("relations", [])
            self.attributes = self.kg_data.get("attributes", [])
            self.kg_stats = self.kg_data.get("stats", {})
            logger.info(f"KG数据加载成功: {self.kg_stats}")
        else:
            self.entities = []
            self.relations = []
            self.attributes = []
            self.kg_stats = {}
            logger.warning(f"KG数据加载失败: {self.kg_data.get('error', '未知错误')}")
        
        logger.info(f"初始化长期记忆系统，记忆ID: {memory_id}")
        logger.info(f"KG候选目录: {self.kg_candidates_dir}")
        logger.info(f"KG数据目录: {self.kg_data_dir}")
        logger.info(f"场景目录: {self.scenes_dir}")
    
    def _ensure_directories(self):
        """确保所有必要的目录都存在"""
        self.kg_candidates_dir.mkdir(parents=True, exist_ok=True)
        self.kg_data_dir.mkdir(parents=True, exist_ok=True)
        self.kg_entity_dir.mkdir(parents=True, exist_ok=True)
        self.kg_relation_dir.mkdir(parents=True, exist_ok=True)
        self.scenes_dir.mkdir(parents=True, exist_ok=True)
    
    def _refresh_kg_data(self):
        """刷新内存中的KG数据（重新加载）"""
        self.kg_data = load_kg(self.kg_data_dir)
        if self.kg_data["success"]:
            self.entities = self.kg_data.get("entities", [])
            self.relations = self.kg_data.get("relations", [])
            self.attributes = self.kg_data.get("attributes", [])
            self.kg_stats = self.kg_data.get("stats", {})
            logger.debug(f"KG数据刷新成功: {self.kg_stats}")
        else:
            logger.warning(f"KG数据刷新失败: {self.kg_data.get('error', '未知错误')}")
    
    def write_kg_facts(
        self,
        candidate_data: Union[str, Dict],
        source_file: Optional[Union[str, Path]] = None,
        auto_cleanup: bool = True
    ) -> Dict:
        """
        向 data/memory/{id}/kg_data 写入新的KG信息
        
        Args:
            candidate_data: 完整的KG候选数据（JSON字符串或字典），包含 dialogue_id, episode_id, kg_candidate 等字段
            source_file: 可选的源文件路径（KG候选文件），如果提供且auto_cleanup为True，
                         则处理完成后删除该文件（注意：不再更新kg_available字段，由episode_situation.json控制）
            auto_cleanup: 是否自动清理源文件，默认为True
            
        Returns:
            包含处理结果的字典
        """
        logger.info("开始写入KG事实")
        
        # 解析输入
        if isinstance(candidate_data, str):
            try:
                full_data = json.loads(candidate_data)
            except json.JSONDecodeError as e:
                error_msg = f"解析JSON字符串失败: {e}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
        else:
            full_data = candidate_data
        
        # 提取来源信息
        dialogue_id = full_data.get('dialogue_id')
        episode_id = full_data.get('episode_id')
        
        # 获取scene_id
        scene_id = None
        if dialogue_id and episode_id:
            try:
                # 构建episode_key
                episode_key = f"{dialogue_id}:{episode_id}"
                
                # 加载episode_situation.json
                episode_situation_file = self.memory_root / "episodes" / "episode_situation.json"
                if episode_situation_file.exists():
                    with open(episode_situation_file, 'r', encoding='utf-8') as f:
                        episode_situation_data = json.load(f)
                    
                    # 查找对应的episode
                    episode_info = episode_situation_data.get('episodes', {}).get(episode_key)
                    if episode_info:
                        scene_file = episode_info.get('scene_file')
                        if scene_file:
                            # 加载scene文件获取scene_id
                            scene_file_path = self.memory_root / "scene" / scene_file
                            if scene_file_path.exists():
                                with open(scene_file_path, 'r', encoding='utf-8') as f:
                                    scene_data = json.load(f)
                                scene_id = scene_data.get('scene_id')
                                logger.info(f"找到scene_id: {scene_id} (来自scene文件: {scene_file})")
                            else:
                                logger.warning(f"scene文件不存在: {scene_file_path}")
                        else:
                            logger.warning(f"episode {episode_key} 没有scene_file字段")
                    else:
                        logger.warning(f"在episode_situation.json中找不到episode: {episode_key}")
                else:
                    logger.warning(f"episode_situation.json文件不存在: {episode_situation_file}")
            except Exception as e:
                logger.error(f"获取scene_id失败: {e}")
        
        # 构建来源信息
        source_info = None
        if dialogue_id and episode_id:
            source_info = {
                'dialogue_id': dialogue_id,
                'episode_id': episode_id,
                'scene_id': scene_id,
                'file_number': full_data.get('file_number'),
                'generated_at': full_data.get('generated_at'),
                'prompt_version': full_data.get('prompt_version'),
                'prompt_key': full_data.get('prompt_key')
            }
        else:
            logger.warning("候选数据缺少 dialogue_id 或 episode_id 字段，将不记录来源信息")
        
        # 提取kg_candidate数据
        kg_candidate = full_data.get('kg_candidate', {})
        if not kg_candidate:
            error_msg = "输入数据缺少 'kg_candidate' 字段"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        
        # 提取facts数据
        facts = kg_candidate.get('facts', {})
        if not facts:
            error_msg = "kg_candidate数据缺少 'facts' 字段"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        
        # 统计信息
        stats = {
            "entities_processed": 0,
            "entities_saved": 0,
            "relations_processed": 0,
            "relations_saved": 0,
            "attributes_processed": 0,
            "attributes_saved": 0,
            "features_processed": 0,
            "features_saved": 0
        }
        
        # 处理实体
        entities = facts.get('entities', [])
        for entity in entities:
            stats["entities_processed"] += 1
            if save_entity(entity, self.kg_entity_dir, source_info):
                stats["entities_saved"] += 1
        
        # 处理关系
        relations = facts.get('relations', [])
        for relation in relations:
            stats["relations_processed"] += 1
            if save_relation(relation, self.kg_relation_dir, source_info):
                stats["relations_saved"] += 1
        
        # 处理属性
        attributes = facts.get('attributes', [])
        for attribute in attributes:
            stats["attributes_processed"] += 1
            if save_attribute(attribute, self.kg_entity_dir, source_info):
                stats["attributes_saved"] += 1
        
        # 处理特征
        features = facts.get('features', [])
        for feature in features:
            stats["features_processed"] += 1
            if save_feature(feature, self.kg_entity_dir, source_info):
                stats["features_saved"] += 1
        
        # 返回结果
        result = {
            "success": True,
            "message": "KG事实写入完成",
            "stats": stats,
            "memory_id": self.memory_id,
            "kg_data_dir": str(self.kg_data_dir),
            "source_info": source_info
        }
        
        logger.info(f"KG事实写入完成: {stats}")
        
        # 刷新内存中的KG数据以保持一致性
        self._refresh_kg_data()
        return result
    
    def write_scene(self, scene_json: Union[str, Dict]) -> Dict:
        """
        写入场景信息，并将theme编码到FAISS索引
        
        Args:
            scene_json: 场景信息的JSON字符串或字典，应包含scene_id, theme, diary等字段
            
        Returns:
            包含处理结果的字典
        """
        logger.info("开始写入场景信息")
        
        # 解析输入
        if isinstance(scene_json, str):
            try:
                scene_data = json.loads(scene_json)
            except json.JSONDecodeError as e:
                error_msg = f"解析JSON字符串失败: {e}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
        else:
            scene_data = scene_json
        
        # 验证必要字段
        scene_id = scene_data.get('scene_id')
        theme = scene_data.get('theme', '')
        diary = scene_data.get('diary', '')
        
        if not scene_id:
            error_msg = "scene_data缺少'scene_id'字段"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        
        # 调用scene_utils保存场景
        try:
            result = scene_utils.save_scene(scene_data, self.scenes_dir)
            
            # 确保结果格式与现有接口兼容
            if result.get("success"):
                return {
                    "success": True,
                    "message": "场景写入完成",
                    "scene_id": scene_id,
                    "scene_file": result.get("scene_file", ""),
                    "faiss_indexed": result.get("faiss_indexed", False),
                    "faiss_message": result.get("faiss_message", ""),
                    "memory_id": self.memory_id,
                    "scenes_dir": str(self.scenes_dir)
                }
            else:
                return {
                    "success": False,
                    "error": result.get("message", "未知错误"),
                    "scene_id": scene_id,
                    "memory_id": self.memory_id
                }
                
        except Exception as e:
            error_msg = f"写入场景失败: {e}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
    
    def recall_scene(self, query: str, top_k: int = 5) -> Dict:
        """
        回忆场景：按照diary进行召回
        
        Args:
            query: 查询字符串（diary文本）
            top_k: 返回结果的数量
            
        Returns:
            包含查询结果的字典
        """
        logger.info(f"开始回忆场景: query='{query}', top_k={top_k}")
        
        if not query or not query.strip():
            return {
                "success": False,
                "error": "查询字符串不能为空",
                "query": query,
                "top_k": top_k,
                "results": []
            }
        
        # 调用scene_utils搜索场景
        try:
            search_results = scene_utils.search_scenes(query, self.scenes_dir, top_k)
            
            # 转换结果为兼容格式
            formatted_results = []
            for result in search_results:
                scene_data = result.get("scene_data", {})
                formatted_results.append({
                    "scene_id": scene_data.get("scene_id", ""),
                    "theme": scene_data.get("theme", ""),
                    "diary": scene_data.get("diary", ""),
                    "similarity": result.get("similarity", 0.0),
                    "rank": result.get("rank", 0),
                    "metadata": scene_data.get("meta", {}),
                    "search_metadata": result.get("search_metadata", {})
                })
            
            return {
                "success": True,
                "message": f"找到 {len(formatted_results)} 个匹配的场景",
                "query": query,
                "top_k": top_k,
                "results": formatted_results,
                "results_count": len(formatted_results),
                "memory_id": self.memory_id,
                "scenes_dir": str(self.scenes_dir)
            }
            
        except Exception as e:
            error_msg = f"回忆场景失败: {e}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "query": query,
                "top_k": top_k,
                "results": []
            }
    
    def query_kg(self, pattern: Union[str, Dict]) -> Dict:
        """
        查询知识图谱（保留为空接口）
        
        Args:
            pattern: 查询模式（字符串或字典）
            
        Returns:
            包含查询结果的字典
        """
        logger.info(f"query_kg接口（保留为空）被调用: pattern={pattern}")
        return {
            "success": True,
            "message": "query_kg接口目前为空实现",
            "pattern": pattern,
            "results": [],
            "note": "此接口保留为未来扩展"
        }
    
