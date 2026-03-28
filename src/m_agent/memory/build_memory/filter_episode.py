#!/usr/bin/env python3
# 2025-12-28 changshengEVA
"""
Episode Filtering 妯″潡銆?
鎵弿 qualifications 鏂囦欢锛屾牴鎹?eligibility 瑙勫垯杩囨护 episodes锛岀敓鎴?eligibility 鏂囦欢銆?
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from tqdm import tqdm

from m_agent.paths import memory_stage_dir
# 娣诲姞椤圭洰鏍圭洰褰曞埌 Python 璺緞锛岀‘淇濆彲浠ュ鍏?load_model

# 閰嶇疆鏃ュ織锛氬彧鏄剧ず WARNING 鍙婁互涓婄骇鍒紝鍑忓皯杈撳嚭鍣煶
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 璺緞閰嶇疆
EPISODES_ROOT = memory_stage_dir("default", "episodes")

def ensure_directory(path: Path):
    """纭繚鐩綍瀛樺湪"""
    path.mkdir(parents=True, exist_ok=True)

def scan_qualification_files(episodes_root: Path = None) -> List[Path]:
    """
    鎵弿鎵€鏈?qualification 鏂囦欢銆?
    杩斿洖鎵€鏈夋壘鍒扮殑 qualification 鏂囦欢璺緞鍒楄〃銆?
    
    Args:
        episodes_root: episodes鏍圭洰褰曪紝濡傛灉涓篘one鍒欎娇鐢ㄩ粯璁ょ殑EPISODES_ROOT
    """
    if episodes_root is None:
        episodes_root = EPISODES_ROOT
    
    qualification_files = []
    # 鎵弿 by_dialogue 鐩綍
    by_dialogue_dir = episodes_root / "by_dialogue"
    if not by_dialogue_dir.exists():
        return qualification_files
    
    for dialogue_dir in by_dialogue_dir.iterdir():
        if dialogue_dir.is_dir():
            qualification_file = dialogue_dir / "qualifications_v1.json"
            if qualification_file.exists():
                qualification_files.append(qualification_file)
    
    return qualification_files

def get_eligibility_path(qualification_file: Path, eligibility_version: str = "v1") -> Path:
    """
    鏍规嵁 qualification 鏂囦欢璺緞鐢熸垚瀵瑰簲鐨?eligibility 鏂囦欢璺緞銆?
    鏍煎紡: episodes/by_dialogue/{dialogue_id}/eligibility_{version}.json
    """
    dialogue_dir = qualification_file.parent
    return dialogue_dir / f"eligibility_{eligibility_version}.json"

def needs_eligibility_filter(qualification_file: Path, eligibility_version: str = "v1") -> bool:
    """妫€鏌?qualification 鏄惁闇€瑕佺敓鎴?eligibility锛坋ligibility 鏂囦欢涓嶅瓨鍦級"""
    eligibility_file = get_eligibility_path(qualification_file, eligibility_version)
    return not eligibility_file.exists()

def load_qualifications(qualification_file: Path) -> Dict:
    """鍔犺浇 qualification JSON 鏂囦欢"""
    with open(qualification_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_episodes(dialogue_dir: Path, episode_version: str = "v1") -> Dict:
    """鍔犺浇 episode JSON 鏂囦欢"""
    episode_file = dialogue_dir / f"episodes_{episode_version}.json"
    if not episode_file.exists():
        raise FileNotFoundError(f"Episode file not found: {episode_file}")
    
    with open(episode_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def build_episode_index(episode_data: Dict) -> Dict[str, Dict]:
    """
    寤虹珛 episode_id 鍒?episode 鍏冩暟鎹殑绱㈠紩銆?
    """
    episode_index = {}
    for ep in episode_data.get("episodes", []):
        ep_id = ep.get("episode_id")
        if ep_id:
            episode_index[ep_id] = ep
    return episode_index

def apply_eligibility_rules(episode: Dict, qualification: Dict) -> Dict:
    """
    搴旂敤 eligibility 瑙勫垯锛岃繑鍥炶繃婊ょ粨鏋溿€?
    
    Args:
        episode: episode 鍏冩暟鎹?
        qualification: qualification 鏁版嵁
        
    Returns:
        鍖呭惈 eligible, reason, rule_hits, scene_available, kg_available, emo_available 鐨勫瓧鍏?
    """
    score = qualification.get("scene_potential_score", {})
    rule_hits = []
    eligible = True
    reason = "scene_buildable"
    
    # 鑾峰彇 novelty 鍊?
    factual_novelty = score.get("factual_novelty", 0)
    emotional_novelty = score.get("emotional_novelty", 0)
    
    # 璁＄畻鏂扮殑鍒ゅ畾鏉′欢
    scene_available = (
        factual_novelty >= 1
        and (factual_novelty == 2 or emotional_novelty == 1)
    )
    kg_available = factual_novelty >= 1
    emo_available = emotional_novelty == 1
    
    # Rule 1: information density
    if score.get("information_density", 1) < 1:
        eligible = False
        reason = "information_density_0"
        rule_hits.append("information_density_0")
    
    # Rule 2: pure social interaction filter
    turn_span = episode.get("turn_span", [0, 0])
    turn_count = turn_span[1] - turn_span[0] + 1 if len(turn_span) == 2 else 0
    
    if (
        episode.get("intent_type") == "emotional_interaction"
        and episode.get("interaction_mode") == "casual_banter"
        and turn_count <= 3
    ):
        eligible = False
        reason = "pure_social_interaction"
        rule_hits.append("emotional_casual_short")
    
    return {
        "eligible": eligible,
        "reason": reason,
        "rule_hits": rule_hits,
        "scene_available": scene_available,
        "kg_available": kg_available,
        "emo_available": emo_available,
        "factual_novelty": factual_novelty,
        "emotional_novelty": emotional_novelty
    }

def filter_qualifications(qualification_data: Dict, episode_data: Dict) -> List[Dict]:
    """
    杩囨护 qualifications锛岀敓鎴?eligibility 缁撴灉銆?
    
    Args:
        qualification_data: qualification 鏁版嵁
        episode_data: episode 鏁版嵁
        
    Returns:
        eligibility 缁撴灉鍒楄〃锛堝寘鍚畬鏁翠俊鎭級
    """
    # 寤虹珛 episode 绱㈠紩
    episode_index = build_episode_index(episode_data)
    
    results = []
    qualifications = qualification_data.get("qualifications", [])
    
    for q in qualifications:
        ep_id = q.get("episode_id")
        ep = episode_index.get(ep_id)
        
        if ep is None:
            logger.warning(f"Episode {ep_id} not found in episode data, skipping")
            continue
        
        # 搴旂敤 eligibility 瑙勫垯
        eligibility_result = apply_eligibility_rules(ep, q)
        
        results.append({
            "episode_id": ep_id,
            "dialogue_id": q.get("dialogue_id", ""),
            "eligible": eligibility_result["eligible"],
            "reason": eligibility_result["reason"],
            "rule_hits": eligibility_result["rule_hits"],
            "scene_available": eligibility_result["scene_available"],
            "kg_available": eligibility_result["kg_available"],
            "emo_available": eligibility_result["emo_available"],
            "factual_novelty": eligibility_result["factual_novelty"],
            "emotional_novelty": eligibility_result["emotional_novelty"]
        })
    
    return results

def save_eligibility(results: List[Dict], dialogue_id: str, eligibility_file: Path,
                     eligibility_version: str = "v1"):
    """Save eligibility results to disk."""
    ensure_directory(eligibility_file.parent)
    
    eligibility_output = {
        "dialogue_id": dialogue_id,
        "eligibility_version": eligibility_version,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "results": results
    }
    
    with open(eligibility_file, 'w', encoding='utf-8') as f:
        json.dump(eligibility_output, f, ensure_ascii=False, indent=2)


def save_episode_situation(results: List[Dict], dialogue_id: str, episodes_root: Path = None):
    """
    Save episode_situation.json for one dialogue.

    Args:
        results: Eligibility results for each episode.
        dialogue_id: Dialogue identifier.
        episodes_root: Episodes root directory.
    """
    if episodes_root is None:
        episodes_root = EPISODES_ROOT
    
    situation_file = episodes_root / "episode_situation.json"
    
    # 鍔犺浇鐜版湁鐨?situation 鏁版嵁锛堝鏋滃瓨鍦級
    existing_data = {}
    if situation_file.exists():
        try:
            with open(situation_file, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
        except Exception as e:
            logger.warning(f"鍔犺浇鐜版湁 episode_situation.json 澶辫触: {e}")
            existing_data = {}
    
    # 纭繚鏁版嵁缁撴瀯姝ｇ‘
    if "statistics" not in existing_data:
        existing_data["statistics"] = {
            "total_episodes": 0,
            "scene_available": {"count": 0, "episode_keys": []},
            "kg_available": {"count": 0, "episode_keys": []},
            "emo_available": {"count": 0, "episode_keys": []},
            "by_novelty": {
                "factual_novelty_0": {"count": 0, "episode_keys": []},
                "factual_novelty_1": {"count": 0, "episode_keys": []},
                "factual_novelty_2": {"count": 0, "episode_keys": []},
                "emotional_novelty_0": {"count": 0, "episode_keys": []},
                "emotional_novelty_1": {"count": 0, "episode_keys": []}
            }
        }
    
    if "episodes" not in existing_data:
        existing_data["episodes"] = {}
    
    # 鏇存柊鎴栨坊鍔犲綋鍓?dialogue 鐨?episode 淇℃伅
    for result in results:
        ep_id = result["episode_id"]
        # 浣跨敤缁勫悎閿綔涓哄敮涓€鏍囪瘑锛歞ialogue_id:episode_id
        episode_key = f"{dialogue_id}:{ep_id}"
        
        # 淇濆瓨瀹屾暣鐨?episode 淇℃伅锛屼繚鐣欑幇鏈夊瓧娈碉紙濡?scene_generated 绛夛級
        if episode_key in existing_data["episodes"]:
            # 鍚堝苟鐜版湁瀛楁
            existing_episode = existing_data["episodes"][episode_key]
            # 鏇存柊鍩虹瀛楁
            existing_episode.update({
                "scene_available": result["scene_available"],
                "kg_available": result["kg_available"],
                "emo_available": result["emo_available"],
                "factual_novelty": result["factual_novelty"],
                "emotional_novelty": result["emotional_novelty"],
                "eligible": result["eligible"],
                "reason": result["reason"],
                "updated_at": datetime.utcnow().isoformat() + "Z"
            })
            # 纭繚 episode_key, episode_id, dialogue_id 涓嶅彉锛堜絾搴旇宸茬粡瀛樺湪锛?
            existing_episode["episode_key"] = episode_key
            existing_episode["episode_id"] = ep_id
            existing_episode["dialogue_id"] = dialogue_id
            # 淇濈暀鍏朵粬瀛楁锛堝 scene_generated, scene_generated_at, scene_file 绛夛級
            existing_data["episodes"][episode_key] = existing_episode
        else:
            # 鍒涘缓鏂版潯鐩?
            existing_data["episodes"][episode_key] = {
                "episode_key": episode_key,
                "episode_id": ep_id,
                "dialogue_id": dialogue_id,
                "scene_available": result["scene_available"],
                "kg_available": result["kg_available"],
                "emo_available": result["emo_available"],
                "factual_novelty": result["factual_novelty"],
                "emotional_novelty": result["emotional_novelty"],
                "eligible": result["eligible"],
                "reason": result["reason"],
                "updated_at": datetime.utcnow().isoformat() + "Z"
            }
    
    # 閲嶆柊璁＄畻缁熻淇℃伅
    scene_available_keys = []
    kg_available_keys = []
    emo_available_keys = []
    
    factual_novelty_0_keys = []
    factual_novelty_1_keys = []
    factual_novelty_2_keys = []
    emotional_novelty_0_keys = []
    emotional_novelty_1_keys = []
    
    for episode_key, ep_data in existing_data["episodes"].items():
        if ep_data.get("scene_available"):
            scene_available_keys.append(episode_key)
        if ep_data.get("kg_available"):
            kg_available_keys.append(episode_key)
        if ep_data.get("emo_available"):
            emo_available_keys.append(episode_key)
        
        # 鎸?novelty 鍒嗙被
        factual_novelty = ep_data.get("factual_novelty", 0)
        if factual_novelty == 0:
            factual_novelty_0_keys.append(episode_key)
        elif factual_novelty == 1:
            factual_novelty_1_keys.append(episode_key)
        elif factual_novelty == 2:
            factual_novelty_2_keys.append(episode_key)
        
        emotional_novelty = ep_data.get("emotional_novelty", 0)
        if emotional_novelty == 0:
            emotional_novelty_0_keys.append(episode_key)
        elif emotional_novelty == 1:
            emotional_novelty_1_keys.append(episode_key)
    
    # 鏇存柊缁熻淇℃伅
    existing_data["statistics"] = {
        "total_episodes": len(existing_data["episodes"]),
        "scene_available": {
            "count": len(scene_available_keys),
            "episode_keys": scene_available_keys
        },
        "kg_available": {
            "count": len(kg_available_keys),
            "episode_keys": kg_available_keys
        },
        "emo_available": {
            "count": len(emo_available_keys),
            "episode_keys": emo_available_keys
        },
        "by_novelty": {
            "factual_novelty_0": {
                "count": len(factual_novelty_0_keys),
                "episode_keys": factual_novelty_0_keys
            },
            "factual_novelty_1": {
                "count": len(factual_novelty_1_keys),
                "episode_keys": factual_novelty_1_keys
            },
            "factual_novelty_2": {
                "count": len(factual_novelty_2_keys),
                "episode_keys": factual_novelty_2_keys
            },
            "emotional_novelty_0": {
                "count": len(emotional_novelty_0_keys),
                "episode_keys": emotional_novelty_0_keys
            },
            "emotional_novelty_1": {
                "count": len(emotional_novelty_1_keys),
                "episode_keys": emotional_novelty_1_keys
            }
        }
    }
    
    # 娣诲姞鍏冩暟鎹?
    existing_data["metadata"] = {
        "last_updated": datetime.utcnow().isoformat() + "Z",
        "source_dialogue": dialogue_id,
        "episode_count": len(results)
    }
    
    # 淇濆瓨鏂囦欢
    ensure_directory(situation_file.parent)
    with open(situation_file, 'w', encoding='utf-8') as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=2)
    
    #logger.info(f"鏇存柊 episode_situation.json: 缁熻淇℃伅宸叉洿鏂帮紝鎬昏 {len(existing_data['episodes'])} 涓?episodes")

def process_qualification_file(qualification_file: Path,
                               episode_version: str = "v1",
                               eligibility_version: str = "v1",
                               force_update: bool = False,
                               episodes_root: Path = None) -> bool:
    """澶勭悊鍗曚釜 qualification 鏂囦欢锛岀敓鎴?eligibility 骞朵繚瀛?situation"""
    try:
        # 鍔犺浇 qualifications
        qualification_data = load_qualifications(qualification_file)
        
        # 鑾峰彇 dialogue_id
        if qualification_data.get("qualifications"):
            dialogue_id = qualification_data["qualifications"][0].get("dialogue_id", "")
        else:
            # 浠庣洰褰曞悕鎺ㄦ柇
            dialogue_id = qualification_file.parent.name
        
        # 鍔犺浇瀵瑰簲鐨?episodes
        dialogue_dir = qualification_file.parent
        episode_data = load_episodes(dialogue_dir, episode_version)
        
        # 楠岃瘉 dialogue_id 涓€鑷存€?
        if episode_data.get("dialogue_id") and episode_data["dialogue_id"] != dialogue_id:
            logger.warning(f"Dialogue ID mismatch: qualification={dialogue_id}, episode={episode_data['dialogue_id']}")
            dialogue_id = episode_data["dialogue_id"]
        
        # 杩囨护 qualifications
        results = filter_qualifications(qualification_data, episode_data)
        
        # 淇濆瓨 eligibility 鏂囦欢锛堝彧鏈夊湪闇€瑕佹椂鎴栧己鍒舵洿鏂版椂锛?
        eligibility_file = get_eligibility_path(qualification_file, eligibility_version)
        if force_update or needs_eligibility_filter(qualification_file, eligibility_version):
            save_eligibility(results, dialogue_id, eligibility_file, eligibility_version)
            logger.info(f"Generated eligibility for {dialogue_id}: {len(results)} episodes processed")
        else:
            logger.info(f"Eligibility file already exists for {dialogue_id}, skipping generation")
        
        # 鎬绘槸淇濆瓨 episode situation 鏁版嵁锛堝嵆浣?eligibility 鏂囦欢宸插瓨鍦級
        save_episode_situation(results, dialogue_id, episodes_root)
        
        return True
        
    except Exception as e:
        logger.error(f"澶勭悊 qualification 鏂囦欢 {qualification_file} 澶辫触: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def scan_and_filter_episodes(episode_version: str = "v1",
                             eligibility_version: str = "v1",
                             use_tqdm: bool = True,
                             force_update_situation: bool = True,
                             episodes_root: Path = None):
    """
    Scan qualification files and build or refresh eligibility outputs.

    Args:
        episode_version: Episode file version to read.
        eligibility_version: Eligibility file version to generate.
        use_tqdm: Whether to render a progress bar.
        force_update_situation: Whether to refresh episode_situation.json even
            when an eligibility file already exists.
        episodes_root: Optional episodes root override.
    """
    # 纭畾浣跨敤鐨勬牴鐩綍
    if episodes_root is None:
        episodes_root = EPISODES_ROOT
    
    # 纭繚 episodes 鏍圭洰褰曞瓨鍦?
    ensure_directory(episodes_root)
    
    # 鎵弿鎵€鏈?qualification 鏂囦欢
    qualification_files = scan_qualification_files(episodes_root)
    
    # 濡傛灉寮哄埗鏇存柊 situation锛屽垯澶勭悊鎵€鏈夋枃浠讹紱鍚﹀垯鍙鐞嗛渶瑕佺敓鎴?eligibility 鐨勬枃浠?
    if force_update_situation:
        files_to_process = qualification_files
        logger.info(f"寮哄埗鏇存柊妯″紡锛氬鐞嗘墍鏈?{len(files_to_process)} 涓?qualification 鏂囦欢")
    else:
        files_to_process = []
        for file in qualification_files:
            if needs_eligibility_filter(file, eligibility_version):
                files_to_process.append(file)
    
    if not files_to_process:
        # 娌℃湁闇€瑕佸鐞嗙殑鏂囦欢锛岄潤榛橀€€鍑?
        logger.info("娌℃湁闇€瑕佸鐞嗙殑 qualification 鏂囦欢")
        return
    
    # 澶勭悊鏂囦欢
    if use_tqdm:
        file_iter = tqdm(files_to_process, desc="杩囨护 episodes")
    else:
        file_iter = files_to_process
    
    success_count = 0
    for qualification_file in file_iter:
        if process_qualification_file(
            qualification_file,
            episode_version,
            eligibility_version,
            force_update=force_update_situation,
            episodes_root=episodes_root
        ):
            success_count += 1
    
    logger.info(f"鎴愬姛澶勭悊 {success_count}/{len(files_to_process)} 涓?qualification 鏂囦欢")

def clear_all_eligibility(eligibility_version: str = "v1", confirm: bool = False):
    """
    Remove generated eligibility files.

    Args:
        eligibility_version: Eligibility file version to target.
        confirm: When False, show a preview only.
    """
    # 鎵弿鎵€鏈?qualification 鏂囦欢
    qualification_files = scan_qualification_files()
    
    eligibility_files = []
    for qualification_file in qualification_files:
        eligibility_file = get_eligibility_path(qualification_file, eligibility_version)
        if eligibility_file.exists():
            eligibility_files.append(eligibility_file)
    
    if not eligibility_files:
        print("娌℃湁鎵惧埌 eligibility 鏂囦欢")
        return
    
    print(f"鎵惧埌 {len(eligibility_files)} 涓?eligibility 鏂囦欢:")
    for eligibility_file in eligibility_files:
        print(f"  - {eligibility_file}")
    
    if not confirm:
        print("\n杩欏彧鏄瑙堛€傝瀹為檯鍒犻櫎杩欎簺鏂囦欢锛岃杩愯: clear_all_eligibility(confirm=True)")
        return
    
    # 瀹為檯鍒犻櫎鏂囦欢
    deleted_count = 0
    for eligibility_file in eligibility_files:
        try:
            eligibility_file.unlink()
            print(f"宸插垹闄? {eligibility_file}")
            deleted_count += 1
        except Exception as e:
            print(f"鍒犻櫎澶辫触 {eligibility_file}: {e}")
    
    print(f"\nDeleted {deleted_count}/{len(eligibility_files)} files.")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Episode filtering utility")
    parser.add_argument("--scan", action="store_true", help="scan and generate eligibility files")
    parser.add_argument("--clear", action="store_true", help="remove generated eligibility files")
    parser.add_argument("--confirm", action="store_true", help="confirm deletion when used with --clear")
    parser.add_argument("--episode-version", default="v1", help="episode file version")
    parser.add_argument("--eligibility-version", default="v1", help="eligibility file version")
    parser.add_argument("--force-update-situation", action="store_true",
                       help="refresh episode_situation.json even if eligibility already exists")
    
    args = parser.parse_args()
    
    if args.clear:
        clear_all_eligibility(eligibility_version=args.eligibility_version, confirm=args.confirm)
    elif args.scan:
        scan_and_filter_episodes(
            episode_version=args.episode_version,
            eligibility_version=args.eligibility_version,
            force_update_situation=args.force_update_situation
        )
    else:
        # 榛樿琛屼负锛氭壂鎻忓苟杩囨护
        scan_and_filter_episodes(
            episode_version=args.episode_version,
            eligibility_version=args.eligibility_version,
            force_update_situation=args.force_update_situation
        )
