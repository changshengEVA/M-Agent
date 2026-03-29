#!/usr/bin/env python3
# 2026-01-20 changshengEVA
"""
Scene Formation 模块。
扫描所有 episode，使用 scene prompt 生成 scene（theme 和 diary）。
每个 scene 保存为单独文件，存储在 `{id}/scene` 目录，按编号从 `0001` 开始存储。
"""

import os
import json
import yaml
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Callable
from tqdm import tqdm

from m_agent.paths import CONFIG_DIR, memory_stage_dir
# 添加项目根目录到 Python 路径，确保可以导入 load_model

# 导入 episode 状管理器
from .episode_status_manager import get_status_manager
# 配置日志：只显示 WARNING 及以上级别，减少输出噪音
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 路径配置
DIALOGUES_ROOT = memory_stage_dir("default", "dialogues")
EPISODES_ROOT = memory_stage_dir("default", "episodes")
CONFIG_PATH = CONFIG_DIR / "prompt" / "scene.yaml"

def load_prompts(memory_owner_name: str = "changshengEVA") -> Dict:
    """Load scene prompts and replace the memory owner placeholder."""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 替换 prompts 中的 <memory_owner_name> 占位符
    if isinstance(config, dict):
        for key, value in config.items():
            if isinstance(value, str):
                config[key] = value.replace('<memory_owner_name>', memory_owner_name)
    
    return config

def ensure_directory(path: Path):
    """Ensure a directory exists."""
    path.mkdir(parents=True, exist_ok=True)

def scan_episode_files(episodes_root: Path = None) -> List[Path]:
    """
    Scan all episode files and return their paths.

    Args:
        episodes_root: Optional episodes root override.
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
    Return the scene output root.

    Layout:
        {episodes_root}/../scene
    """
    if episodes_root is None:
        episodes_root = EPISODES_ROOT
    return episodes_root.parent / "scene"

def get_next_scene_number(scene_root: Path) -> int:
    """
    Return the next available scene file number.
    """
    ensure_directory(scene_root)
    
    max_number = 0
    for file_path in scene_root.iterdir():
        if file_path.is_file() and file_path.suffix == '.json':
            try:
                # 文件名格式：00001.json、00002.json
                number_str = file_path.stem
                number = int(number_str)
                if number > max_number:
                    max_number = number
            except ValueError:
                continue
    
    return max_number + 1

def get_scene_path_by_number(scene_root: Path, number: int) -> Path:
    """
    Build a scene file path from its numeric id.

    Layout:
        {scene_root}/{number:05d}.json
    """
    return scene_root / f"{number:05d}.json"

