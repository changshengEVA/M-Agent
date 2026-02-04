#!/usr/bin/env python3
# 2026-01-20 changshengEVA
"""
Scene Formation 模块。
扫描所有 episode，使用 scene prompt 生成 scene（theme 和 diary）。
每个 scene 保存为单独文件，存储在 {id}/scene 目录，按编号从00001开始存储。
"""

import os
import sys
import json
import yaml
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from tqdm import tqdm

# 添加项目根目录到 Python 路径，确保可以导入 load_model
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 导入 episode 状态管理器
try:
    from .episode_status_manager import get_status_manager
except ImportError:
    # 如果相对导入失败，尝试绝对导入
    sys.path.insert(0, str(Path(__file__).parent))
    from episode_status_manager import get_status_manager

# 配置日志：只显示 WARNING 及以上级别，减少输出噪音
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 路径配置
DIALOGUES_ROOT = PROJECT_ROOT / "data" / "memory" / "default" / "dialogues"
EPISODES_ROOT = PROJECT_ROOT / "data" / "memory" / "default" / "episodes"
CONFIG_PATH = PROJECT_ROOT / "config" / "prompt" / "scene.yaml"

def load_prompts(memory_owner_name: str = "changshengEVA") -> Dict:
    """从 config/prompt/scene.yaml 加载 prompts，并替换 <memory_owner_name> 占位符"""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 替换 prompts 中的 <memory_owner_name> 占位符
    if isinstance(config, dict):
        for key, value in config.items():
            if isinstance(value, str):
                config[key] = value.replace('<memory_owner_name>', memory_owner_name)
    
    return config

def ensure_directory(path: Path):
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)

def scan_episode_files(episodes_root: Path = None) -> List[Path]:
    """
    扫描所有 episode 文件。
    返回所有找到的 episode 文件路径列表。
    
    Args:
        episodes_root: episodes根目录，如果为None则使用默认的EPISODES_ROOT
    """
    if episodes_root is None:
        episodes_root = EPISODES_ROOT
    
    episode_files = []
    # 扫描 by_dialogue 目录
    by_dialogue_dir = episodes_root / "by_dialogue"
    if not by_dialogue_dir.exists():
        return episode_files
    
    for dialogue_dir in by_dialogue_dir.iterdir():
        if dialogue_dir.is_dir():
            episode_file = dialogue_dir / "episodes_v1.json"
            if episode_file.exists():
                episode_files.append(episode_file)
    
    return episode_files

def get_scene_root(episodes_root: Path = None) -> Path:
    """
    获取 scene 根目录。
    格式: {episodes_root}/../scene
    """
    if episodes_root is None:
        episodes_root = EPISODES_ROOT
    return episodes_root.parent / "scene"

def get_next_scene_number(scene_root: Path) -> int:
    """
    获取下一个 scene 文件编号。
    扫描现有文件，返回最大的编号+1，如果没有任何文件则返回1。
    """
    ensure_directory(scene_root)
    
    max_number = 0
    for file_path in scene_root.iterdir():
        if file_path.is_file() and file_path.suffix == '.json':
            try:
                # 文件名格式: 00001.json, 00002.json 等
                number_str = file_path.stem
                number = int(number_str)
                if number > max_number:
                    max_number = number
            except ValueError:
                continue
    
    return max_number + 1

def get_scene_path_by_number(scene_root: Path, number: int) -> Path:
    """
    根据编号生成 scene 文件路径。
    格式: {scene_root}/{number:05d}.json
    """
    return scene_root / f"{number:05d}.json"

