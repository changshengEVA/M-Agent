#!/usr/bin/env python3
"""
Episode 鐘舵€佺鐞嗗櫒銆?
鐢ㄤ簬鍦ㄧ敓鎴愯繃绋嬩腑鏇存柊鍜屾鏌?episode_situation.json 鏂囦欢銆?
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Any

from m_agent.paths import memory_stage_dir

class EpisodeStatusManager:
    """绠＄悊 episode_situation.json 鏂囦欢鐨勮鍙栧拰鏇存柊"""
    
    def __init__(self, situation_file_path: Optional[Path] = None, workflow_id: str = "test"):
        """
        鍒濆鍖栫姸鎬佺鐞嗗櫒銆?
        
        Args:
            situation_file_path: episode_situation.json 鏂囦欢璺緞锛屽鏋滀负 None 鍒欎娇鐢ㄩ粯璁よ矾寰?
            workflow_id: 宸ヤ綔娴両D锛岀敤浜庢瀯寤洪粯璁よ矾寰勶紙渚嬪 "test", "default"锛?
        """
        if situation_file_path is None:
            self.situation_file = memory_stage_dir(workflow_id, "episodes") / "episode_situation.json"
        else:
            self.situation_file = situation_file_path
        
        self._data = None
        self._load_data()
    
    def _load_data(self):
        """鍔犺浇 episode_situation.json 鏁版嵁"""
        if not self.situation_file.exists():
            # 濡傛灉鏂囦欢涓嶅瓨鍦紝鍒涘缓鍩烘湰缁撴瀯
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
            # 灏濊瘯浠庣幇鏈夌殑 eligibility 鏂囦欢鑷姩鍒濆鍖?episode 鐘舵€?
            self._auto_initialize_from_eligibility_files()
        else:
            with open(self.situation_file, 'r', encoding='utf-8') as f:
                self._data = json.load(f)
    
    def _auto_initialize_from_eligibility_files(self):
        """
        浠庣幇鏈夌殑 eligibility_v1.json 鏂囦欢鑷姩鍒濆鍖?episode 鐘舵€併€?
        鎵弿 episodes 鐩綍涓嬬殑鎵€鏈?eligibility_v1.json 鏂囦欢锛屽皢鍏朵腑鐨勮祫鏍间俊鎭?
        瀵煎叆鍒?episode_situation.json 涓€?
        """
        import logging
        logger = logging.getLogger(__name__)
        
        # 鑾峰彇 episodes 鐩綍
        episodes_dir = self.situation_file.parent
        if not episodes_dir.exists():
            logger.warning(f"Episodes 鐩綍涓嶅瓨鍦? {episodes_dir}")
            return
        
        # 鎵弿 by_dialogue 瀛愮洰褰?
        by_dialogue_dir = episodes_dir / "by_dialogue"
        if not by_dialogue_dir.exists():
            logger.warning(f"by_dialogue 鐩綍涓嶅瓨鍦? {by_dialogue_dir}")
            return
        
        initialized_count = 0
        for dialogue_dir in by_dialogue_dir.iterdir():
            if not dialogue_dir.is_dir():
                continue
            
            # 鏌ユ壘 eligibility_v1.json 鏂囦欢
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
                    
                    # 濡傛灉 episode 宸插瓨鍦紝璺宠繃
                    if episode_key in self._data.get("episodes", {}):
                        continue
                    
                    # 鍒涘缓 episode 鏉＄洰
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
                logger.warning(f"鍔犺浇 eligibility 鏂囦欢澶辫触 {eligibility_file}: {e}")
                continue
        
        if initialized_count > 0:
            logger.info(
                "Auto-initialized %s episode records from eligibility files.",
                initialized_count,
            )
            # 鏇存柊缁熻淇℃伅
            self.update_statistics()
            # 淇濆瓨鍒版枃浠?
            self._save_data()
    
    def _save_data(self):
        """Save data to disk."""
        # 纭繚鐩綍瀛樺湪
        self.situation_file.parent.mkdir(parents=True, exist_ok=True)

        # 鏇存柊 metadata
        if "metadata" not in self._data:
            self._data["metadata"] = {}

        self._data["metadata"]["last_updated"] = datetime.utcnow().isoformat() + "Z"

        with open(self.situation_file, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get_episode(self, episode_key: str) -> Optional[Dict]:
        """Get one episode record."""
        return self._data.get("episodes", {}).get(episode_key)
    
    def update_episode(self, episode_key: str, updates: Dict[str, Any]):
        """
        鏇存柊鎸囧畾 episode 鐨勬暟鎹€?
        
        Args:
            episode_key: episode 閿紙鏍煎紡锛歞ialogue_id:episode_id锛?
            updates: 瑕佹洿鏂扮殑瀛楁瀛楀吀
        """
        if "episodes" not in self._data:
            self._data["episodes"] = {}
        
        if episode_key not in self._data["episodes"]:
            # 濡傛灉 episode 涓嶅瓨鍦紝鍒涘缓鍩烘湰缁撴瀯
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
        
        # 鏇存柊瀛楁
        for key, value in updates.items():
            self._data["episodes"][episode_key][key] = value
        
        # 鏇存柊鏇存柊鏃堕棿鎴?
        self._data["episodes"][episode_key]["updated_at"] = datetime.utcnow().isoformat() + "Z"
        
        self._save_data()
    
    def _parse_episode_key(self, episode_key: str) -> tuple[str, str]:
        """瑙ｆ瀽 episode_key 涓?dialogue_id 鍜?episode_id"""
        if ":" in episode_key:
            parts = episode_key.split(":")
            return parts[0], parts[1]
        else:
            # 濡傛灉娌℃湁鍐掑彿锛屽亣璁炬暣涓瓧绗︿覆鏄?dialogue_id锛宔pisode_id 涓?ep_001
            return episode_key, "ep_001"
    
    def is_scene_generated(self, episode_key: str) -> bool:
        """妫€鏌ユ槸鍚﹀凡鐢熸垚 scene"""
        episode = self.get_episode(episode_key)
        if not episode:
            return False
        return episode.get("scene_generated", False)
    
    def is_kg_candidates_generated(self, episode_key: str) -> bool:
        """妫€鏌ユ槸鍚﹀凡鐢熸垚 kg_candidates"""
        episode = self.get_episode(episode_key)
        if not episode:
            return False
        return episode.get("kg_candidates_generated", False)

    def mark_scene_generated(self, episode_key: str, scene_file: str, created_at: Optional[str] = None):
        """
        鏍囪 scene 宸茬敓鎴愩€?
        
        Args:
            episode_key: episode 閿?
            scene_file: scene 鏂囦欢鍚?
            created_at: 鐢熸垚鏃堕棿锛屽鏋滀负 None 鍒欎娇鐢ㄥ綋鍓嶆椂闂?
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
        鏍囪 kg_candidates 宸茬敓鎴愩€?
        
        Args:
            episode_key: episode 閿?
            kg_file: kg_candidate 鏂囦欢鍚?
            created_at: 鐢熸垚鏃堕棿锛屽鏋滀负 None 鍒欎娇鐢ㄥ綋鍓嶆椂闂?
        """
        if created_at is None:
            created_at = datetime.utcnow().isoformat() + "Z"
        
        self.update_episode(episode_key, {
            "kg_candidates_generated": True,
            "kg_candidates_generated_at": created_at,
            "kg_candidate_file": kg_file
        })

    def get_all_episodes(self) -> Dict[str, Dict]:
        """鑾峰彇鎵€鏈?episode 鏁版嵁"""
        return self._data.get("episodes", {})
    
    def update_statistics(self):
        """鏇存柊缁熻淇℃伅"""
        episodes = self.get_all_episodes()
        
        # 璁＄畻鍚勭缁熻
        total_episodes = len(episodes)
        scene_available_keys = [k for k, v in episodes.items() if v.get("scene_available", False)]
        kg_available_keys = [k for k, v in episodes.items() if v.get("kg_available", False)]
        emo_available_keys = [k for k, v in episodes.items() if v.get("emo_available", False)]
        
        # 鏇存柊 statistics
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


# 鍏ㄥ眬瀹炰緥缂撳瓨
_managers = {}

def get_status_manager(situation_file_path: Optional[Path] = None, workflow_id: str = "test") -> EpisodeStatusManager:
    """
    鑾峰彇鐘舵€佺鐞嗗櫒瀹炰緥锛堝熀浜庡弬鏁扮紦瀛橈級銆?
    
    Args:
        situation_file_path: episode_situation.json 鏂囦欢璺緞锛屽鏋滀负 None 鍒欎娇鐢ㄩ粯璁よ矾寰?
        workflow_id: 宸ヤ綔娴両D锛岀敤浜庢瀯寤洪粯璁よ矾寰勶紙渚嬪 "test", "default"锛?
    
    Returns:
        EpisodeStatusManager 瀹炰緥
    """
    global _managers
    
    # 鐢熸垚缂撳瓨閿?
    if situation_file_path is not None:
        # 濡傛灉鎻愪緵浜嗗叿浣撹矾寰勶紝浣跨敤璺緞瀛楃涓蹭綔涓洪敭
        key = str(situation_file_path)
    else:
        # 鍚﹀垯浣跨敤 workflow_id
        key = workflow_id
    
    if key not in _managers:
        _managers[key] = EpisodeStatusManager(situation_file_path, workflow_id)
    
    return _managers[key]
