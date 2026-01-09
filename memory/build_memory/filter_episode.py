#!/usr/bin/env python3
# 2025-12-28 changshengEVA
"""
Episode Filtering 模块。
扫描 qualifications 文件，根据 eligibility 规则过滤 episodes，生成 eligibility 文件。
"""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from tqdm import tqdm

# 添加项目根目录到 Python 路径，确保可以导入 load_model
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 配置日志：只显示 WARNING 及以上级别，减少输出噪音
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 路径配置
EPISODES_ROOT = PROJECT_ROOT / "data" / "memory" / "episodes"

def ensure_directory(path: Path):
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)

def scan_qualification_files() -> List[Path]:
    """
    扫描所有 qualification 文件。
    返回所有找到的 qualification 文件路径列表。
    """
    qualification_files = []
    # 扫描 by_dialogue 目录
    by_dialogue_dir = EPISODES_ROOT / "by_dialogue"
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
    根据 qualification 文件路径生成对应的 eligibility 文件路径。
    格式: episodes/by_dialogue/{dialogue_id}/eligibility_{version}.json
    """
    dialogue_dir = qualification_file.parent
    return dialogue_dir / f"eligibility_{eligibility_version}.json"

def needs_eligibility_filter(qualification_file: Path, eligibility_version: str = "v1") -> bool:
    """检查 qualification 是否需要生成 eligibility（eligibility 文件不存在）"""
    eligibility_file = get_eligibility_path(qualification_file, eligibility_version)
    return not eligibility_file.exists()

def load_qualifications(qualification_file: Path) -> Dict:
    """加载 qualification JSON 文件"""
    with open(qualification_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_episodes(dialogue_dir: Path, episode_version: str = "v1") -> Dict:
    """加载 episode JSON 文件"""
    episode_file = dialogue_dir / f"episodes_{episode_version}.json"
    if not episode_file.exists():
        raise FileNotFoundError(f"Episode file not found: {episode_file}")
    
    with open(episode_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def build_episode_index(episode_data: Dict) -> Dict[str, Dict]:
    """
    建立 episode_id 到 episode 元数据的索引。
    """
    episode_index = {}
    for ep in episode_data.get("episodes", []):
        ep_id = ep.get("episode_id")
        if ep_id:
            episode_index[ep_id] = ep
    return episode_index

def apply_eligibility_rules(episode: Dict, qualification: Dict) -> Dict:
    """
    应用 eligibility 规则，返回过滤结果。
    
    Args:
        episode: episode 元数据
        qualification: qualification 数据
        
    Returns:
        包含 eligible, reason, rule_hits, scene_available, kg_available, emo_available 的字典
    """
    score = qualification.get("scene_potential_score", {})
    rule_hits = []
    eligible = True
    reason = "scene_buildable"
    
    # 获取 novelty 值
    factual_novelty = score.get("factual_novelty", 0)
    emotional_novelty = score.get("emotional_novelty", 0)
    
    # 计算新的判定条件
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
    过滤 qualifications，生成 eligibility 结果。
    
    Args:
        qualification_data: qualification 数据
        episode_data: episode 数据
        
    Returns:
        eligibility 结果列表（包含完整信息）
    """
    # 建立 episode 索引
    episode_index = build_episode_index(episode_data)
    
    results = []
    qualifications = qualification_data.get("qualifications", [])
    
    for q in qualifications:
        ep_id = q.get("episode_id")
        ep = episode_index.get(ep_id)
        
        if ep is None:
            logger.warning(f"Episode {ep_id} not found in episode data, skipping")
            continue
        
        # 应用 eligibility 规则
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
    """保存 eligibility 到文件"""
    ensure_directory(eligibility_file.parent)
    
    eligibility_output = {
        "dialogue_id": dialogue_id,
        "eligibility_version": eligibility_version,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "results": results
    }
    
    with open(eligibility_file, 'w', encoding='utf-8') as f:
        json.dump(eligibility_output, f, ensure_ascii=False, indent=2)


def save_episode_situation(results: List[Dict], dialogue_id: str):
    """
    保存 episode situation 数据到全局文件（统计总和形式）。
    
    Args:
        results: 包含完整 episode 信息的列表
        dialogue_id: 对话 ID
    """
    situation_file = EPISODES_ROOT / "episode_situation.json"
    
    # 加载现有的 situation 数据（如果存在）
    existing_data = {}
    if situation_file.exists():
        try:
            with open(situation_file, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
        except Exception as e:
            logger.warning(f"加载现有 episode_situation.json 失败: {e}")
            existing_data = {}
    
    # 确保数据结构正确
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
    
    # 更新或添加当前 dialogue 的 episode 信息
    for result in results:
        ep_id = result["episode_id"]
        # 使用组合键作为唯一标识：dialogue_id:episode_id
        episode_key = f"{dialogue_id}:{ep_id}"
        
        # 保存完整的 episode 信息
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
    
    # 重新计算统计信息
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
        
        # 按 novelty 分类
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
    
    # 更新统计信息
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
    
    # 添加元数据
    existing_data["metadata"] = {
        "last_updated": datetime.utcnow().isoformat() + "Z",
        "source_dialogue": dialogue_id,
        "episode_count": len(results)
    }
    
    # 保存文件
    ensure_directory(situation_file.parent)
    with open(situation_file, 'w', encoding='utf-8') as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=2)
    
    logger.info(f"更新 episode_situation.json: 统计信息已更新，总计 {len(existing_data['episodes'])} 个 episodes")

