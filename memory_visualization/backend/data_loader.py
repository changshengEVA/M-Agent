#!/usr/bin/env python3
"""
Memory数据加载器
用于加载dialogues、episodes、scenes数据
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import os

logger = logging.getLogger(__name__)

class MemoryDataLoader:
    """Memory数据加载器"""
    
    def __init__(self, data_dir: Optional[str] = None):
        """
        初始化数据加载器
        
        Args:
            data_dir: 数据目录路径，默认为项目根目录下的data/memory
        """
        if data_dir is None:
            # 默认使用项目根目录下的data/memory
            project_root = Path(__file__).parent.parent.parent
            self.data_dir = project_root / "data" / "memory"
        else:
            self.data_dir = Path(data_dir)
        
        logger.info(f"Memory数据目录: {self.data_dir}")
        
        # 数据缓存
        self.dialogues: List[Dict] = []
        self.episodes: List[Dict] = []
        self.qualifications: List[Dict] = []
        self.scenes: List[Dict] = []
        
        # 统计数据
        self.stats: Dict[str, Any] = {}
        
        # 加载数据
        self.load_all_data()
    
    def load_all_data(self) -> Dict[str, Any]:
        """加载所有数据并返回统计信息"""
        logger.info("开始加载Memory数据...")
        
        # 清空缓存
        self.dialogues.clear()
        self.episodes.clear()
        self.qualifications.clear()
        self.scenes.clear()
        
        # 加载dialogues
        dialogues_count = self._load_dialogues()
        
        # 加载episodes和qualifications
        episodes_count, qualifications_count = self._load_episodes_and_qualifications()
        
        # 加载scenes
        scenes_count = self._load_scenes()
        
        # 计算统计数据
        self.stats = {
            "total_dialogues": dialogues_count,
            "total_episodes": episodes_count,
            "total_qualifications": qualifications_count,
            "total_scenes": scenes_count,
            "loaded_at": datetime.now().isoformat(),
            "dialogues_by_user": self._count_dialogues_by_user(),
            "episodes_by_dialogue": self._count_episodes_by_dialogue(),
            "scenes_by_user": self._count_scenes_by_user(),
            "score_distribution": self._calculate_score_distribution()
        }
        
        logger.info(f"数据加载完成: {self.stats}")
        return self.stats
    
    def _load_dialogues(self) -> int:
        """加载所有dialogues"""
        count = 0
        dialogues_path = self.data_dir / "dialogues" / "by_user"
        
        if not dialogues_path.exists():
            logger.warning(f"Dialogues目录不存在: {dialogues_path}")
            return 0
        
        # 遍历所有用户目录
        for user_dir in dialogues_path.iterdir():
            if not user_dir.is_dir():
                continue
            
            user_id = user_dir.name
            
            # 遍历年份月份目录
            for year_month_dir in user_dir.iterdir():
                if not year_month_dir.is_dir():
                    continue
                
                # 遍历所有JSON文件
                for json_file in year_month_dir.glob("*.json"):
                    try:
                        with open(json_file, 'r', encoding='utf-8') as f:
                            dialogue_data = json.load(f)
                            dialogue_data["file_path"] = str(json_file)
                            dialogue_data["user_id"] = user_id
                            self.dialogues.append(dialogue_data)
                            count += 1
                    except Exception as e:
                        logger.error(f"加载dialogue文件失败 {json_file}: {e}")
        
        logger.info(f"加载了 {count} 个dialogues")
        return count
    
    def _load_episodes_and_qualifications(self) -> tuple[int, int]:
        """加载所有episodes和qualifications"""
        episodes_count = 0
        qualifications_count = 0
        episodes_path = self.data_dir / "episodes" / "by_dialogue"
        
        if not episodes_path.exists():
            logger.warning(f"Episodes目录不存在: {episodes_path}")
            return 0, 0
        
        # 遍历所有dialogue目录
        for dialogue_dir in episodes_path.iterdir():
            if not dialogue_dir.is_dir():
                continue
            
            dialogue_id = dialogue_dir.name
            
            # 加载episodes文件
            episodes_file = dialogue_dir / "episodes_v1.json"
            if episodes_file.exists():
                try:
                    with open(episodes_file, 'r', encoding='utf-8') as f:
                        episodes_data = json.load(f)
                        episodes_data["dialogue_id"] = dialogue_id
                        episodes_data["file_path"] = str(episodes_file)
                        self.episodes.append(episodes_data)
                        episodes_count += 1
                except Exception as e:
                    logger.error(f"加载episodes文件失败 {episodes_file}: {e}")
            
            # 加载qualifications文件
            qualifications_file = dialogue_dir / "qualifications_v1.json"
            if qualifications_file.exists():
                try:
                    with open(qualifications_file, 'r', encoding='utf-8') as f:
                        qualifications_data = json.load(f)
                        qualifications_data["dialogue_id"] = dialogue_id
                        qualifications_data["file_path"] = str(qualifications_file)
                        self.qualifications.append(qualifications_data)
                        qualifications_count += 1
                except Exception as e:
                    logger.error(f"加载qualifications文件失败 {qualifications_file}: {e}")
        
        logger.info(f"加载了 {episodes_count} 个episodes和 {qualifications_count} 个qualifications")
        return episodes_count, qualifications_count
    
    def _load_scenes(self) -> int:
        """加载所有scenes"""
        count = 0
        scenes_path = self.data_dir / "scenes" / "by_user"
        
        if not scenes_path.exists():
            logger.warning(f"Scenes目录不存在: {scenes_path}")
            return 0
        
        # 遍历所有用户目录
        for user_dir in scenes_path.iterdir():
            if not user_dir.is_dir():
                continue
            
            user_id = user_dir.name
            
            # 遍历场景目录
            for scene_dir in user_dir.iterdir():
                if not scene_dir.is_dir():
                    continue
                
                # 查找场景文件
                for scene_file in scene_dir.glob("*.json"):
                    try:
                        with open(scene_file, 'r', encoding='utf-8') as f:
                            scene_data = json.load(f)
                            scene_data["user_id"] = user_id
                            scene_data["file_path"] = str(scene_file)
                            self.scenes.append(scene_data)
                            count += 1
                    except Exception as e:
                        logger.error(f"加载scene文件失败 {scene_file}: {e}")
        
        logger.info(f"加载了 {count} 个scenes")
        return count
    
    def _count_dialogues_by_user(self) -> Dict[str, int]:
        """按用户统计dialogues数量"""
        counts = {}
        for dialogue in self.dialogues:
            user_id = dialogue.get("user_id", "unknown")
            counts[user_id] = counts.get(user_id, 0) + 1
        return counts
    
    def _count_episodes_by_dialogue(self) -> Dict[str, int]:
        """按dialogue统计episodes数量"""
        counts = {}
        for episode_data in self.episodes:
            dialogue_id = episode_data.get("dialogue_id", "unknown")
            episodes_list = episode_data.get("episodes", [])
            counts[dialogue_id] = len(episodes_list)
        return counts
    
    def _count_scenes_by_user(self) -> Dict[str, int]:
        """按用户统计scenes数量"""
        counts = {}
        for scene in self.scenes:
            user_id = scene.get("user_id", "unknown")
            counts[user_id] = counts.get(user_id, 0) + 1
        return counts
    
    def _calculate_score_distribution(self) -> Dict[str, Dict[str, int]]:
        """计算评分分布，动态检测 scene_potential_score 中的字段"""
        # 首先收集所有可能的分数字段及其可能的值范围
        # 我们遍历所有 qualifications 来发现字段
        field_value_sets = {}
        
        for qualification_data in self.qualifications:
            qualifications = qualification_data.get("qualifications", [])
            for qual in qualifications:
                score = qual.get("scene_potential_score", {})
                for field, value in score.items():
                    if isinstance(value, (int, float)):
                        # 确保字段在 field_value_sets 中
                        if field not in field_value_sets:
                            field_value_sets[field] = set()
                        # 记录出现的值（转换为字符串以便作为键）
                        field_value_sets[field].add(str(value))
        
        # 构建分布字典，每个字段包含其所有可能值的计数
        distribution = {}
        for field, value_set in field_value_sets.items():
            # 确定值的范围（假设分数为0-5的整数，但动态适应）
            # 为了简单起见，我们为每个出现的值创建一个桶
            distribution[field] = {value: 0 for value in sorted(value_set, key=lambda x: int(x) if x.isdigit() else x)}
        
        # 如果没有发现任何字段，使用默认字段（向后兼容）
        # 注意：根据实际数据，评分字段是 factual_novelty 和 emotional_novelty
        # 但为了兼容性，我们保留旧的默认字段
        if not distribution:
            # 检查是否有任何实际的评分数据
            has_actual_scores = False
            for qualification_data in self.qualifications:
                qualifications = qualification_data.get("qualifications", [])
                for qual in qualifications:
                    if qual.get("scene_potential_score"):
                        has_actual_scores = True
                        break
                if has_actual_scores:
                    break
            
            if has_actual_scores:
                # 如果有实际评分数据但没有检测到字段，可能是数据结构问题
                # 返回空分布而不是错误的默认值
                distribution = {}
            else:
                # 完全没有评分数据时使用旧的默认字段
                distribution = {
                    "information_density": {"0": 0, "1": 0, "2": 0},
                    "novelty": {"0": 0, "1": 0, "2": 0},
                    "total": {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0}
                }
        
        # 重新遍历 qualifications 进行计数
        for qualification_data in self.qualifications:
            qualifications = qualification_data.get("qualifications", [])
            for qual in qualifications:
                score = qual.get("scene_potential_score", {})
                for field, value in score.items():
                    if isinstance(value, (int, float)):
                        str_value = str(value)
                        if field in distribution and str_value in distribution[field]:
                            distribution[field][str_value] += 1
        
        return distribution
    
    # 获取数据的方法
    def get_all_dialogues(self) -> List[Dict]:
        """获取所有dialogues"""
        return self.dialogues
    
    def get_dialogue_by_id(self, dialogue_id: str) -> Optional[Dict]:
        """根据ID获取dialogue"""
        for dialogue in self.dialogues:
            if dialogue.get("dialogue_id") == dialogue_id:
                return dialogue
        return None
    
    def get_all_episodes(self) -> List[Dict]:
        """获取所有episodes"""
        return self.episodes
    
    def get_episodes_by_dialogue_id(self, dialogue_id: str) -> List[Dict]:
        """根据dialogue ID获取episodes"""
        result = []
        for episode_data in self.episodes:
            if episode_data.get("dialogue_id") == dialogue_id:
                result.extend(episode_data.get("episodes", []))
        return result
    
    def get_all_qualifications(self) -> List[Dict]:
        """获取所有qualifications"""
        return self.qualifications
    
    def get_qualifications_by_dialogue_id(self, dialogue_id: str) -> List[Dict]:
        """根据dialogue ID获取qualifications"""
        for qualification_data in self.qualifications:
            if qualification_data.get("dialogue_id") == dialogue_id:
                return qualification_data.get("qualifications", [])
        return []
    
    def get_all_scenes(self) -> List[Dict]:
        """获取所有scenes"""
        return self.scenes
    
    def get_scene_by_id(self, scene_id: str) -> Optional[Dict]:
        """根据ID获取scene"""
        for scene in self.scenes:
            if scene.get("scene_id") == scene_id:
                return scene
        return None
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计数据"""
        return self.stats
    
    def get_episode_by_id(self, dialogue_id: str, episode_id: str) -> Optional[Dict]:
        """根据dialogue ID和episode ID获取单个episode"""
        for episode_data in self.episodes:
            if episode_data.get("dialogue_id") == dialogue_id:
                episodes_list = episode_data.get("episodes", [])
                for episode in episodes_list:
                    if episode.get("episode_id") == episode_id:
                        # 添加dialogue_id到episode对象中
                        episode_with_dialogue = episode.copy()
                        episode_with_dialogue["dialogue_id"] = dialogue_id
                        return episode_with_dialogue
        return None
    
    def get_qualification_by_episode_id(self, dialogue_id: str, episode_id: str) -> Optional[Dict]:
        """根据dialogue ID和episode ID获取对应的qualification"""
        for qualification_data in self.qualifications:
            if qualification_data.get("dialogue_id") == dialogue_id:
                qualifications_list = qualification_data.get("qualifications", [])
                for qualification in qualifications_list:
                    if qualification.get("episode_id") == episode_id:
                        return qualification
        return None
    
    def get_episode_with_details(self, dialogue_id: str, episode_id: str) -> Optional[Dict]:
        """获取episode及其相关的qualification评分"""
        episode = self.get_episode_by_id(dialogue_id, episode_id)
        if not episode:
            return None
        
        qualification = self.get_qualification_by_episode_id(dialogue_id, episode_id)
        dialogue = self.get_dialogue_by_id(dialogue_id)
        
        return {
            "episode": episode,
            "qualification": qualification,
            "dialogue": dialogue
        }
    
    def get_dialogue_with_details(self, dialogue_id: str) -> Optional[Dict]:
        """获取dialogue及其相关的episodes和qualifications"""
        dialogue = self.get_dialogue_by_id(dialogue_id)
        if not dialogue:
            return None
        
        episodes = self.get_episodes_by_dialogue_id(dialogue_id)
        qualifications = self.get_qualifications_by_dialogue_id(dialogue_id)
        
        return {
            "dialogue": dialogue,
            "episodes": episodes,
            "qualifications": qualifications
        }