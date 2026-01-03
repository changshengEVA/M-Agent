#!/usr/bin/env python3
"""
知识图谱数据加载模块
负责加载和解析 KG 候选 JSON 文件
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

class Entity:
    """实体类"""
    def __init__(self, entity_id: str, entity_type: str, confidence: float, scenes: List[str]):
        self.id = entity_id
        self.type = entity_type
        self.confidence = confidence
        self.scenes = scenes  # 包含该实体的scene列表
    
    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "confidence": self.confidence,
            "scenes": self.scenes
        }

class Relation:
    """关系类"""
    def __init__(self, subject: str, relation: str, object: str, confidence: float, scene_id: str):
        self.subject = subject
        self.relation = relation
        self.object = object
        self.confidence = confidence
        self.scene_id = scene_id
    
    def to_dict(self):
        return {
            "subject": self.subject,
            "relation": self.relation,
            "object": self.object,
            "confidence": self.confidence,
            "scene_id": self.scene_id
        }

class Scene:
    """Scene类"""
    def __init__(self, scene_id: str, user_id: str, generated_at: str, prompt_version: str):
        self.scene_id = scene_id
        self.user_id = user_id
        self.generated_at = generated_at
        self.prompt_version = prompt_version
    
    def to_dict(self):
        return {
            "scene_id": self.scene_id,
            "user_id": self.user_id,
            "generated_at": self.generated_at,
            "prompt_version": self.prompt_version
        }

class KGDataLoader:
    """知识图谱数据加载器"""
    
    def __init__(self, data_dir: str = None):
        """
        初始化数据加载器
        
        Args:
            data_dir: KG候选数据目录路径，如果为None则使用默认路径
        """
        if data_dir is None:
            # 默认路径：相对于项目根目录（f:/AI/M-Agent）
            import os
            # 获取项目根目录（f:/AI/M-Agent）
            # 当前文件在 f:/AI/M-Agent/kg_visualization/backend/data_loader.py
            # 所以项目根目录是父目录的父目录
            current_file = os.path.abspath(__file__)
            backend_dir = os.path.dirname(current_file)
            kg_viz_dir = os.path.dirname(backend_dir)
            project_root = os.path.dirname(kg_viz_dir)  # f:/AI/M-Agent
            data_dir = os.path.join(project_root, "data", "memory", "kg_candidates", "strong")
        
        self.data_dir = Path(data_dir)
        self.entities: Dict[str, Entity] = {}  # id -> Entity
        self.relations: List[Relation] = []
        self.scenes: Dict[str, Scene] = {}
        
    def load_all_data(self) -> Dict:
        """
        加载所有数据文件
        
        Returns:
            包含统计信息的字典
        """
        if not self.data_dir.exists():
            logger.error(f"数据目录不存在: {self.data_dir}")
            return {"error": "数据目录不存在"}
        
        # 清空现有数据
        self.entities.clear()
        self.relations.clear()
        self.scenes.clear()
        
        # 查找所有JSON文件
        json_files = list(self.data_dir.glob("*.kg_candidate.json"))
        logger.info(f"找到 {len(json_files)} 个KG候选文件")
        
        for file_path in json_files:
            self._load_single_file(file_path)
        
        # 统计信息
        stats = {
            "total_scenes": len(self.scenes),
            "total_entities": len(self.entities),
            "total_relations": len(self.relations),
            "entity_types": self._get_entity_type_distribution(),
            "relation_types": self._get_relation_type_distribution(),
            "loaded_at": datetime.now().isoformat()
        }
        
        logger.info(f"数据加载完成: {stats['total_entities']} 个实体, {stats['total_relations']} 个关系")
        return stats
    
    def _load_single_file(self, file_path: Path):
        """加载单个文件"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            scene_id = data.get("scene_id", file_path.stem)
            user_id = data.get("user_id", "unknown")
            generated_at = data.get("generated_at", "")
            prompt_version = data.get("prompt_version", "")
            
            # 保存scene信息
            self.scenes[scene_id] = Scene(scene_id, user_id, generated_at, prompt_version)
            
            # 处理实体
            facts = data.get("facts", {})
            entities = facts.get("entities", [])
            for entity_data in entities:
                entity_id = entity_data.get("id")
                entity_type = entity_data.get("type", "unknown")
                confidence = entity_data.get("confidence", 0.0)
                
                if entity_id:
                    if entity_id in self.entities:
                        # 更新现有实体，添加scene到列表
                        if scene_id not in self.entities[entity_id].scenes:
                            self.entities[entity_id].scenes.append(scene_id)
                        # 更新置信度为最高值
                        if confidence > self.entities[entity_id].confidence:
                            self.entities[entity_id].confidence = confidence
                    else:
                        # 创建新实体
                        self.entities[entity_id] = Entity(
                            entity_id=entity_id,
                            entity_type=entity_type,
                            confidence=confidence,
                            scenes=[scene_id]
                        )
            
            # 处理关系
            relations = facts.get("relations", [])
            for rel_data in relations:
                subject = rel_data.get("subject")
                relation = rel_data.get("relation")
                obj = rel_data.get("object")
                confidence = rel_data.get("confidence", 0.0)
                
                if subject and relation and obj:
                    self.relations.append(Relation(
                        subject=subject,
                        relation=relation,
                        object=obj,
                        confidence=confidence,
                        scene_id=scene_id
                    ))
                    
        except Exception as e:
            logger.error(f"加载文件失败 {file_path}: {e}")
    
    def _get_entity_type_distribution(self) -> Dict[str, int]:
        """获取实体类型分布"""
        distribution = {}
        for entity in self.entities.values():
            entity_type = entity.type
            distribution[entity_type] = distribution.get(entity_type, 0) + 1
        return distribution
    
    def _get_relation_type_distribution(self) -> Dict[str, int]:
        """获取关系类型分布"""
        distribution = {}
        for relation in self.relations:
            rel_type = relation.relation
            distribution[rel_type] = distribution.get(rel_type, 0) + 1
        return distribution
    
    def get_all_entities(self) -> List[Dict]:
        """获取所有实体"""
        return [entity.to_dict() for entity in self.entities.values()]
    
    def get_all_relations(self) -> List[Dict]:
        """获取所有关系"""
        return [relation.to_dict() for relation in self.relations]
    
    def get_all_scenes(self) -> List[Dict]:
        """获取所有scene"""
        return [scene.to_dict() for scene in self.scenes.values()]
    
    def get_entity_by_id(self, entity_id: str) -> Optional[Dict]:
        """根据ID获取实体"""
        entity = self.entities.get(entity_id)
        return entity.to_dict() if entity else None
    
    def get_relations_for_entity(self, entity_id: str) -> List[Dict]:
        """获取与实体相关的关系"""
        result = []
        for relation in self.relations:
            if relation.subject == entity_id or relation.object == entity_id:
                result.append(relation.to_dict())
        return result
    
    def get_graph_data(self) -> Dict:
        """获取图数据格式（用于前端可视化）"""
        nodes = []
        edges = []
        
        # 节点
        for entity in self.entities.values():
            nodes.append({
                "id": entity.id,
                "label": entity.id,
                "type": entity.type,
                "confidence": entity.confidence,
                "title": f"类型: {entity.type}<br>置信度: {entity.confidence}<br>出现在 {len(entity.scenes)} 个scenes"
            })
        
        # 边
        for i, relation in enumerate(self.relations):
            edges.append({
                "id": f"edge_{i}",
                "from": relation.subject,
                "to": relation.object,
                "label": relation.relation,
                "confidence": relation.confidence,
                "title": f"关系: {relation.relation}<br>置信度: {relation.confidence}<br>Scene: {relation.scene_id}"
            })
        
        return {
            "nodes": nodes,
            "edges": edges
        }