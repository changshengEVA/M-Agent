#!/usr/bin/env python3
# 2025-12-28 changshengEVA
"""
Scene Memory Unit 构建模块。
扫描所有 eligible episodes，为每个未构建 scene 的 episode 构建 scene 记忆单元。
"""

import os
import sys
import json
import yaml
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from tqdm import tqdm

# 添加项目根目录到 Python 路径，确保可以导入 load_model
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 配置日志：只显示 WARNING 及以上级别，减少输出噪音
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 路径配置
DIALOGUES_ROOT = PROJECT_ROOT / "data" / "memory" / "dialogues"
EPISODES_ROOT = PROJECT_ROOT / "data" / "memory" / "episodes"
SCENES_ROOT = PROJECT_ROOT / "data" / "memory" / "scenes"
CONFIG_PATH = PROJECT_ROOT / "config" / "prompt.yaml"
TRACKER_PATH = SCENES_ROOT / "unbuilt_scenes_tracker.json"

def ensure_directory(path: Path):
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)

def scan_eligibility_files() -> List[Path]:
    """
    扫描所有 eligibility 文件。
    返回所有找到的 eligibility 文件路径列表。
    """
    eligibility_files = []
    # 扫描 by_dialogue 目录
    by_dialogue_dir = EPISODES_ROOT / "by_dialogue"
    if not by_dialogue_dir.exists():
        return eligibility_files
    
    for dialogue_dir in by_dialogue_dir.iterdir():
        if dialogue_dir.is_dir():
            eligibility_file = dialogue_dir / "eligibility_v1.json"
            if eligibility_file.exists():
                eligibility_files.append(eligibility_file)
    
    return eligibility_files