def load_episodes(episode_file: Path) -> Dict:
    """加载 episode JSON 文件"""
    with open(episode_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def find_dialogue_file(dialogue_id: str, dialogues_root: Path = None) -> Optional[Path]:
    """
    根据 dialogue_id 查找对应的对话文件。
    搜索 dialogues 目录下的所有子目录。
    
    Args:
        dialogue_id: 对话ID
        dialogues_root: 对话根目录，如果为None则使用默认的DIALOGUES_ROOT
    """
    if dialogues_root is None:
        dialogues_root = DIALOGUES_ROOT
    
    # 搜索所有可能的目录结构
    search_patterns = [
        ("by_user", "*", "*"),      # by_user/{user_id}/{year-month}/
        ("by_flipflop", "*", "*"),  # by_flipflop/{flipflop_id}/{year-month}/
        ("", "*", "*"),             # 直接搜索根目录下的 {user_id}/{year-month}/
    ]
    
    for base_dir, user_pattern, date_pattern in search_patterns:
        search_dir = dialogues_root / base_dir
        if not search_dir.exists():
            continue
            
        # 遍历用户目录
        for user_dir in search_dir.iterdir():
            if user_dir.is_dir():
                # 遍历年月目录
                for year_month_dir in user_dir.iterdir():
                    if year_month_dir.is_dir():
                        dialogue_file = year_month_dir / f"{dialogue_id}.json"
                        if dialogue_file.exists():
                            return dialogue_file
    
    # 如果没有找到，尝试递归搜索整个目录
    for file_path in dialogues_root.rglob(f"{dialogue_id}.json"):
        if file_path.is_file():
            return file_path
    
    return None

def load_dialogue(dialogue_file: Path) -> Dict:
    """加载对话 JSON 文件"""
    with open(dialogue_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def build_episode_with_content(episode_meta: Dict, dialogue_data: Dict) -> Dict:
    """
    根据 episode 元数据和对话数据构建完整的 episode 内容。
    
    Args:
        episode_meta: episode 元数据（来自 episodes_v1.json）
        dialogue_data: 完整的对话数据
        
    Returns:
        包含完整内容的 episode 字典
    """
    # 提取 turn_span
    turn_span = episode_meta.get('turn_span', [0, 0])
    start_id, end_id = turn_span[0], turn_span[1]
    
    # 提取对应的对话轮次
    turns = dialogue_data.get('turns', [])
    episode_turns = []
    
    for turn in turns:
        turn_id = turn.get('turn_id', -1)
        if start_id <= turn_id <= end_id:
            episode_turns.append(turn)
    
    # 构建完整的 episode 结构
    episode_with_content = episode_meta.copy()
    episode_with_content['turns'] = episode_turns
    episode_with_content['dialogue_content'] = {
        'dialogue_id': dialogue_data.get('dialogue_id', ''),
        'user_id': dialogue_data.get('user_id', ''),
        'participants': dialogue_data.get('participants', []),
        'meta': dialogue_data.get('meta', {})
    }
    
    return episode_with_content

def call_openai_for_scene(episode_with_content: Dict, prompt_template: str) -> Dict:
    """
    调用 OpenAI 进行 scene 生成。
    返回解析后的 JSON 结果（包含 theme 和 diary）。
    """
    from load_model.OpenAIcall import get_llm
    
    # 获取 LLM 实例，温度设为 0.1 以获得更确定性的输出
    llm = get_llm(model_temperature=0.1)
    
    # 将 episode JSON 转换为字符串用于插入
    episode_str = json.dumps(episode_with_content, ensure_ascii=False, indent=2)
    full_prompt = prompt_template.replace('<txt_string>', episode_str)
    
    try:
        response_text = llm(full_prompt)
        # 解析 JSON 响应
        # 响应可能包含额外的文本，尝试提取 JSON 部分
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            response_text = json_match.group(0)
        
        result = json.loads(response_text)
        return result
    except json.JSONDecodeError as e:
        logger.error(f"解析 OpenAI 响应失败: {e}")
        logger.error(f"响应文本: {response_text[:500]}...")
        raise
    except Exception as e:
        logger.error(f"调用 OpenAI 失败: {e}")
        raise

def build_scene_structure(scene_number: int, episode_meta: Dict, scene_result: Dict, memory_owner_name: str = "changshengEVA") -> Dict:
    """
    构建最终的 scene 结构，符合要求的格式。
    
    Args:
        scene_number: scene 编号（用于生成 scene_id）
        episode_meta: episode 元数据（包含 episode_id, dialogue_id, turn_span）
        scene_result: 包含 theme 和 diary 的字典
        memory_owner_name: 记忆所有者的名称，用于 meta 字段
        
    Returns:
        完整的 scene 数据字典
    """
    scene_id = f"scene_{scene_number:05d}"
    episode_id = episode_meta.get('episode_id', '')
    dialogue_id = episode_meta.get('dialogue_id', '')
    turn_span = episode_meta.get('turn_span', [])
    
    # 确定语言：默认为中文，但可根据内容判断
    language = "zh-CN"  # 假设对话是中文
    
    return {
        "scene_id": scene_id,
        "scene_version": "v1",
        "source": {
            "episodes": [
                {
                    "episode_id": episode_id,
                    "dialogue_id": dialogue_id,
                    "turn_span": turn_span
                }
            ]
        },
        "meta": {
            "created_at": datetime.utcnow().isoformat() + "Z",
            "memory_owner": memory_owner_name,
            "language": language
        },
        "theme": scene_result.get("theme", ""),
        "diary": scene_result.get("diary", "")
    }

def save_scenes_as_individual_files(scenes: List[Dict], scene_root: Path):
    """
    将每个 scene 保存为单独的文件，按照编号从00001开始存储。
    
    Args:
        scenes: scene 列表
        scene_root: scene 根目录
    """
    ensure_directory(scene_root)
    
    # 获取下一个起始编号
    start_number = get_next_scene_number(scene_root)
    
    saved_files = []
    for i, scene in enumerate(scenes):
        file_number = start_number + i
        file_path = get_scene_path_by_number(scene_root, file_number)
        
        # 直接保存 scene 字典（不添加额外字段）
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(scene, f, ensure_ascii=False, indent=2)
        
        saved_files.append(file_path)
    
    return saved_files

def extract_workflow_id_from_path(episodes_root: Path) -> str:
    """
    从 episodes 根目录路径中提取工作流 ID。
    路径格式: .../data/memory/{workflow_id}/episodes
    如果无法提取，返回 "default"。
    """
    try:
        # 将路径转换为字符串并标准化
        parts = episodes_root.parts
        # 查找 "memory" 的索引
        if "memory" in parts:
            idx = parts.index("memory")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        # 如果找不到，尝试从路径名推断
        # 假设 episodes_root 的父目录是 workflow_id
        workflow_id = episodes_root.parent.name
        if workflow_id and workflow_id != "episodes":
            return workflow_id
    except Exception:
        pass
    return "default"

def process_episode_file(episode_file: Path,
                        prompts: Dict,
                        dialogues_root: Path = None,
                        episodes_root: Path = None,
                        scene_root: Path = None,
                        force_update: bool = False,
                        memory_owner_name: str = "changshengEVA") -> bool:
    """
    处理单个 episode 文件，生成 scene。
    
    Args:
        episode_file: episode 文件路径
        prompts: prompt 模板字典
        dialogues_root: 对话根目录
        episodes_root: episodes根目录
        scene_root: scene根目录（如果为None，则使用默认位置）
        force_update: 是否强制更新，即使已生成也重新生成
        memory_owner_name: 记忆所有者的名称，用于 scene 的 meta 字段
    """
    try:
        # 确定 episodes_root
        if episodes_root is None:
            episodes_root = EPISODES_ROOT
        
        # 提取工作流 ID
        workflow_id = extract_workflow_id_from_path(episodes_root)
        
        # 获取状态管理器
        status_manager = get_status_manager(workflow_id=workflow_id)
        
        # 加载 episode 数据
        episode_data = load_episodes(episode_file)
        dialogue_id = episode_data.get("dialogue_id", "")
        
        # 查找并加载对应的对话文件
        dialogue_file = find_dialogue_file(dialogue_id, dialogues_root)
        if not dialogue_file:
            logger.error(f"找不到对话文件: {dialogue_id}")
            return False
        
        dialogue_data = load_dialogue(dialogue_file)
        
        # 为每个 episode 生成 scene
        scenes = []
        skipped_count = 0
        
        for episode_meta in episode_data.get("episodes", []):
            episode_id = episode_meta.get("episode_id")
            episode_key = f"{dialogue_id}:{episode_id}"
            
            # 检查是否已生成 scene
            if not force_update and status_manager.is_scene_generated(episode_key):
                logger.info(f"Episode {episode_key} 已生成 scene，跳过")
                skipped_count += 1
                continue
            
            # 检查 scene_available 状态
            episode_status = status_manager.get_episode(episode_key)
            if episode_status is None:
                logger.warning(f"Episode {episode_key} 未在 episode_situation.json 中找到，跳过")
                skipped_count += 1
                continue
            if not episode_status.get("scene_available", False):
                logger.info(f"Episode {episode_key} scene_available 为 False，跳过")
                skipped_count += 1
                continue
            
            # 构建包含完整内容的 episode
            episode_with_content = build_episode_with_content(episode_meta, dialogue_data)
            
            # 获取 prompt 模板
            prompt_key = "scene_former_v1"
            prompt_template = prompts.get(prompt_key, "")
            
            if not prompt_template:
                logger.error(f"未找到 prompt 模板: {prompt_key}")
                return False
            
            try:
                scene_result = call_openai_for_scene(episode_with_content, prompt_template)
                # 验证结果包含 theme 和 diary
                if "theme" not in scene_result or "diary" not in scene_result:
                    logger.error(f"scene 结果缺少 theme 或 diary: {scene_result}")
                    continue
                
                # 构建 scene 结构（需要 scene_number，但此时未知，先占位）
                # 我们将稍后分配编号
                scenes.append({
                    "episode_meta": episode_meta,
                    "scene_result": scene_result,
                    "episode_key": episode_key
                })
            except Exception as e:
                logger.error(f"为 episode {episode_id} 生成 scene 失败: {e}")
                # 继续处理其他 episode
                continue
        
        if not scenes:
            if skipped_count > 0:
                logger.info(f"对话 {dialogue_id} 的所有 {skipped_count} 个 episode 已生成 scene，跳过")
            else:
                logger.info(f"对话 {dialogue_id} 没有成功生成任何 scene")
            return True
        
        # 分配 scene 编号并构建最终 scene 结构
        if scene_root is None:
            scene_root = get_scene_root(episodes_root)
        
        # 获取下一个起始编号
        start_number = get_next_scene_number(scene_root)
        final_scenes = []
        for i, scene_data in enumerate(scenes):
            scene_number = start_number + i
            final_scene = build_scene_structure(
                scene_number,
                scene_data["episode_meta"],
                scene_data["scene_result"],
                memory_owner_name=memory_owner_name
            )
            final_scenes.append({
                "scene": final_scene,
                "episode_key": scene_data["episode_key"]
            })
        
        # 保存 scene 文件
        saved_scenes = [item["scene"] for item in final_scenes]
        saved_files = save_scenes_as_individual_files(saved_scenes, scene_root)
        
        # 更新状态
        for i, item in enumerate(final_scenes):
            episode_key = item["episode_key"]
            scene_file = saved_files[i].name if i < len(saved_files) else f"unknown_{i}.json"
            created_at = item["scene"].get("meta", {}).get("created_at")
            status_manager.mark_scene_generated(episode_key, scene_file, created_at)
        
        logger.info(f"为对话 {dialogue_id} 生成 {len(saved_files)} 个 scene 文件，跳过 {skipped_count} 个已生成的，保存到 {scene_root}")
        
        return True
        
    except Exception as e:
        logger.error(f"处理 episode 文件 {episode_file} 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def scan_and_form_scenes(use_tqdm: bool = True,
                        force_update: bool = False,
                        dialogues_root: Path = None,
                        episodes_root: Path = None,
                        scene_root: Path = None,
                        memory_owner_name: str = "changshengEVA"):
    """
    主函数：扫描所有 episode 文件，为需要生成 scene 的对话创建 scene。
    
    Args:
        use_tqdm: 是否使用 tqdm 显示进度条（默认 True）
        force_update: 是否强制更新 scene 文件（即使文件已存在，默认 False）
        dialogues_root: 对话根目录，如果为None则使用默认的DIALOGUES_ROOT
        episodes_root: episodes根目录，如果为None则使用默认的EPISODES_ROOT
        scene_root: scene根目录，如果为None则使用默认位置（episodes_root/../scene）
        memory_owner_name: 记忆所有者的名称，用于替换prompt中的<memory_owner_name>占位符
    """
    # 确定使用的根目录
    if episodes_root is None:
        episodes_root = EPISODES_ROOT
    if dialogues_root is None:
        dialogues_root = DIALOGUES_ROOT
    if scene_root is None:
        scene_root = get_scene_root(episodes_root)
    
    # 确保 scene 根目录存在
    ensure_directory(scene_root)
    
    # 加载 prompts
    prompts = load_prompts(memory_owner_name)
    if not prompts:
        logger.error("未找到 scene prompts")
        return
    
    # 扫描所有 episode 文件
    episode_files = scan_episode_files(episodes_root)
    
    if not episode_files:
        # 没有需要处理的文件，静默退出
        logger.info("没有找到 episode 文件")
        return
    
    # 处理文件
    if use_tqdm:
        file_iter = tqdm(episode_files, desc="生成 scenes")
    else:
        file_iter = episode_files
    
    success_count = 0
    for episode_file in file_iter:
        if process_episode_file(
            episode_file,
            prompts,
            dialogues_root,
            episodes_root,
            scene_root,
            force_update=force_update,
            memory_owner_name=memory_owner_name
        ):
            success_count += 1
    
    logger.info(f"成功处理 {success_count}/{len(episode_files)} 个 episode 文件")

def clear_all_scenes(scene_root: Path = None, confirm: bool = False):
    """
    清理所有 scene 文件。
    
    Args:
        scene_root: scene根目录，如果为None则使用默认位置
        confirm: 如果为 True，则实际删除文件；如果为 False，只显示将要删除的文件列表
    """
    if scene_root is None:
        scene_root = get_scene_root()
    
    if not scene_root.exists():
        print(f"scene 目录不存在: {scene_root}")
        return
    
    scene_files = []
    for file_path in scene_root.iterdir():
        if file_path.is_file() and file_path.suffix == '.json':
            try:
                # 只删除数字命名的文件
                int(file_path.stem)
                scene_files.append(file_path)
            except ValueError:
                continue
    
    if not scene_files:
        print("没有找到 scene 文件")
        return
    
    print(f"找到 {len(scene_files)} 个 scene 文件:")
    for scene_file in scene_files:
        print(f"  - {scene_file}")
    
    if not confirm:
        print("\n这只是预览。要实际删除这些文件，请运行: clear_all_scenes(confirm=True)")
        return
    
    # 实际删除文件
    deleted_count = 0
    for scene_file in scene_files:
        try:
            scene_file.unlink()
            print(f"已删除: {scene_file}")
            deleted_count += 1
        except Exception as e:
            print(f"删除失败 {scene_file}: {e}")
    
    print(f"\n成功删除 {deleted_count}/{len(scene_files)} 个文件")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Scene Formation 模块")
    parser.add_argument("--scan", action="store_true", help="扫描并生成 scenes")
    parser.add_argument("--clear", action="store_true", help="清理所有 scene 文件")
    parser.add_argument("--confirm", action="store_true", help="确认删除（与 --clear 一起使用）")
    parser.add_argument("--force-update", action="store_true",
                       help="强制更新 scene 文件（即使文件已存在）")
    
    args = parser.parse_args()
    
    if args.clear:
        clear_all_scenes(confirm=args.confirm)
    elif args.scan:
        scan_and_form_scenes(
            force_update=args.force_update
        )
    else:
        # 默认行为：扫描并生成
        scan_and_form_scenes(
            force_update=args.force_update
        )