def process_qualification_file(qualification_file: Path,
                               episode_version: str = "v1",
                               eligibility_version: str = "v1",
                               force_update: bool = False) -> bool:
    """处理单个 qualification 文件，生成 eligibility 并保存 situation"""
    try:
        # 加载 qualifications
        qualification_data = load_qualifications(qualification_file)
        
        # 获取 dialogue_id
        if qualification_data.get("qualifications"):
            dialogue_id = qualification_data["qualifications"][0].get("dialogue_id", "")
        else:
            # 从目录名推断
            dialogue_id = qualification_file.parent.name
        
        # 加载对应的 episodes
        dialogue_dir = qualification_file.parent
        episode_data = load_episodes(dialogue_dir, episode_version)
        
        # 验证 dialogue_id 一致性
        if episode_data.get("dialogue_id") and episode_data["dialogue_id"] != dialogue_id:
            logger.warning(f"Dialogue ID mismatch: qualification={dialogue_id}, episode={episode_data['dialogue_id']}")
            dialogue_id = episode_data["dialogue_id"]
        
        # 过滤 qualifications
        results = filter_qualifications(qualification_data, episode_data)
        
        # 保存 eligibility 文件（只有在需要时或强制更新时）
        eligibility_file = get_eligibility_path(qualification_file, eligibility_version)
        if force_update or needs_eligibility_filter(qualification_file, eligibility_version):
            save_eligibility(results, dialogue_id, eligibility_file, eligibility_version)
            logger.info(f"Generated eligibility for {dialogue_id}: {len(results)} episodes processed")
        else:
            logger.info(f"Eligibility file already exists for {dialogue_id}, skipping generation")
        
        # 总是保存 episode situation 数据（即使 eligibility 文件已存在）
        save_episode_situation(results, dialogue_id)
        
        return True
        
    except Exception as e:
        logger.error(f"处理 qualification 文件 {qualification_file} 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def scan_and_filter_episodes(episode_version: str = "v1",
                             eligibility_version: str = "v1",
                             use_tqdm: bool = True,
                             force_update_situation: bool = True):
    """
    主函数：扫描所有 qualification 文件，为需要生成 eligibility 的对话创建 eligibility。
    
    Args:
        episode_version: episode 文件版本（默认 v1）
        eligibility_version: eligibility 文件版本（默认 v1）
        use_tqdm: 是否使用 tqdm 显示进度条
        force_update_situation: 是否强制更新 episode_situation.json（即使 eligibility 文件已存在）
    """
    # 确保 episodes 根目录存在
    ensure_directory(EPISODES_ROOT)
    
    # 扫描所有 qualification 文件
    qualification_files = scan_qualification_files()
    
    # 如果强制更新 situation，则处理所有文件；否则只处理需要生成 eligibility 的文件
    if force_update_situation:
        files_to_process = qualification_files
        logger.info(f"强制更新模式：处理所有 {len(files_to_process)} 个 qualification 文件")
    else:
        files_to_process = []
        for file in qualification_files:
            if needs_eligibility_filter(file, eligibility_version):
                files_to_process.append(file)
    
    if not files_to_process:
        # 没有需要处理的文件，静默退出
        logger.info("没有需要处理的 qualification 文件")
        return
    
    # 处理文件
    if use_tqdm:
        file_iter = tqdm(files_to_process, desc="过滤 episodes")
    else:
        file_iter = files_to_process
    
    success_count = 0
    for qualification_file in file_iter:
        if process_qualification_file(
            qualification_file,
            episode_version,
            eligibility_version,
            force_update=force_update_situation
        ):
            success_count += 1
    
    logger.info(f"成功处理 {success_count}/{len(files_to_process)} 个 qualification 文件")

def clear_all_eligibility(eligibility_version: str = "v1", confirm: bool = False):
    """
    清理所有 eligibility 文件。
    
    Args:
        eligibility_version: eligibility 文件版本（默认 v1）
        confirm: 如果为 True，则实际删除文件；如果为 False，只显示将要删除的文件列表
    """
    # 扫描所有 qualification 文件
    qualification_files = scan_qualification_files()
    
    eligibility_files = []
    for qualification_file in qualification_files:
        eligibility_file = get_eligibility_path(qualification_file, eligibility_version)
        if eligibility_file.exists():
            eligibility_files.append(eligibility_file)
    
    if not eligibility_files:
        print("没有找到 eligibility 文件")
        return
    
    print(f"找到 {len(eligibility_files)} 个 eligibility 文件:")
    for eligibility_file in eligibility_files:
        print(f"  - {eligibility_file}")
    
    if not confirm:
        print("\n这只是预览。要实际删除这些文件，请运行: clear_all_eligibility(confirm=True)")
        return
    
    # 实际删除文件
    deleted_count = 0
    for eligibility_file in eligibility_files:
        try:
            eligibility_file.unlink()
            print(f"已删除: {eligibility_file}")
            deleted_count += 1
        except Exception as e:
            print(f"删除失败 {eligibility_file}: {e}")
    
    print(f"\n成功删除 {deleted_count}/{len(eligibility_files)} 个文件")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Episode Filtering 模块")
    parser.add_argument("--scan", action="store_true", help="扫描并过滤 episodes")
    parser.add_argument("--clear", action="store_true", help="清理所有 eligibility 文件")
    parser.add_argument("--confirm", action="store_true", help="确认删除（与 --clear 一起使用）")
    parser.add_argument("--episode-version", default="v1", help="episode 文件版本（默认 v1）")
    parser.add_argument("--eligibility-version", default="v1", help="eligibility 文件版本（默认 v1）")
    parser.add_argument("--force-update-situation", action="store_true",
                       help="强制更新 episode_situation.json（即使 eligibility 文件已存在）")
    
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
        # 默认行为：扫描并过滤
        scan_and_filter_episodes(
            episode_version=args.episode_version,
            eligibility_version=args.eligibility_version,
            force_update_situation=args.force_update_situation
        )