#!/usr/bin/env python3
"""
增强版知识图谱数据加载模块
支持实体-特征-场景三层数据结构
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)

class Entity3D:
    """三维实体类"""
    def __init__(self, entity_id: str, entity_type: str, confidence: float, 
                 features: List[Dict], sources: List[Dict]):
        self.id = entity_id
        self.type = entity_type
        self.confidence = confidence
        self.features = features  # 特征列表
        self.sources = sources    # 来源列表
        
    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "confidence": self.confidence,
            "features": self.features,
            "sources": self.sources
        }

class FeatureNode:
    """特征节点类"""
    def __init__(self, feature_id: str, entity_id: str, feature_text: str, 
                 sources: List[Dict], confidence: float = 1.0):
        self.id = feature_id
        self.entity_id = entity_id
        self.feature_text = feature_text
        self.sources = sources
        self.confidence = confidence
        
    def to_dict(self):
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "feature": self.feature_text,
            "sources": self.sources,
            "confidence": self.confidence
        }

class SceneNode:
    """场景节点类"""
    def __init__(self, scene_id: str, dialogue_id: str, episode_id: str,
                 generated_at: str, prompt_version: str, file_number: int = None):
        self.id = scene_id
        self.dialogue_id = dialogue_id
        self.episode_id = episode_id
        self.generated_at = generated_at
        self.prompt_version = prompt_version
        self.file_number = file_number
        
    def to_dict(self):
        return {
            "id": self.id,
            "dialogue_id": self.dialogue_id,
            "episode_id": self.episode_id,
            "generated_at": self.generated_at,
            "prompt_version": self.prompt_version,
            "file_number": self.file_number
        }

class VerticalEdge:
    """垂直连接边类（实体-特征，特征-场景）"""
    def __init__(self, from_id: str, to_id: str, edge_type: str, 
                 confidence: float = 1.0, sources: List[Dict] = None):
        self.from_id = from_id
        self.to_id = to_id
        self.edge_type = edge_type  # "entity_feature" 或 "feature_scene"
        self.confidence = confidence
        self.sources = sources or []
        
    def to_dict(self):
        return {
            "from": self.from_id,
            "to": self.to_id,
            "type": self.edge_type,
            "confidence": self.confidence,
            "sources": self.sources
        }

class EnhancedKGDataLoader:
    """增强版知识图谱数据加载器（支持三层结构）"""
    
    def __init__(self, data_dir: str = None, memory_id: str = "test2"):
        """
        初始化数据加载器
        
        Args:
            data_dir: KG数据目录路径，如果为None则使用默认路径
            memory_id: memory目录下的ID，默认为"test2"
        """
        if data_dir is None:
            import os
            current_file = os.path.abspath(__file__)
            backend_dir = os.path.dirname(current_file)
            kg_viz_dir = os.path.dirname(backend_dir)
            project_root = os.path.dirname(kg_viz_dir)
            data_dir = os.path.join(project_root, "data", "memory", memory_id, "kg_data")
        
        self.data_dir = Path(data_dir)
        self.memory_id = memory_id
        
        # 三层数据结构
        self.entities: Dict[str, Entity3D] = {}  # id -> Entity3D
        self.features: Dict[str, FeatureNode] = {}  # id -> FeatureNode
        self.scenes: Dict[str, SceneNode] = {}  # id -> SceneNode
        self.horizontal_edges: List[Dict] = []  # 水平关系边（实体间）
        self.vertical_edges: List[VerticalEdge] = []  # 垂直连接边
        
        # 索引
        self.entity_to_features: Dict[str, List[str]] = defaultdict(list)  # 实体ID -> 特征ID列表
        self.feature_to_scenes: Dict[str, List[str]] = defaultdict(list)  # 特征ID -> 场景ID列表
        
    def load_all_data(self) -> Dict:
        """
        加载所有数据文件（三层结构）
        
        Returns:
            包含统计信息的字典
        """
        if not self.data_dir.exists():
            logger.error(f"数据目录不存在: {self.data_dir}")
            return {"error": "数据目录不存在"}
        
        # 清空现有数据
        self.entities.clear()
        self.features.clear()
        self.scenes.clear()
        self.horizontal_edges.clear()
        self.vertical_edges.clear()
        self.entity_to_features.clear()
        self.feature_to_scenes.clear()
        
        # 加载实体文件（包含特征和来源）
        entity_dir = self.data_dir / "entity"
        if entity_dir.exists():
            entity_files = list(entity_dir.glob("*.json"))
            logger.info(f"找到 {len(entity_files)} 个实体文件")
            for file_path in entity_files:
                self._load_entity_file_3d(file_path)
        else:
            logger.warning(f"实体目录不存在: {entity_dir}")
        
        # 加载关系文件（水平边）
        relation_dir = self.data_dir / "relation"
        if relation_dir.exists():
            relation_files = list(relation_dir.glob("*.json"))
            logger.info(f"找到 {len(relation_files)} 个关系文件")
            for file_path in relation_files:
                self._load_relation_file(file_path)
        else:
            logger.warning(f"关系目录不存在: {relation_dir}")
        
        # 构建场景节点（从特征来源中提取）
        self._build_scene_nodes()
        
        # 构建垂直连接
        self._build_vertical_edges()
        
        # 统计信息
        stats = {
            "total_entities": len(self.entities),
            "total_features": len(self.features),
            "total_scenes": len(self.scenes),
            "total_horizontal_edges": len(self.horizontal_edges),
            "total_vertical_edges": len(self.vertical_edges),
            "entity_types": self._get_entity_type_distribution(),
            "feature_distribution": self._get_feature_distribution(),
            "scene_distribution": self._get_scene_distribution(),
            "loaded_at": datetime.now().isoformat(),
            "memory_id": self.memory_id
        }
        
        logger.info(f"三层数据加载完成: {stats['total_entities']} 实体, "
                   f"{stats['total_features']} 特征, {stats['total_scenes']} 场景")
        return stats
    
    def _load_entity_file_3d(self, file_path: Path):
        """加载实体文件（三维版本）"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            entity_id = data.get("id") or file_path.stem
            entity_type = data.get("type", "unknown")
            confidence = data.get("confidence", 0.0)
            features = data.get("features", [])
            sources = data.get("sources", [])
            
            if entity_id:
                # 创建实体节点
                self.entities[entity_id] = Entity3D(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    confidence=confidence,
                    features=features,
                    sources=sources
                )
                
                # 创建特征节点
                for i, feature_data in enumerate(features):
                    feature_text = feature_data.get("feature", "")
                    feature_sources = feature_data.get("sources", [])
                    
                    if feature_text:
                        # 生成特征ID
                        feature_id = f"{entity_id}_feature_{i}"
                        
                        # 创建特征节点
                        self.features[feature_id] = FeatureNode(
                            feature_id=feature_id,
                            entity_id=entity_id,
                            feature_text=feature_text,
                            sources=feature_sources,
                            confidence=confidence
                        )
                        
                        # 建立实体-特征索引
                        self.entity_to_features[entity_id].append(feature_id)
                        
        except Exception as e:
            logger.error(f"加载实体文件失败 {file_path}: {e}")
    
    def _load_relation_file(self, file_path: Path):
        """加载关系文件（水平边）"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            subject = data.get("subject")
            relation = data.get("relation")
            obj = data.get("object")
            confidence = data.get("confidence", 0.0)
            
            if subject and relation and obj:
                # 检查实体是否存在
                if subject in self.entities and obj in self.entities:
                    edge_id = f"edge_{len(self.horizontal_edges)}"
                    self.horizontal_edges.append({
                        "id": edge_id,
                        "from": subject,
                        "to": obj,
                        "label": relation,
                        "confidence": confidence,
                        "type": "horizontal"
                    })
                    
        except Exception as e:
            logger.error(f"加载关系文件失败 {file_path}: {e}")
    
    def _build_scene_nodes(self):
        """从特征来源中构建场景节点"""
        scene_counter = 0
        scene_id_map = {}  # 原始scene_id -> 标准化scene_id
        
        for feature_id, feature in self.features.items():
            for source in feature.sources:
                scene_id = source.get("scene_id")
                dialogue_id = source.get("dialogue_id")
                episode_id = source.get("episode_id")
                generated_at = source.get("generated_at")
                prompt_version = source.get("prompt_version")
                file_number = source.get("file_number")
                
                if scene_id:
                    # 标准化scene_id
                    std_scene_id = f"scene_{scene_id}"
                    
                    if std_scene_id not in self.scenes:
                        # 创建场景节点
                        self.scenes[std_scene_id] = SceneNode(
                            scene_id=std_scene_id,
                            dialogue_id=dialogue_id or "",
                            episode_id=episode_id or "",
                            generated_at=generated_at or "",
                            prompt_version=prompt_version or "",
                            file_number=file_number
                        )
                        scene_counter += 1
                    
                    # 建立特征-场景索引
                    if feature_id not in self.feature_to_scenes:
                        self.feature_to_scenes[feature_id] = []
                    
                    if std_scene_id not in self.feature_to_scenes[feature_id]:
                        self.feature_to_scenes[feature_id].append(std_scene_id)
        
        logger.info(f"构建了 {scene_counter} 个场景节点")
    
    def _build_vertical_edges(self):
        """构建垂直连接边"""
        # 实体-特征连接
        for entity_id, feature_ids in self.entity_to_features.items():
            for feature_id in feature_ids:
                edge = VerticalEdge(
                    from_id=entity_id,
                    to_id=feature_id,
                    edge_type="entity_feature",
                    confidence=self.entities[entity_id].confidence
                )
                self.vertical_edges.append(edge)
        
        # 特征-场景连接
        for feature_id, scene_ids in self.feature_to_scenes.items():
            for scene_id in scene_ids:
                edge = VerticalEdge(
                    from_id=feature_id,
                    to_id=scene_id,
                    edge_type="feature_scene",
                    confidence=self.features[feature_id].confidence
                )
                self.vertical_edges.append(edge)
        
        logger.info(f"构建了 {len(self.vertical_edges)} 个垂直连接边")
    
    def _get_entity_type_distribution(self) -> Dict[str, int]:
        """获取实体类型分布"""
        distribution = {}
        for entity in self.entities.values():
            entity_type = entity.type
            distribution[entity_type] = distribution.get(entity_type, 0) + 1
        return distribution
    
    def _get_feature_distribution(self) -> Dict[str, int]:
        """获取特征分布（按实体）"""
        distribution = {}
        for entity_id, feature_ids in self.entity_to_features.items():
            distribution[entity_id] = len(feature_ids)
        return distribution
    
    def _get_scene_distribution(self) -> Dict[str, int]:
        """获取场景分布（按特征）"""
        distribution = {}
        for feature_id, scene_ids in self.feature_to_scenes.items():
            distribution[feature_id] = len(scene_ids)
        return distribution
    
    def get_3d_graph_data(self) -> Dict:
        """获取三维图数据格式（用于前端可视化）"""
        # 实体节点
        entity_nodes = []
        for entity in self.entities.values():
            entity_nodes.append({
                "id": entity.id,
                "label": entity.id,
                "type": entity.type,
                "layer": "entity",
                "confidence": entity.confidence,
                "features_count": len(entity.features),
                "sources_count": len(entity.sources),
                "color": self._get_entity_color(entity.type),
                "position": {"x": 0, "y": 0, "z": 0}  # 将由前端布局
            })
        
        # 特征节点
        feature_nodes = []
        for feature in self.features.values():
            feature_nodes.append({
                "id": feature.id,
                "label": feature.feature_text[:30] + "..." if len(feature.feature_text) > 30 else feature.feature_text,
                "full_text": feature.feature_text,
                "type": "feature",
                "layer": "feature",
                "entity_id": feature.entity_id,
                "confidence": feature.confidence,
                "sources_count": len(feature.sources),
                "color": "#9b59b6",  # 紫色
                "position": {"x": 0, "y": 0, "z": 100}  # 特征层在z=100
            })
        
        # 场景节点
        scene_nodes = []
        for scene in self.scenes.values():
            scene_nodes.append({
                "id": scene.id,
                "label": scene.id,
                "type": "scene",
                "layer": "scene",
                "dialogue_id": scene.dialogue_id,
                "episode_id": scene.episode_id,
                "generated_at": scene.generated_at,
                "color": "#2ecc71",  # 绿色
                "position": {"x": 0, "y": 0, "z": 200}  # 场景层在z=200
            })
        
        # 水平边
        horizontal_edges = []
        for edge in self.horizontal_edges:
            horizontal_edges.append({
                "id": edge["id"],
                "from": edge["from"],
                "to": edge["to"],
                "label": edge["label"],
                "type": "horizontal",
                "confidence": edge["confidence"],
                "color": "#7f8c8d"  # 灰色
            })
        
        # 垂直边
        vertical_edges = []
        for i, edge in enumerate(self.vertical_edges):
            vertical_edges.append({
                "id": f"vertical_{i}",
                "from": edge.from_id,
                "to": edge.to_id,
                "type": edge.edge_type,
                "confidence": edge.confidence,
                "color": "#3498db" if edge.edge_type == "entity_feature" else "#e74c3c"  # 蓝色或红色
            })
        
        return {
            "entities": entity_nodes,
            "features": feature_nodes,
            "scenes": scene_nodes,
            "horizontal_edges": horizontal_edges,
            "vertical_edges": vertical_edges,
            "stats": {
                "total_entities": len(entity_nodes),
                "total_features": len(feature_nodes),
                "total_scenes": len(scene_nodes),
                "total_horizontal_edges": len(horizontal_edges),
                "total_vertical_edges": len(vertical_edges)
            }
        }
    
    def _get_entity_color(self, entity_type: str) -> str:
        """根据实体类型获取颜色"""
        color_map = {
            'person': '#3498db',        # 蓝色
            'organization': '#e74c3c',  # 红色
            'location': '#2ecc71',      # 绿色
            'product': '#f39c12',       # 橙色
            'work': '#9b59b6',          # 紫色
            'unknown': '#95a5a6'        # 灰色
        }
        return color_map.get(entity_type, '#95a5a6')
    
    def get_entity_details(self, entity_id: str) -> Optional[Dict]:
        """获取实体详细信息"""
        entity = self.entities.get(entity_id)
        if not entity:
            return None
        
        return {
            "entity": entity.to_dict(),
            "features": [self.features[fid].to_dict() for fid in self.entity_to_features.get(entity_id, [])],
            "related_entities": self._get_related_entities(entity_id)
        }
    
    def get_feature_details(self, feature_id: str) -> Optional[Dict]:
        """获取特征详细信息"""
        feature = self.features.get(feature_id)
        if not feature:
            return None
        
        entity = self.entities.get(feature.entity_id)
        scenes = [self.scenes[sid].to_dict() for sid in self.feature_to_scenes.get(feature_id, [])]
        
        return {
            "feature": feature.to_dict(),
            "entity": entity.to_dict() if entity else None,
            "scenes": scenes
        }
    
    def get_scene_details(self, scene_id: str) -> Optional[Dict]:
        """获取场景详细信息，包括theme、diary和原始对话内容"""
        scene = self.scenes.get(scene_id)
        if not scene:
            return None
        
        # 尝试加载完整的scene文件内容
        full_scene_data = self._load_full_scene_file(scene_id)
        
        # 加载原始对话内容（如果scene中有dialogue_id）
        dialogue_content = None
        if full_scene_data:
            dialogue_content = self._load_dialogue_content(full_scene_data)
        
        # 查找与该场景相关的特征
        related_features = []
        for feature_id, scene_ids in self.feature_to_scenes.items():
            if scene_id in scene_ids:
                feature = self.features.get(feature_id)
                if feature:
                    related_features.append({
                        "feature": feature.to_dict(),
                        "entity": self.entities.get(feature.entity_id).to_dict() if feature.entity_id in self.entities else None
                    })
        
        # 合并基本scene信息和完整scene数据
        scene_dict = scene.to_dict()
        if full_scene_data:
            # 添加theme和diary字段
            scene_dict["theme"] = full_scene_data.get("theme", "")
            scene_dict["diary"] = full_scene_data.get("diary", "")
            # 添加其他可能存在的字段
            scene_dict["source"] = full_scene_data.get("source", {})
            scene_dict["meta"] = full_scene_data.get("meta", {})
        
        return {
            "scene": scene_dict,
            "related_features": related_features,
            "full_data": full_scene_data if full_scene_data else None,
            "dialogue_content": dialogue_content
        }
    
    def _load_full_scene_file(self, scene_id: str) -> Optional[Dict]:
        """加载完整的scene文件内容"""
        try:
            # 从scene_id中提取数字部分
            # 可能的scene_id格式: "scene_00001", "00001", "scene_1"
            scene_number = None
            
            # 如果scene_id以"scene_"开头
            if scene_id.startswith("scene_"):
                # 提取"scene_"之后的部分
                number_part = scene_id[6:]  # 移除"scene_"前缀
                # 尝试提取数字
                import re
                match = re.search(r'\d+', number_part)
                if match:
                    scene_number = match.group(0)
                else:
                    scene_number = number_part
            else:
                # 直接尝试提取数字
                import re
                match = re.search(r'\d+', scene_id)
                if match:
                    scene_number = match.group(0)
                else:
                    scene_number = scene_id
            
            if not scene_number:
                logger.warning(f"无法从scene_id中提取数字: {scene_id}")
                return None
            
            # 构建scene文件路径
            # scene文件存储在: {data_dir}/../scene/{scene_number}.json
            scene_dir = self.data_dir.parent / "scene"
            
            # 尝试不同的文件名格式
            possible_filenames = [
                f"{scene_number}.json",           # 00001.json
                f"{scene_number.zfill(5)}.json",  # 00001.json (确保5位)
                f"scene_{scene_number}.json",     # scene_00001.json
                f"scene_{scene_number.zfill(5)}.json",  # scene_00001.json (5位)
            ]
            
            scene_file = None
            for filename in possible_filenames:
                test_path = scene_dir / filename
                if test_path.exists():
                    scene_file = test_path
                    break
            
            if not scene_file:
                logger.warning(f"未找到scene文件，尝试的路径: {scene_dir}/{possible_filenames[0]}等")
                return None
            
            # 加载JSON文件
            with open(scene_file, 'r', encoding='utf-8') as f:
                scene_data = json.load(f)
            
            logger.info(f"成功加载scene文件: {scene_file}")
            return scene_data
            
        except Exception as e:
            logger.error(f"加载scene文件失败 {scene_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def _load_dialogue_content(self, scene_data: Dict) -> Optional[Dict]:
        """加载原始对话内容"""
        try:
            # 从scene数据中提取dialogue_id和turn_span
            source = scene_data.get("source", {})
            episodes = source.get("episodes", [])
            
            if not episodes:
                logger.warning("scene数据中没有episodes信息")
                return None
            
            # 获取第一个episode（通常只有一个）
            episode = episodes[0]
            dialogue_id = episode.get("dialogue_id")
            turn_span = episode.get("turn_span", [])
            
            if not dialogue_id:
                logger.warning("scene数据中没有dialogue_id")
                return None
            
            # 构建对话文件路径
            # 对话文件存储在: {data_dir}/../dialogues/{user_id}/{year-month}/{dialogue_id}.json
            # 首先需要解析dialogue_id格式: "dlg_2025-10-21_22-24-25"
            # 提取日期部分: 2025-10-21
            import re
            date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', dialogue_id)
            if not date_match:
                logger.warning(f"无法从dialogue_id中提取日期: {dialogue_id}")
                return None
            
            year = date_match.group(1)
            month = date_match.group(2)
            
            # 构建对话文件基础目录
            # 正确路径: F:\AI\M-Agent\data\memory\test2\dialogues
            dialogues_base_dir = self.data_dir.parent / "dialogues"
            
            # 直接使用ZQR作为用户目录（根据用户说明）
            user_dir = "ZQR"
            dialogue_dir = dialogues_base_dir / user_dir / f"{year}-{month}"
            test_path = dialogue_dir / f"{dialogue_id}.json"
            
            if test_path.exists():
                dialogue_file = test_path
                logger.info(f"在用户目录 {user_dir} 中找到对话文件: {dialogue_file}")
            else:
                logger.warning(f"对话文件不存在: {test_path}")
                # 如果ZQR目录不存在，尝试查找其他可能的目录
                possible_user_dirs = ["ZQR", "changshengEVA", "test", "default"]
                dialogue_file = None
                for user_dir in possible_user_dirs:
                    dialogue_dir = dialogues_base_dir / user_dir / f"{year}-{month}"
                    test_path = dialogue_dir / f"{dialogue_id}.json"
                    if test_path.exists():
                        dialogue_file = test_path
                        logger.info(f"在备选用户目录 {user_dir} 中找到对话文件: {dialogue_file}")
                        break
                
                # 如果还没找到，尝试递归搜索所有用户目录
                if not dialogue_file:
                    logger.info(f"在常规路径中未找到对话文件，开始递归搜索...")
                    for user_dir in dialogues_base_dir.iterdir():
                        if user_dir.is_dir():
                            # 搜索所有月份目录
                            for month_dir in user_dir.iterdir():
                                if month_dir.is_dir():
                                    test_path = month_dir / f"{dialogue_id}.json"
                                    if test_path.exists():
                                        dialogue_file = test_path
                                        logger.info(f"在递归搜索中找到对话文件: {dialogue_file}")
                                        break
                            if dialogue_file:
                                break
            
            if not dialogue_file:
                logger.warning(f"未找到对话文件: {dialogue_id}")
                return None
            
            # 加载对话文件
            with open(dialogue_file, 'r', encoding='utf-8') as f:
                dialogue_data = json.load(f)
            
            # 提取指定turn_span范围内的对话轮次
            turns = dialogue_data.get("turns", [])
            selected_turns = []
            
            if turn_span and len(turn_span) >= 2:
                start_idx = turn_span[0]
                end_idx = turn_span[1] + 1  # 包含结束索引
                if 0 <= start_idx < len(turns) and 0 <= end_idx <= len(turns):
                    selected_turns = turns[start_idx:end_idx]
                else:
                    logger.warning(f"turn_span超出范围: {turn_span}, 对话总轮次: {len(turns)}")
                    selected_turns = turns  # 返回所有轮次
            else:
                selected_turns = turns  # 如果没有turn_span，返回所有轮次
            
            # 构建返回数据
            result = {
                "dialogue_id": dialogue_id,
                "dialogue_file": str(dialogue_file),
                "turn_span": turn_span,
                "total_turns": len(turns),
                "selected_turns": selected_turns,
                "dialogue_meta": dialogue_data.get("meta", {}),
                "participants": dialogue_data.get("participants", [])
            }
            
            logger.info(f"成功加载对话内容: {dialogue_id}, 提取了 {len(selected_turns)} 个轮次")
            return result
            
        except Exception as e:
            logger.error(f"加载对话内容失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def _get_related_entities(self, entity_id: str) -> List[Dict]:
        """获取相关实体（通过水平边）"""
        related = []
        for edge in self.horizontal_edges:
            if edge["from"] == entity_id:
                related.append({
                    "entity_id": edge["to"],
                    "relation": edge["label"],
                    "confidence": edge["confidence"]
                })
            elif edge["to"] == entity_id:
                related.append({
                    "entity_id": edge["from"],
                    "relation": edge["label"],
                    "confidence": edge["confidence"]
                })
        return related
    
    def get_all_data_summary(self) -> Dict:
        """获取所有数据摘要"""
        return {
            "entities": [e.to_dict() for e in self.entities.values()],
            "features": [f.to_dict() for f in self.features.values()],
            "scenes": [s.to_dict() for s in self.scenes.values()],
            "horizontal_edges": self.horizontal_edges,
            "vertical_edges": [e.to_dict() for e in self.vertical_edges],
            "indices": {
                "entity_to_features": dict(self.entity_to_features),
                "feature_to_scenes": dict(self.feature_to_scenes)
            }
        }