def load_episodes(episode_file: Path) -> Dict:
    """加载 episode JSON 文件"""
    with open(episode_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_existing_scene_version(scene_root: Path, scene_file_name: str) -> Optional[str]:
    """
    Read the stored scene version from an existing scene file.
    """
    try:
        scene_path = scene_root / scene_file_name
        if not scene_path.exists():
            return None
        with open(scene_path, 'r', encoding='utf-8') as f:
            scene_data = json.load(f)
        return scene_data.get("scene_version")
    except Exception:
        return None

def find_dialogue_file(dialogue_id: str, dialogues_root: Path = None) -> Optional[Path]:
    """
    Find a dialogue JSON file by dialogue id.

    Args:
        dialogue_id: Dialogue identifier.
        dialogues_root: Optional dialogue root override.
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
    Merge episode metadata with the relevant dialogue turns.
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

def extract_episode_time_range(episode_meta: Dict, dialogue_data: Dict) -> Tuple[str, str]:
    """
    Extract start and end timestamps for an episode.
    """
    start_time = ""
    end_time = ""

    turn_span = episode_meta.get("turn_span", [])
    if not isinstance(turn_span, list) or len(turn_span) < 2:
        turn_span = [0, 0]

    try:
        start_id = int(turn_span[0])
        end_id = int(turn_span[1])
    except (TypeError, ValueError):
        start_id, end_id = 0, 0

    turns = dialogue_data.get("turns", [])
    if not isinstance(turns, list):
        turns = []

    # 优先按 turn_id 精确匹配
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        turn_id = turn.get("turn_id")
        if turn_id == start_id and not start_time:
            start_time = turn.get("timestamp", "") or ""
        if turn_id == end_id and not end_time:
            end_time = turn.get("timestamp", "") or ""
        if start_time and end_time:
            break

    # 若精确匹配失败，回退到 span 内第一条和最后一条 turn
    if not start_time or not end_time:
        span_turns: List[Dict] = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            try:
                turn_id = int(turn.get("turn_id", -1))
            except (TypeError, ValueError):
                continue
            if start_id <= turn_id <= end_id:
                span_turns.append(turn)
        if span_turns:
            if not start_time:
                start_time = span_turns[0].get("timestamp", "") or ""
            if not end_time:
                end_time = span_turns[-1].get("timestamp", "") or ""

    # 最后回退到 dialogue 元信息
    meta = dialogue_data.get("meta", {})
    if not isinstance(meta, dict):
        meta = {}
    if not start_time:
        start_time = meta.get("start_time", "") or ""
    if not end_time:
        end_time = meta.get("end_time", "") or ""

    return start_time, end_time

def call_openai_for_scene(
    episode_with_content: Dict,
    prompt_template: str,
    llm_model: Optional[Callable[[str], str]] = None
) -> Dict:
    """
    Generate a scene payload from one episode.
    """
    if llm_model is None:
        from m_agent.load_model.OpenAIcall import get_llm
        llm_model = get_llm(model_temperature=0.1)
    
    # 获取 LLM 实例，温度设为 0.1 以获得更确定性的输出
    
    # 将 episode JSON 转换为字符串用于插入
    episode_str = json.dumps(episode_with_content, ensure_ascii=False, indent=2)
    full_prompt = prompt_template.replace('<txt_string>', episode_str)
    
    try:
        response_text = llm_model(full_prompt)
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

def attach_theme_embedding(
    scene_result: Dict,
    embed_model: Optional[Callable[[Any], Any]] = None
) -> Dict:
    """
    Generate and attach an embedding for the scene theme."""
    scene_result = scene_result.copy()
    theme_text = scene_result.get("theme", "")
    if not isinstance(theme_text, str):
        theme_text = str(theme_text) if theme_text is not None else ""
        scene_result["theme"] = theme_text

    if not theme_text.strip():
        scene_result["theme_embedding"] = []
        return scene_result

    if embed_model is None:
        from m_agent.load_model.BGEcall import get_embed_model
        embed_model = get_embed_model()

    try:
        embedding = embed_model(theme_text)
        if isinstance(embedding, list):
            scene_result["theme_embedding"] = embedding
        else:
            logger.warning(f"theme embedding 格式异常，写入空列表: {theme_text[:50]}...")
            scene_result["theme_embedding"] = []
    except Exception as e:
        logger.error(f"生成 theme embedding 失败: {theme_text[:50]}..., 错误: {e}")
        raise

    return scene_result

def build_scene_structure(scene_number: int,
                         episode_meta: Dict,
                         scene_result: Dict,
                         scene_version: str = "v1",
                         memory_owner_name: str = "changshengEVA",
                         start_time: str = "",
                         end_time: str = "") -> Dict:
    """
    Build the final persisted scene structure.
    """
    scene_id = f"scene_{scene_number:05d}"
    episode_id = episode_meta.get('episode_id', '')
    dialogue_id = episode_meta.get('dialogue_id', '')
    turn_span = episode_meta.get('turn_span', [])
    
    # 确定语言：默认为中文，但可根据内容判断
    language = "zh-CN"  # 假设对话是中文
    
    return {
        "scene_id": scene_id,
        "scene_version": scene_version,
        "source": {
            "episodes": [
                {
                    "episode_id": episode_id,
                    "dialogue_id": dialogue_id,
                    "turn_span": turn_span,
                    "start_time": start_time,
                    "end_time": end_time
                }
            ]
        },
        "meta": {
            "created_at": datetime.utcnow().isoformat() + "Z",
            "memory_owner": memory_owner_name,
            "language": language
        },
        "theme": scene_result.get("theme", ""),
        "theme_embedding": scene_result.get("theme_embedding", []),
        "diary": scene_result.get("diary", "")
    }

def save_scenes_as_individual_files(scenes: List[Dict], scene_root: Path):
    """
    Save scene payloads as numbered JSON files.
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
    Extract a workflow id from the episodes path.
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
                        prompt_version: str = "v1",
                        memory_owner_name: str = "changshengEVA",
                        embed_model: Optional[Callable[[Any], Any]] = None,
                        llm_model: Optional[Callable[[str], str]] = None) -> bool:
    """
    Process one episode file and generate any required scenes.
    """
    try:
        # 确定 episodes_root
        if episodes_root is None:
            episodes_root = EPISODES_ROOT
        
        # 提取工作流 ID
        workflow_id = extract_workflow_id_from_path(episodes_root)
        
        # 获取状管理器
        status_manager = get_status_manager(workflow_id=workflow_id)
        
        # 加载 episode 数据
        episode_data = load_episodes(episode_file)
        dialogue_id = episode_data.get("dialogue_id", "")
        effective_scene_root = scene_root if scene_root is not None else get_scene_root(episodes_root)
        
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
                existing_status = status_manager.get_episode(episode_key) or {}
                scene_file_name = existing_status.get("scene_file")
                existing_version = None
                if scene_file_name:
                    existing_version = get_existing_scene_version(effective_scene_root, scene_file_name)

                if existing_version == prompt_version:
                    logger.info(f"Episode {episode_key} 已生成 scene（版本 {existing_version}），跳过")
                    skipped_count += 1
                    continue

                logger.info(
                    f"Episode {episode_key} has scene version {existing_version}; "
                    f"regenerating with prompt version {prompt_version}"
                )
            
            # 检查 scene_available 状态
            episode_status = status_manager.get_episode(episode_key)
            if episode_status is None:
                logger.warning(f"Episode {episode_key} 未在 episode_situation.json 中找到，跳过")
                skipped_count += 1
                continue
            if not episode_status.get("scene_available", False):
                logger.info(f"Skipping episode {episode_key}: scene_available is False")
                skipped_count += 1
                continue
            
            # 构建包含完整内容的 episode
            episode_with_content = build_episode_with_content(episode_meta, dialogue_data)
            
            # 获取 prompt 模板
            prompt_key = f"scene_former_{prompt_version}"
            prompt_template = prompts.get(prompt_key, "")
            
            if not prompt_template:
                logger.error(f"Prompt template not found: {prompt_key}")
                return False
            
            try:
                scene_result = call_openai_for_scene(
                    episode_with_content,
                    prompt_template,
                    llm_model=llm_model
                )
                # 验证结果包含 theme 和 diary
                if "theme" not in scene_result or "diary" not in scene_result:
                    logger.error(f"Scene result is missing theme or diary: {scene_result}")
                    continue
                scene_result = attach_theme_embedding(scene_result, embed_model=embed_model)
                
                # Store intermediate scene data before assigning file numbers.
                scenes.append(
                    {
                        "episode_meta": episode_meta,
                        "scene_result": scene_result,
                        "episode_key": episode_key,
                    }
                )
            except Exception as e:
                logger.error(f"Failed to generate scene for episode {episode_id}: {e}")
                # 继续处理其他 episode
                continue
        
        if not scenes:
            if skipped_count > 0:
                logger.info(f"Skipped all {skipped_count} episodes for dialogue {dialogue_id}")
            else:
                logger.info(f"对话 {dialogue_id} 没有成功生成任何 scene")
            return True
        
        # 分配 scene 编号并构建最终 scene 结构
        if scene_root is None:
            scene_root = effective_scene_root
        
        # 获取下一个起始编号
        start_number = get_next_scene_number(scene_root)
        final_scenes = []
        for i, scene_data in enumerate(scenes):
            scene_number = start_number + i
            start_time, end_time = extract_episode_time_range(
                scene_data["episode_meta"],
                dialogue_data
            )
            final_scene = build_scene_structure(
                scene_number,
                scene_data["episode_meta"],
                scene_data["scene_result"],
                scene_version=prompt_version,
                memory_owner_name=memory_owner_name,
                start_time=start_time,
                end_time=end_time
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
        
        logger.info(
            f"Generated {len(saved_files)} scene files for dialogue {dialogue_id}, "
            f"skipped {skipped_count}, output={scene_root}, prompt_version={prompt_version}"
        )
        
        return True
        
    except Exception as e:
        logger.error(f"处理 episode 文件 {episode_file} 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def scan_and_form_scenes(use_tqdm: bool = True,
                        force_update: bool = False,
                        prompt_version: str = "v1",
                        dialogues_root: Path = None,
                        episodes_root: Path = None,
                        scene_root: Path = None,
                        memory_owner_name: str = "changshengEVA",
                        embed_model: Optional[Callable[[Any], Any]] = None,
                        llm_model: Optional[Callable[[str], str]] = None):
    """
    Scan episode files and generate scene outputs.

    Args:
        use_tqdm: Whether to render a progress bar.
        force_update: Whether to regenerate existing scene files.
        prompt_version: Prompt version used for scene generation.
        dialogues_root: Optional dialogue root override.
        episodes_root: Optional episodes root override.
        scene_root: Optional scene root override.
        memory_owner_name: Placeholder value injected into prompt templates.
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

    # 验证 prompt_version 是否有效
    expected_prompt_key = f"scene_former_{prompt_version}"
    if expected_prompt_key not in prompts:
        logger.error(f"无效的 scene prompt 版本: {prompt_version}，未找到模板: {expected_prompt_key}")
        available = [k for k in prompts.keys() if k.startswith("scene_former_")]
        logger.error(f"可用 scene 模板: {available}")
        return
    
    # 扫描所有 episode 文件
    episode_files = scan_episode_files(episodes_root)
    
    if not episode_files:
        # 没有需要处理的文件，静默退出
        logger.info("没有找到 episode 文件")
        return
    
    # 处理文件
    if use_tqdm:
        file_iter = tqdm(episode_files, desc=f"生成 scenes (prompt: {prompt_version})")
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
            prompt_version=prompt_version,
            memory_owner_name=memory_owner_name,
            embed_model=embed_model,
            llm_model=llm_model
        ):
            success_count += 1
    
    logger.info(
        f"成功处理 {success_count}/{len(episode_files)} 个 episode 文件，使用 prompt 版本: {prompt_version}"
    )

def clear_all_scenes(scene_root: Path = None, confirm: bool = False):
    """
    Remove generated scene files.

    Args:
        scene_root: Optional scene root override.
        confirm: When False, show a preview only.
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
        print("\n这只是预览要实际删除这些文件，请运行: clear_all_scenes(confirm=True)")
        return
    
    # 实际删除文件
    deleted_count = 0
    for scene_file in scene_files:
        try:
            scene_file.unlink()
            print(f"已删除 {scene_file}")
            deleted_count += 1
        except Exception as e:
            print(f"删除失败 {scene_file}: {e}")
    
    print(f"\nDeleted {deleted_count}/{len(scene_files)} files.")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Scene generation utility")
    parser.add_argument("--scan", action="store_true", help="scan and generate scenes")
    parser.add_argument("--clear", action="store_true", help="remove generated scene files")
    parser.add_argument("--confirm", action="store_true", help="confirm deletion when used with --clear")
    parser.add_argument("--force-update", action="store_true",
                       help="regenerate scene files even if they already exist")
    parser.add_argument("--prompt-version", default="v1", help="scene prompt version")
    
    args = parser.parse_args()
    
    if args.clear:
        clear_all_scenes(confirm=args.confirm)
    elif args.scan:
        scan_and_form_scenes(
            force_update=args.force_update,
            prompt_version=args.prompt_version
        )
    else:
        # 默认行为：扫描并生成
        scan_and_form_scenes(
            force_update=args.force_update,
            prompt_version=args.prompt_version
        )
