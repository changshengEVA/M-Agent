#!/usr/bin/env python3
"""
Episode 状态管理器。
用于在生成过程中更新和检查 episode_situation.json 文件。
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Any

class EpisodeStatusManager:
    """管理 episode_situation.json 文件的读取和更新"""
    
    def __init__(self, situation_file_path: Optional[Path] = None, workflow_id: str = "test"):
        """
        初始化状态管理器。
        
        Args:
            situation_file_path: episode_situation.json 文件路径，如果为 None 则使用默认路径
            workflow_id: 工作流ID，用于构建默认路径（例如 "test", "default"）
        """
        if situation_file_path is None:
            # 默认路径：项目根目录/data/memory/{workflow_id}/episodes/episode_situation.json
            project_root = Path(__file__).parent.parent.parent
            self.situation_file = project_root / "data" / "memory" / workflow_id / "episodes" / "episode_situation.json"
        else:
            self.situation_file = situation_file_path
        
        self._data = None
        self._load_data()
    
    def _load_data(self):
        """加载 episode_situation.json 数据"""
        if not self.situation_file.exists():
            # 如果文件不存在，创建基本结构
            self._data = {
                "statistics": {
                    "total_episodes": 0,
                    "scene_available": {"count": 0, "episode_keys": []},
                    "kg_available": {"count": 0, "episode_keys": []},
                    "emo_available": {"count": 0, "episode_keys": []},
                    "by_novelty": {}
                },
                "episodes": {},
                "metadata": {
                    "last_updated": datetime.utcnow().isoformat() + "Z",
                    "source_dialogue": "",
                    "episode_count": 0
                }
            }
            # 尝试从现有的 eligibility 文件自动初始化 episode 状态
            self._auto_initialize_from_eligibility_files()
        else:
            with open(self.situation_file, 'r', encoding='utf-8') as f:
                self._data = json.load(f)
    
    def _auto_initialize_from_eligibility_files(self):
        """
        从现有的 eligibility_v1.json 文件自动初始化 episode 状态。
        扫描 episodes 目录下的所有 eligibility_v1.json 文件，将其中的资格信息
        导入到 episode_situation.json 中。
        """
        import logging
        logger = logging.getLogger(__name__)
        
        # 获取 episodes 目录
        episodes_dir = self.situation_file.parent
        if not episodes_dir.exists():
            logger.warning(f"Episodes 目录不存在: {episodes_dir}")
            return
        
        # 扫描 by_dialogue 子目录
        by_dialogue_dir = episodes_dir / "by_dialogue"
        if not by_dialogue_dir.exists():
            logger.warning(f"by_dialogue 目录不存在: {by_dialogue_dir}")
            return
        
        initialized_count = 0
        for dialogue_dir in by_dialogue_dir.iterdir():
            if not dialogue_dir.is_dir():
                continue
            
            # 查找 eligibility_v1.json 文件
            eligibility_file = dialogue_dir / "eligibility_v1.json"
            if not eligibility_file.exists():
                continue
            
            try:
                with open(eligibility_file, 'r', encoding='utf-8') as f:
                    eligibility_data = json.load(f)
                
                dialogue_id = eligibility_data.get("dialogue_id", "")
                results = eligibility_data.get("results", [])
                
                for result in results:
                    episode_id = result.get("episode_id", "")
                    episode_key = f"{dialogue_id}:{episode_id}"
                    
                    # 如果 episode 已存在，跳过
                    if episode_key in self._data.get("episodes", {}):
                        continue
                    
                    # 创建 episode 条目
                    self._data.setdefault("episodes", {})[episode_key] = {
                        "episode_key": episode_key,
                        "episode_id": episode_id,
                        "dialogue_id": dialogue_id,
                        "scene_available": result.get("scene_available", False),
                        "kg_available": result.get("kg_available", False),
                        "emo_available": result.get("emo_available", False),
                        "factual_novelty": result.get("factual_novelty", 0),
                        "emotional_novelty": result.get("emotional_novelty", 0),
                        "eligible": result.get("eligible", False),
                        "reason": result.get("reason", ""),
                        "updated_at": datetime.utcnow().isoformat() + "Z",
                        "scene_generated": False,
                        "kg_candidates_generated": False,
                        "scene_generated_at": None,
                        "kg_candidates_generated_at": None,
                        "scene_file": None,
                        "kg_candidate_file": None
                    }
                    
                    initialized_count += 1
                    
            except Exception as e:
                logger.warning(f"加载 eligibility 文件失败 {eligibility_file}: {e}")
                continue
        
        if initialized_count > 0:
            logger.info(f"从 eligibility 文件自动初始化了 {initialized_count} 个 episode 状态")
            # 更新统计信息
            self.update_statistics()
            # 保存到文件
            self._save_data()
    
    def _save_data(self):
        """保存数据到文件"""
        # 确保目录存在
        self.situation_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 更新 metadata
        if "metadata" not in self._data:
            self._data["metadata"] = {}
        
        self._data["metadata"]["last_updated"] = datetime.utcnow().isoformat() + "Z"
        
        with open(self.situation_file, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
    
    def get_episode(self, episode_key: str) -> Optional[Dict]:
        """获取指定 episode 的数据"""
        return self._data.get("episodes", {}).get(episode_key)
    
    def update_episode(self, episode_key: str, updates: Dict[str, Any]):
        """
        更新指定 episode 的数据。
        
        Args:
            episode_key: episode 键（格式：dialogue_id:episode_id）
            updates: 要更新的字段字典
        """
        if "episodes" not in self._data:
            self._data["episodes"] = {}
        
        if episode_key not in self._data["episodes"]:
            # 如果 episode 不存在，创建基本结构
            dialogue_id, episode_id = self._parse_episode_key(episode_key)
            self._data["episodes"][episode_key] = {
                "episode_key": episode_key,
                "episode_id": episode_id,
                "dialogue_id": dialogue_id,
                "scene_available": False,
                "kg_available": False,
                "emo_available": False,
                "factual_novelty": 0,
                "emotional_novelty": 0,
                "eligible": False,
                "reason": "",
                "updated_at": datetime.utcnow().isoformat() + "Z",
                "scene_generated": False,
                "kg_candidates_generated": False,
                "scene_generated_at": None,
                "kg_candidates_generated_at": None,
                "scene_file": None,
                "kg_candidate_file": None
            }
        
        # 更新字段
        for key, value in updates.items():
            self._data["episodes"][episode_key][key] = value
        
        # 更新更新时间戳
        self._data["episodes"][episode_key]["updated_at"] = datetime.utcnow().isoformat() + "Z"
        
        self._save_data()
    
    def _parse_episode_key(self, episode_key: str) -> tuple[str, str]:
        """解析 episode_key 为 dialogue_id 和 episode_id"""
        if ":" in episode_key:
            parts = episode_key.split(":")
            return parts[0], parts[1]
        else:
            # 如果没有冒号，假设整个字符串是 dialogue_id，episode_id 为 ep_001
            return episode_key, "ep_001"
    
    def is_scene_generated(self, episode_key: str) -> bool:
        """检查是否已生成 scene"""
        episode = self.get_episode(episode_key)
        if not episode:
            return False
        return episode.get("scene_generated", False)
    
    def is_kg_candidates_generated(self, episode_key: str) -> bool:
        """检查是否已生成 kg_candidates"""
        episode = self.get_episode(episode_key)
        if not episode:
            return False
        return episode.get("kg_candidates_generated", False)

    def mark_scene_generated(self, episode_key: str, scene_file: str, created_at: Optional[str] = None):
        """
        标记 scene 已生成。
        
        Args:
            episode_key: episode 键
            scene_file: scene 文件名
            created_at: 生成时间，如果为 None 则使用当前时间
        """
        if created_at is None:
            created_at = datetime.utcnow().isoformat() + "Z"
        
        self.update_episode(episode_key, {
            "scene_generated": True,
            "scene_generated_at": created_at,
            "scene_file": scene_file
        })
    
    def mark_kg_candidates_generated(self, episode_key: str, kg_file: str, created_at: Optional[str] = None):
        """
        标记 kg_candidates 已生成。
        
        Args:
            episode_key: episode 键
            kg_file: kg_candidate 文件名
            created_at: 生成时间，如果为 None 则使用当前时间
        """
        if created_at is None:
            created_at = datetime.utcnow().isoformat() + "Z"
        
        self.update_episode(episode_key, {
            "kg_candidates_generated": True,
            "kg_candidates_generated_at": created_at,
            "kg_candidate_file": kg_file
        })

    def get_all_episodes(self) -> Dict[str, Dict]:
        """获取所有 episode 数据"""
        return self._data.get("episodes", {})
    
    def update_statistics(self):
        """更新统计信息"""
        episodes = self.get_all_episodes()
        
        # 计算各种统计
        total_episodes = len(episodes)
        scene_available_keys = [k for k, v in episodes.items() if v.get("scene_available", False)]
        kg_available_keys = [k for k, v in episodes.items() if v.get("kg_available", False)]
        emo_available_keys = [k for k, v in episodes.items() if v.get("emo_available", False)]
        
        # 更新 statistics
        if "statistics" not in self._data:
            self._data["statistics"] = {}
        
        self._data["statistics"]["total_episodes"] = total_episodes
        self._data["statistics"]["scene_available"] = {
            "count": len(scene_available_keys),
            "episode_keys": scene_available_keys
        }
        self._data["statistics"]["kg_available"] = {
            "count": len(kg_available_keys),
            "episode_keys": kg_available_keys
        }
        self._data["statistics"]["emo_available"] = {
            "count": len(emo_available_keys),
            "episode_keys": emo_available_keys
        }
        
        self._save_data()


# 全局实例缓存
_managers = {}

def get_status_manager(situation_file_path: Optional[Path] = None, workflow_id: str = "test") -> EpisodeStatusManager:
    """
    获取状态管理器实例（基于参数缓存）。
    
    Args:
        situation_file_path: episode_situation.json 文件路径，如果为 None 则使用默认路径
        workflow_id: 工作流ID，用于构建默认路径（例如 "test", "default"）
    
    Returns:
        EpisodeStatusManager 实例
    """
    global _managers
    
    # 生成缓存键
    if situation_file_path is not None:
        # 如果提供了具体路径，使用路径字符串作为键
        key = str(situation_file_path)
    else:
        # 否则使用 workflow_id
        key = workflow_id
    
    if key not in _managers:
        _managers[key] = EpisodeStatusManager(situation_file_path, workflow_id)
    
    return _managers[key]
