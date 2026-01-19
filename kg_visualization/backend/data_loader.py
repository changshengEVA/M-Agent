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
    def __init__(self, entity_id: str, entity_type: str, confidence: float, scenes: List[str], attributes: List[Dict] = None):
        self.id = entity_id
        self.type = entity_type
        self.confidence = confidence
        self.scenes = scenes  # 包含该实体的scene列表
        self.attributes = attributes or []  # 实体属性列表
    
    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "confidence": self.confidence,
            "scenes": self.scenes,
            "attributes": self.attributes
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
    
    def __init__(self, data_dir: str = None, memory_id: str = "test"):
        """
        初始化数据加载器
        
        Args:
            data_dir: KG数据目录路径，如果为None则使用默认路径
            memory_id: memory目录下的ID，默认为"test"
        """
        if data_dir is None:
            # 默认路径：相对于项目根目录（f:/AI/M-Agent）
            import os
            # 获取项目根目录（f:/AI/M-Agent）
            current_file = os.path.abspath(__file__)
            backend_dir = os.path.dirname(current_file)
            kg_viz_dir = os.path.dirname(backend_dir)
            project_root = os.path.dirname(kg_viz_dir)  # f:/AI/M-Agent
            data_dir = os.path.join(project_root, "data", "memory", memory_id, "kg_data")
        
        self.data_dir = Path(data_dir)
        self.entities: Dict[str, Entity] = {}  # id -> Entity
        self.relations: List[Relation] = []
        # 新格式没有scene信息，但为了兼容性保留空字典
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
        
        # 加载实体文件
        entity_dir = self.data_dir / "entity"
        if entity_dir.exists():
            entity_files = list(entity_dir.glob("*.json"))
            logger.info(f"找到 {len(entity_files)} 个实体文件")
            for file_path in entity_files:
                self._load_entity_file(file_path)
        else:
            logger.warning(f"实体目录不存在: {entity_dir}")
        
        # 加载关系文件
        relation_dir = self.data_dir / "relation"
        if relation_dir.exists():
            relation_files = list(relation_dir.glob("*.json"))
            logger.info(f"找到 {len(relation_files)} 个关系文件")
            for file_path in relation_files:
                self._load_relation_file(file_path)
        else:
            logger.warning(f"关系目录不存在: {relation_dir}")
        
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
    
    def _load_entity_file(self, file_path: Path):
        """加载实体文件"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            entity_id = data.get("id")
            if not entity_id:
                # 尝试从文件名获取ID
                entity_id = file_path.stem
            
            entity_type = data.get("type", "unknown")
            confidence = data.get("confidence", 0.0)
            attributes = data.get("attributes", [])
            
            if entity_id:
                # 新格式没有scene信息，使用空列表
                self.entities[entity_id] = Entity(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    confidence=confidence,
                    scenes=[],  # 空列表，因为没有scene信息
                    attributes=attributes  # 添加attributes字段
                )
                
        except Exception as e:
            logger.error(f"加载实体文件失败 {file_path}: {e}")
    
    def _load_relation_file(self, file_path: Path):
        """加载关系文件"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            subject = data.get("subject")
            relation = data.get("relation")
            obj = data.get("object")
            confidence = data.get("confidence", 0.0)
            
            if subject and relation and obj:
                # 新格式没有scene_id，使用空字符串
                self.relations.append(Relation(
                    subject=subject,
                    relation=relation,
                    object=obj,
                    confidence=confidence,
                    scene_id=""  # 空字符串，因为没有scene信息
                ))
                
        except Exception as e:
            logger.error(f"加载关系文件失败 {file_path}: {e}")
    
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
            # 构建属性文本
            attributes_text = ""
            if entity.attributes:
                attributes_text = "<br>属性: "
                for attr in entity.attributes[:3]:  # 只显示前3个属性
                    attributes_text += f"{attr.get('field', '')}: {attr.get('value', '')}; "
                if len(entity.attributes) > 3:
                    attributes_text += f"... (共{len(entity.attributes)}个)"
            
            nodes.append({
                "id": entity.id,
                "label": entity.id,
                "type": entity.type,
                "confidence": entity.confidence,
                "attributes": entity.attributes,  # 添加attributes字段
                "scenes": entity.scenes,  # 添加scenes字段
                "title": f"类型: {entity.type}<br>置信度: {entity.confidence}<br>出现在 {len(entity.scenes)} 个scenes{attributes_text}"
            })
        
        # 边
        for i, relation in enumerate(self.relations):
            edges.append({
                "id": f"edge_{i}",
                "from": relation.subject,
                "to": relation.object,
                "label": relation.relation,
                "confidence": relation.confidence,
                "scene_id": relation.scene_id,  # 添加scene_id字段
                "title": f"关系: {relation.relation}<br>置信度: {relation.confidence}"
            })
        
        return {
            "nodes": nodes,
            "edges": edges
        }