def load_eligibility(eligibility_file: Path) -> Dict:
    """加载 eligibility JSON 文件"""
    with open(eligibility_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def find_dialogue_file(dialogue_id: str) -> Optional[Path]:
    """
    根据 dialogue_id 查找对应的对话文件。
    搜索 dialogues 目录下的所有子目录。
    """
    # 搜索 by_user 目录
    by_user_dir = DIALOGUES_ROOT / "by_user"
    if by_user_dir.exists():
        for user_dir in by_user_dir.iterdir():
            if user_dir.is_dir():
                for year_month_dir in user_dir.iterdir():
                    if year_month_dir.is_dir():
                        dialogue_file = year_month_dir / f"{dialogue_id}.json"
                        if dialogue_file.exists():
                            return dialogue_file
    
    # 搜索 by_flipflop 目录
    by_flipflop_dir = DIALOGUES_ROOT / "by_flipflop"
    if by_flipflop_dir.exists():
        for flipflop_dir in by_flipflop_dir.iterdir():
            if flipflop_dir.is_dir():
                for year_month_dir in flipflop_dir.iterdir():
                    if year_month_dir.is_dir():
                        dialogue_file = year_month_dir / f"{dialogue_id}.json"
                        if dialogue_file.exists():
                            return dialogue_file
    
    return None

def check_scene_built(user_id: str, dialogue_id: str, episode_id: str) -> Tuple[bool, Optional[str]]:
    """
    检查 scene 是否已构建。
    
    Returns:
        (scene_built, scene_id)
    """
    if user_id == "unknown":
        return False, None
    
    user_scenes_dir = SCENES_ROOT / "by_user" / user_id
    if not user_scenes_dir.exists():
        return False, None
    
    # 遍历所有 scene 目录，检查其 scene 文件中的 episode 映射
    for scene_dir in user_scenes_dir.iterdir():
        if scene_dir.is_dir() and scene_dir.name.startswith("scene_"):
            scene_file = scene_dir / "v1.0.json"
            if scene_file.exists():
                try:
                    with open(scene_file, 'r', encoding='utf-8') as f:
                        scene_data = json.load(f)
                    
                    # 检查 scene 是否对应这个 episode
                    # 从 source.episodes 中查找匹配的 episode
                    source_episodes = scene_data.get("source", {}).get("episodes", [])
                    for episode_info in source_episodes:
                        if (episode_info.get("dialogue_id") == dialogue_id and
                            episode_info.get("episode_id") == episode_id):
                            return True, scene_dir.name
                except Exception as e:
                    logger.warning(f"读取 scene 文件失败 {scene_file}: {e}")
                    continue
    
    return False, None

def scan_now_scene() -> Dict:
    """
    扫描所有 eligibility 文件，生成或更新 unbuilt_scenes_tracker.json。
    根据用户需求，只保留必要字段：
    - dialogue_id 和 episode_id
    - filter 字段（True/False）表示是否被过滤器过滤掉（根据 eligible 字段）
    - scene_built 字段（True/False）
    - scene_id 字段（如果已构建scene）
    - user_id 字段
    """
    # 扫描所有 eligibility 文件
    eligibility_files = scan_eligibility_files()
    
    # 创建新的 tracker 结构
    new_tracker = {
        "tracker_version": "v1",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "episodes": [],
        "statistics": {
            "total_episodes": 0,
            "filtered_count": 0,
            "unbuilt_count": 0,
            "built_count": 0
        }
    }
    
    all_episodes = []
    
    # 处理每个 eligibility 文件
    for eligibility_file in eligibility_files:
        try:
            eligibility_data = load_eligibility(eligibility_file)
            if not eligibility_data:
                continue
            
            dialogue_id = eligibility_data.get("dialogue_id", "")
            dialogue_dir = eligibility_file.parent
            
            # 获取 user_id
            user_id = "unknown"
            dialogue_file = find_dialogue_file(dialogue_id)
            if dialogue_file:
                # 从路径推断用户ID
                parts = dialogue_file.parts
                if "by_user" in parts:
                    user_idx = parts.index("by_user")
                    if user_idx + 1 < len(parts):
                        user_id = parts[user_idx + 1]
            
            # 处理每个 eligibility 结果
            for result in eligibility_data.get("results", []):
                episode_id = result.get("episode_id")
                eligible = result.get("eligible", True)
                
                # filter 状态：True 表示被过滤掉，False 表示通过
                filter_status = not eligible
                
                # 检查 scene 是否已构建
                scene_built, scene_id = check_scene_built(user_id, dialogue_id, episode_id)
                
                # 创建简化后的 episode 记录
                simplified_episode = {
                    "dialogue_id": dialogue_id,
                    "episode_id": episode_id,
                    "user_id": user_id,
                    "filter": filter_status,
                    "scene_built": scene_built,
                    "scene_id": scene_id,
                    "last_checked": datetime.utcnow().isoformat() + "Z"
                }
                
                all_episodes.append(simplified_episode)
                
        except Exception as e:
            logger.error(f"处理 eligibility 文件 {eligibility_file} 失败: {e}")
            continue
    
    # 按 dialogue_id 和 episode_id 排序
    all_episodes.sort(key=lambda x: (x["dialogue_id"], x["episode_id"]))
    new_tracker["episodes"] = all_episodes
    
    # 更新统计信息
    total = len(all_episodes)
    filtered = sum(1 for ep in all_episodes if ep["filter"])
    built = sum(1 for ep in all_episodes if ep["scene_built"])
    unbuilt = total - built
    
    new_tracker["statistics"] = {
        "total_episodes": total,
        "filtered_count": filtered,
        "unbuilt_count": unbuilt,
        "built_count": built
    }
    
    # 保存新的 tracker 文件
    logger.info(f"保存新的 tracker 文件: {TRACKER_PATH}")
    ensure_directory(TRACKER_PATH.parent)
    
    # 备份原文件（如果存在）
    if TRACKER_PATH.exists():
        import os
        import shutil
        backup_path = TRACKER_PATH.with_suffix('.json.backup')
        logger.info(f"备份原文件到: {backup_path}")
        # 如果备份文件已存在，先删除它
        if backup_path.exists():
            backup_path.unlink()
        os.rename(TRACKER_PATH, backup_path)
    
    with open(TRACKER_PATH, 'w', encoding='utf-8') as f:
        json.dump(new_tracker, f, ensure_ascii=False, indent=2)
    
    logger.info(f"scan_now_scene 完成!")
    logger.info(f"总 episodes: {total}")
    logger.info(f"被过滤的: {filtered}")
    logger.info(f"已构建 scene: {built}")
    logger.info(f"未构建 scene: {unbuilt}")
    
    return new_tracker

def extract_original_episode_talk(dialogue_file: Path, turn_span: List[int]) -> str:
    """
    根据 turn_span 从对话文件中提取原始对话文本。
    
    Args:
        dialogue_file: 对话文件路径
        turn_span: [start_turn_id, end_turn_id] 列表
    
    Returns:
        格式化的原始对话文本，保留说话人和说话顺序
    """
    try:
        with open(dialogue_file, 'r', encoding='utf-8') as f:
            dialogue_data = json.load(f)
    except Exception as e:
        logger.error(f"读取对话文件失败 {dialogue_file}: {e}")
        return ""
    
    turns = dialogue_data.get("turns", [])
    start_id, end_id = turn_span[0], turn_span[1]
    
    # 提取指定范围内的对话轮次
    episode_turns = []
    for turn in turns:
        turn_id = turn.get("turn_id")
        if turn_id is not None and start_id <= turn_id <= end_id:
            speaker = turn.get("speaker", "unknown")
            text = turn.get("text", "")
            episode_turns.append((turn_id, speaker, text))
    
    # 按 turn_id 排序
    episode_turns.sort(key=lambda x: x[0])
    
    # 格式化为字符串：说话人: 文本
    formatted_lines = []
    for turn_id, speaker, text in episode_turns:
        # 清理文本中的多余空格和换行
        cleaned_text = text.strip().replace('\n', ' ')
        formatted_lines.append(f"{speaker}: {cleaned_text}")
    
    return "\n".join(formatted_lines)

def load_prompt_config() -> Dict:
    """加载 prompt.yaml 配置文件"""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        return config
    except Exception as e:
        logger.error(f"加载 prompt 配置文件失败 {CONFIG_PATH}: {e}")
        return {}

def get_scene_build_prompt() -> str:
    """获取 scene_build 的 prompt"""
    config = load_prompt_config()
    return config.get("scene_build", "")

def get_next_scene_id(user_id: str) -> str:
    """
    获取用户的下一个 scene_id。
    格式: scene_000123 (6位数字，前导零)
    
    Args:
        user_id: 用户ID
    
    Returns:
        下一个 scene_id 字符串
    """
    if user_id == "unknown":
        user_id = "default"
    
    user_scenes_dir = SCENES_ROOT / "by_user" / user_id
    if not user_scenes_dir.exists():
        # 如果目录不存在，从 000001 开始
        return "scene_000001"
    
    # 查找现有的 scene 目录
    existing_ids = []
    for scene_dir in user_scenes_dir.iterdir():
        if scene_dir.is_dir() and scene_dir.name.startswith("scene_"):
            try:
                # 提取数字部分
                num_str = scene_dir.name.replace("scene_", "")
                num = int(num_str)
                existing_ids.append(num)
            except ValueError:
                continue
    
    if not existing_ids:
        return "scene_000001"
    
    # 找到最大的数字并加1
    max_id = max(existing_ids)
    next_id = max_id + 1
    
    # 格式化为6位数字，前导零
    return f"scene_{next_id:06d}"

def build_scene_from_episode(dialogue_id: str, episode_id: str, user_id: str) -> Optional[Dict]:
    """
    为指定的 episode 构建 scene。
    
    Args:
        dialogue_id: 对话ID
        episode_id: episode ID
        user_id: 用户ID
    
    Returns:
        构建的 scene 数据字典，如果失败则返回 None
    """
    # 1. 查找对话文件
    dialogue_file = find_dialogue_file(dialogue_id)
    if not dialogue_file:
        logger.error(f"找不到对话文件: {dialogue_id}")
        return None
    
    # 2. 查找 episode 文件获取 turn_span
    episode_dir = EPISODES_ROOT / "by_dialogue" / dialogue_id
    episode_file = episode_dir / "episodes_v1.json"
    if not episode_file.exists():
        logger.error(f"找不到 episode 文件: {episode_file}")
        return None
    
    try:
        with open(episode_file, 'r', encoding='utf-8') as f:
            episode_data = json.load(f)
    except Exception as e:
        logger.error(f"读取 episode 文件失败 {episode_file}: {e}")
        return None
    
    # 查找指定的 episode
    target_episode = None
    for ep in episode_data.get("episodes", []):
        if ep.get("episode_id") == episode_id:
            target_episode = ep
            break
    
    if not target_episode:
        logger.error(f"在对话 {dialogue_id} 中找不到 episode {episode_id}")
        return None
    
    turn_span = target_episode.get("turn_span")
    if not turn_span or len(turn_span) != 2:
        logger.error(f"无效的 turn_span: {turn_span}")
        return None
    
    # 3. 提取原始对话文本
    original_episode_talk = extract_original_episode_talk(dialogue_file, turn_span)
    if not original_episode_talk:
        logger.error(f"无法提取原始对话文本")
        return None
    
    # 4. 获取 scene_build prompt
    scene_build_prompt_template = get_scene_build_prompt()
    if not scene_build_prompt_template:
        logger.error(f"无法获取 scene_build prompt")
        return None
    
    # 5. 填充 prompt 模板
    prompt = scene_build_prompt_template.replace("{{original_episode_talk}}", original_episode_talk)
    
    # 6. 调用 LLM 生成 scene 内容
    try:
        from load_model.OpenAIcall import get_llm
        llm = get_llm(model_temperature=0.1)  # 低温度以获得更确定性的输出
        llm_response = llm(prompt)
        
        # 解析 JSON 响应
        import re
        # 尝试提取 JSON 部分
        json_match = re.search(r'\{.*\}', llm_response, re.DOTALL)
        if json_match:
            scene_content = json.loads(json_match.group())
        else:
            # 如果找不到 JSON，尝试直接解析整个响应
            scene_content = json.loads(llm_response)
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        logger.error(f"LLM 响应: {llm_response[:500] if 'llm_response' in locals() else 'N/A'}")
        return None
    
    # 7. 生成 scene_id
    scene_id = get_next_scene_id(user_id)
    
    # 8. 构建完整的 scene 数据结构
    scene_data = {
        "scene_id": scene_id,
        "scene_version": "v1",
        "source": {
            "episodes": [
                {
                    "episode_id": episode_id,
                    "dialogue_id": dialogue_id,
                    "user_id": user_id,
                    "turn_span": turn_span
                }
            ]
        },
        "scene_type": scene_content.get("scene_type", ""),
        "content_type": scene_content.get("content_type", ""),
        "diary": scene_content.get("diary", ""),
        "intent": scene_content.get("intent", ""),
        "content": scene_content.get("content", {
            "core": "",
            "context": "",
            "outcome": "",
            "notes": ""
        }),
        "tags": scene_content.get("tags", []),
        "confidence": scene_content.get("confidence", 0.5)
    }
    
    return scene_data

def save_scene(scene_data: Dict) -> bool:
    """
    保存 scene 数据到文件。
    
    Args:
        scene_data: scene 数据字典
    
    Returns:
        保存成功返回 True，失败返回 False
    """
    try:
        scene_id = scene_data.get("scene_id")
        # 从 source.episodes[0].user_id 获取 user_id
        user_id = "unknown"
        source_episodes = scene_data.get("source", {}).get("episodes", [])
        if source_episodes and len(source_episodes) > 0:
            user_id = source_episodes[0].get("user_id", "unknown")
        
        if user_id == "unknown":
            logger.error(f"无法为 unknown 用户保存 scene")
            return False
        
        # 创建 scene 目录
        scene_dir = SCENES_ROOT / "by_user" / user_id / scene_id
        ensure_directory(scene_dir)
        
        # 保存 scene 文件
        scene_file = scene_dir / "v1.0.json"
        with open(scene_file, 'w', encoding='utf-8') as f:
            json.dump(scene_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Scene 保存成功: {scene_file}")
        return True
    except Exception as e:
        logger.error(f"保存 scene 失败: {e}")
        return False

def delete_scene(scene_id: str, user_id: str) -> bool:
    """
    删除指定的 scene。
    
    Args:
        scene_id: scene ID (如 "scene_000001")
        user_id: 用户ID
    
    Returns:
        删除成功返回 True，失败返回 False
    """
    try:
        scene_dir = SCENES_ROOT / "by_user" / user_id / scene_id
        if not scene_dir.exists():
            logger.warning(f"Scene 目录不存在: {scene_dir}")
            return False
        
        # 删除 scene 目录及其所有内容
        import shutil
        shutil.rmtree(scene_dir)
        logger.info(f"Scene 删除成功: {scene_dir}")
        
        # 更新 tracker 文件中的状态
        if TRACKER_PATH.exists():
            try:
                with open(TRACKER_PATH, 'r', encoding='utf-8') as f:
                    tracker = json.load(f)
                
                # 找到对应的 episode 并更新状态
                for episode in tracker.get("episodes", []):
                    if episode.get("scene_id") == scene_id and episode.get("user_id") == user_id:
                        episode["scene_built"] = False
                        episode["scene_id"] = None
                        episode["last_checked"] = datetime.utcnow().isoformat() + "Z"
                        logger.info(f"更新 tracker 中 episode 状态: {episode.get('dialogue_id')}/{episode.get('episode_id')}")
                
                # 保存更新的 tracker
                with open(TRACKER_PATH, 'w', encoding='utf-8') as f:
                    json.dump(tracker, f, ensure_ascii=False, indent=2)
                
                logger.info(f"Tracker 文件已更新")
            except Exception as e:
                logger.error(f"更新 tracker 文件失败: {e}")
        
        return True
    except Exception as e:
        logger.error(f"删除 scene 失败: {e}")
        return False

def delete_all_scenes_for_user(user_id: str) -> bool:
    """
    删除指定用户的所有 scenes。
    
    Args:
        user_id: 用户ID
    
    Returns:
        删除成功返回 True，失败返回 False
    """
    try:
        user_scenes_dir = SCENES_ROOT / "by_user" / user_id
        if not user_scenes_dir.exists():
            logger.warning(f"用户 scenes 目录不存在: {user_scenes_dir}")
            return False
        
        # 删除所有 scene 目录
        import shutil
        for scene_dir in user_scenes_dir.iterdir():
            if scene_dir.is_dir() and scene_dir.name.startswith("scene_"):
                shutil.rmtree(scene_dir)
                logger.info(f"删除 scene 目录: {scene_dir}")
        
        logger.info(f"用户 {user_id} 的所有 scenes 已删除")
        
        # 更新 tracker 文件
        if TRACKER_PATH.exists():
            try:
                with open(TRACKER_PATH, 'r', encoding='utf-8') as f:
                    tracker = json.load(f)
                
                # 重置所有该用户的 episodes 状态
                for episode in tracker.get("episodes", []):
                    if episode.get("user_id") == user_id:
                        episode["scene_built"] = False
                        episode["scene_id"] = None
                        episode["last_checked"] = datetime.utcnow().isoformat() + "Z"
                
                # 保存更新的 tracker
                with open(TRACKER_PATH, 'w', encoding='utf-8') as f:
                    json.dump(tracker, f, ensure_ascii=False, indent=2)
                
                logger.info(f"Tracker 文件已更新，重置了用户 {user_id} 的所有 episodes 状态")
            except Exception as e:
                logger.error(f"更新 tracker 文件失败: {e}")
        
        return True
    except Exception as e:
        logger.error(f"删除用户所有 scenes 失败: {e}")
        return False

def scan_and_build_scene() -> Dict:
    """
    扫描并构建 scene 的主函数。
    
    步骤：
    1. 加载 tracker 文件
    2. 找出所有未构建且未被过滤的 episodes
    3. 为每个 episode 构建 scene
    4. 更新 tracker 文件
    
    Returns:
        构建结果的统计信息
    """
    logger.info("开始 scan_and_build_scene...")
    
    # 1. 加载 tracker 文件
    if not TRACKER_PATH.exists():
        logger.error(f"Tracker 文件不存在: {TRACKER_PATH}")
        logger.info("运行 scan_now_scene 创建 tracker...")
        tracker = scan_now_scene()
    else:
        try:
            with open(TRACKER_PATH, 'r', encoding='utf-8') as f:
                tracker = json.load(f)
        except Exception as e:
            logger.error(f"加载 tracker 文件失败: {e}")
            return {"error": f"加载 tracker 失败: {e}"}
    
    # 2. 找出所有未构建且未被过滤的 episodes
    unbuilt_episodes = []
    for episode in tracker.get("episodes", []):
        if not episode.get("scene_built", False) and not episode.get("filter", True):
            unbuilt_episodes.append(episode)
    
    logger.info(f"找到 {len(unbuilt_episodes)} 个未构建且未被过滤的 episodes")
    
    # 3. 为每个 episode 构建 scene
    built_count = 0
    failed_count = 0
    
    for episode in tqdm(unbuilt_episodes, desc="构建 scenes"):
        dialogue_id = episode.get("dialogue_id")
        episode_id = episode.get("episode_id")
        user_id = episode.get("user_id")
        
        logger.info(f"处理 episode: {dialogue_id}/{episode_id} (用户: {user_id})")
        
        # 构建 scene
        scene_data = build_scene_from_episode(dialogue_id, episode_id, user_id)
        
        if scene_data:
            # 保存 scene
            if save_scene(scene_data):
                # 更新 episode 状态
                episode["scene_built"] = True
                episode["scene_id"] = scene_data.get("scene_id")
                episode["last_checked"] = datetime.utcnow().isoformat() + "Z"
                built_count += 1
                logger.info(f"成功构建 scene: {scene_data.get('scene_id')}")
            else:
                failed_count += 1
                logger.error(f"保存 scene 失败: {dialogue_id}/{episode_id}")
        else:
            failed_count += 1
            logger.error(f"构建 scene 失败: {dialogue_id}/{episode_id}")
    
    # 4. 更新 tracker 文件
    if built_count > 0 or failed_count > 0:
        # 更新统计信息
        total_episodes = len(tracker.get("episodes", []))
        built_total = sum(1 for ep in tracker.get("episodes", []) if ep.get("scene_built", False))
        unbuilt_total = total_episodes - built_total
        
        tracker["statistics"] = {
            "total_episodes": total_episodes,
            "filtered_count": sum(1 for ep in tracker.get("episodes", []) if ep.get("filter", False)),
            "unbuilt_count": unbuilt_total,
            "built_count": built_total
        }
        
        # 保存更新的 tracker
        try:
            # 备份原文件
            if TRACKER_PATH.exists():
                import os
                backup_path = TRACKER_PATH.with_suffix('.json.backup')
                # 如果备份文件已存在，先删除它
                if backup_path.exists():
                    backup_path.unlink()
                os.rename(TRACKER_PATH, backup_path)
            
            with open(TRACKER_PATH, 'w', encoding='utf-8') as f:
                json.dump(tracker, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Tracker 文件已更新: {TRACKER_PATH}")
        except Exception as e:
            logger.error(f"更新 tracker 文件失败: {e}")
    
    # 返回统计信息
    result = {
        "total_processed": len(unbuilt_episodes),
        "built_count": built_count,
        "failed_count": failed_count,
        "success_rate": built_count / len(unbuilt_episodes) if unbuilt_episodes else 0
    }
    
    logger.info(f"scan_and_build_scene 完成!")
    logger.info(f"处理结果: {json.dumps(result, indent=2)}")
    
    return result

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Scene Memory Unit 构建模块")
    parser.add_argument("--scan", action="store_true", help="运行 scan_now_scene 接口")
    parser.add_argument("--build", action="store_true", help="运行 scan_and_build_scene 接口")
    parser.add_argument("--delete", type=str, help="删除指定的 scene，格式: user_id:scene_id 或 user_id:all (删除所有)")
    
    args = parser.parse_args()
    
    if args.scan:
        result = scan_now_scene()
        print(f"扫描完成! 共处理 {len(result['episodes'])} 个 episodes")
        print(f"统计信息: {json.dumps(result['statistics'], indent=2)}")
    elif args.build:
        result = scan_and_build_scene()
        print(f"构建完成!")
        print(f"构建结果: {json.dumps(result, indent=2)}")
    elif args.delete:
        # 解析删除参数
        parts = args.delete.split(":")
        if len(parts) != 2:
            print("错误: 删除参数格式应为 user_id:scene_id 或 user_id:all")
            sys.exit(1)
        
        user_id = parts[0]
        target = parts[1]
        
        if target == "all":
            print(f"删除用户 {user_id} 的所有 scenes...")
            if delete_all_scenes_for_user(user_id):
                print(f"成功删除用户 {user_id} 的所有 scenes")
            else:
                print(f"删除用户 {user_id} 的所有 scenes 失败")
                sys.exit(1)
        else:
            scene_id = target
            print(f"删除 scene: {scene_id} (用户: {user_id})...")
            if delete_scene(scene_id, user_id):
                print(f"成功删除 scene: {scene_id}")
            else:
                print(f"删除 scene: {scene_id} 失败")
                sys.exit(1)
    else:
        # 默认行为：运行 scan_now_scene
        result = scan_now_scene()
        print(f"扫描完成! 共处理 {len(result['episodes'])} 个 episodes")
        print(f"统计信息: {json.dumps(result['statistics'], indent=2)}")