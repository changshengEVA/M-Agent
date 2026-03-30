#!/usr/bin/env python3
# 2025-12-27 changshengEVA
"""
Episode Information Scoring 模块。
扫描 episodes 目录下的 episode 文件，对每个 episode 进行信息评分评估。
提供清理 qualifications 文件的接口。
"""

import os
import json
import yaml
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable
from tqdm import tqdm

from m_agent.config_paths import EPISODE_PROMPT_CONFIG_PATH
from m_agent.paths import memory_stage_dir
from m_agent.prompt_utils import load_resolved_prompt_config, normalize_prompt_language
# 添加项目根目录到 Python 路径，确保可以导入 load_model

# 配置日志：只显示 WARNING 及以上级别，减少输出噪音
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 路径配置
DIALOGUES_ROOT = memory_stage_dir("default", "dialogues")
EPISODES_ROOT = memory_stage_dir("default", "episodes")
CONFIG_PATH = EPISODE_PROMPT_CONFIG_PATH

def load_prompts(memory_owner_name: str = "changshengEVA", prompt_language: str = "zh") -> Dict:
    """Load scoring prompts and replace the memory owner placeholder."""
    config = load_resolved_prompt_config(
        CONFIG_PATH,
        language=normalize_prompt_language(prompt_language),
    )
    prompts = config.get('scoring_sys', {})
    # 替换 prompts 中的 <memory_owner_name> 占位符
    if isinstance(prompts, dict):
        for key, value in prompts.items():
            if isinstance(value, str):
                prompts[key] = value.replace('<memory_owner_name>', memory_owner_name)
            elif isinstance(value, dict):
                # 处理嵌套字典（例如 scoring_sys 中的各个评分维度）
                for sub_key, sub_value in value.items():
                    if isinstance(sub_value, str):
                        value[sub_key] = sub_value.replace('<memory_owner_name>', memory_owner_name)
    return prompts

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

def get_qualification_path(episode_file: Path) -> Path:
    """
    Build the qualification file path for an episode file.

    Layout:
        episodes/by_dialogue/{dialogue_id}/qualifications_v1.json
    """
    dialogue_dir = episode_file.parent
    return dialogue_dir / "qualifications_v1.json"

def episode_needs_qualification(episode_file: Path) -> bool:
    """Return whether a qualification file still needs to be generated."""
    qualification_file = get_qualification_path(episode_file)
    return not qualification_file.exists()

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
    
    # 如果没有找到，尝试归搜索整个目录
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

def call_openai_for_scoring(
    episode_with_content: Dict,
    system_prompt: str,
    user_prompt_template: str,
    llm_model: Optional[Callable[[str], str]] = None
) -> Dict:
    """
    调用 OpenAI 进行单个评分维度的评分。
    返回解析后的 JSON 结果。
    """
    if llm_model is None:
        from m_agent.load_model.OpenAIcall import get_llm
        llm_model = get_llm(model_temperature=0.1)
    
    # 获取 LLM 实例，温度设为 0.1 以获得更确定性的输出
    
    # 将 episode JSON 转换为字符串用于插入
    episode_str = json.dumps(episode_with_content, ensure_ascii=False, indent=2)
    user_prompt = user_prompt_template.replace('<EPISODE_JSON>', episode_str)
    
    # 组合 system 和 user prompt（用于 text completion 模型）
    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    
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


def call_openai_for_qualification(
    episode_with_content: Dict,
    scoring_sys: Dict,
    llm_model: Optional[Callable[[str], str]] = None
) -> Dict:
    """
    调用 OpenAI 进行多个评分维度的评分。
    依次按照 scoring_sys 中的内容分别打分，返回所有评分结果的字典。
    """
    scoring_results = {}
    
    for scoring_name, scoring_config in scoring_sys.items():
        score_name = scoring_config.get('score_name', scoring_name)
        system_prompt = scoring_config.get('system_prompt', '')
        user_prompt = scoring_config.get('user_prompt', '')
        
        if not system_prompt or not user_prompt:
            logger.warning(f"Skipping scoring module {scoring_name}: missing prompt text")
            continue
        
        try:
            result = call_openai_for_scoring(
                episode_with_content,
                system_prompt,
                user_prompt,
                llm_model=llm_model
            )
            scoring_results[score_name] = result
        except Exception as e:
            logger.error(f"评分模块 {scoring_name} 失败: {e}")
            # 继续处理其他评分模块
            continue
    
    return scoring_results

def build_qualification_structure(dialogue_id: str, episode_id: str, scoring_results: Dict) -> Dict:
    """
    构建最终的 qualification 结构，符合要求的格式。
    只保留 scoring_sys 中定义的评分维度，不添加额外字段。
    
    Args:
        dialogue_id: 对话ID
        episode_id: 片段ID
        scoring_results: 字典，键为 score_name，值为评分模块的原始输出
    """
    scene_potential_score = {}
    rationale = {}
    
    # 遍历所有评分结果，提取分数和理由
    for score_name, result in scoring_results.items():
        # 根据用户要求，字段名使用 score_name + "_novelty"
        expected_field = f"{score_name}_novelty"
        score_value = None
        
        # 尝试从预期字段获取分数
        if expected_field in result and isinstance(result[expected_field], int):
            score_value = result[expected_field]
            scene_potential_score[expected_field] = score_value
            rationale[expected_field] = result.get("rationale", "No rationale provided")
        else:
            # 如果预期字段不存在，尝试查找其他分数字段
            for key, value in result.items():
                if isinstance(value, int) and (key.endswith("_novelty") or key.endswith("_score")):
                    score_value = value
                    scene_potential_score[key] = score_value
                    rationale[key] = result.get("rationale", "No rationale provided")
                    break
            if score_value is None:
                # 如果没有找到分数字段，跳过该评分模块
                logger.warning(f"评分模块 {score_name} 未找到分数字段，跳过")
    
    # 如果没有任何评分结果，创建空结构
    if not scene_potential_score:
        scene_potential_score = {}
        rationale = {}
    
    # 决策字段暂时设为 pending（下游程序会自动检测）
    decision = "pending"
    
    return {
        "episode_id": episode_id,
        "dialogue_id": dialogue_id,
        "qualification_version": "v1",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "scene_potential_score": scene_potential_score,
        "decision": decision,
        "rationale": rationale
    }

def process_episode_file(
    episode_file: Path,
    prompts: Dict,
    dialogues_root: Path = None,
    memory_owner_name: str = "changshengEVA",
    llm_model: Optional[Callable[[str], str]] = None
) -> bool:
    """处理单个 episode 文件，生成 qualification。"""
    try:
        # 加载 episodes
        episode_data = load_episodes(episode_file)
        dialogue_id = episode_data.get('dialogue_id', episode_file.parent.name)
        
        # 查找并加载对应的对话文件
        dialogue_file = find_dialogue_file(dialogue_id, dialogues_root)
        if not dialogue_file:
            logger.error(f"找不到对话文件: {dialogue_id}")
            return False
        
        dialogue_data = load_dialogue(dialogue_file)
        
        # 获取所有 episodes
        episodes = episode_data.get('episodes', [])
        if not episodes:
            logger.warning(f"Skipping dialogue {dialogue_id}: no episodes found")
            return False
        
        # 为每个 episode 生成 qualification
        qualifications = []
        for episode in episodes:
            episode_id = episode.get('episode_id', 'unknown')
            
            # 构建包含完整内容的 episode
            episode_with_content = build_episode_with_content(episode, dialogue_data)
            
            # 调用 OpenAI 进行 qualification
            openai_result = call_openai_for_qualification(
                episode_with_content,
                prompts,
                llm_model=llm_model
            )
            
            # 构建最终结果
            qualification = build_qualification_structure(dialogue_id, episode_id, openai_result)
            qualifications.append(qualification)
        
        # 保存文件
        qualification_file = get_qualification_path(episode_file)
        save_qualifications(qualifications, qualification_file)
        
        return True
    except Exception as e:
        logger.error(f"处理 episode 文件 {episode_file} 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def save_qualifications(qualifications: List[Dict], qualification_file: Path):
    """Save qualification results to disk."""
    ensure_directory(qualification_file.parent)
    with open(qualification_file, 'w', encoding='utf-8') as f:
        json.dump({
            "qualifications": qualifications,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "qualification_version": "v1"
        }, f, ensure_ascii=False, indent=2)

def scan_and_qualify_episodes(
    use_tqdm: bool = True,
    dialogues_root: Path = None,
    episodes_root: Path = None,
    memory_owner_name: str = "changshengEVA",
    prompt_language: str = "zh",
    llm_model: Optional[Callable[[str], str]] = None
):
    """
    Scan episode files and generate qualification outputs when needed.

    Args:
        use_tqdm: Whether to render a progress bar.
        dialogues_root: Optional dialogue root override.
        episodes_root: Optional episodes root override.
        memory_owner_name: Placeholder value injected into prompt templates.
    """
    # 确定使用的根目录
    if episodes_root is None:
        episodes_root = EPISODES_ROOT
    if dialogues_root is None:
        dialogues_root = DIALOGUES_ROOT
    
    # 确保 episodes 根目录存在
    ensure_directory(episodes_root)
    
    # 加载 prompts，并替换占位符
    prompts = load_prompts(memory_owner_name, prompt_language=prompt_language)
    if not prompts:
        logger.error("未找到 episode_information_scoring prompts")
        return
    
    # 扫描所有 episode 文件
    episode_files = scan_episode_files(episodes_root)
    
    # 过滤需要处理的文件
    files_to_process = []
    for file in episode_files:
        if episode_needs_qualification(file):
            files_to_process.append(file)
    
    if not files_to_process:
        # 没有需要处理的文件，静默退出
        logger.info("没有需要处理的 episode 文件")
        return
    
    # 处理文件
    if use_tqdm:
        file_iter = tqdm(files_to_process, desc="评估 episodes")
    else:
        file_iter = files_to_process
    
    success_count = 0
    for episode_file in file_iter:
        if process_episode_file(
            episode_file,
            prompts,
            dialogues_root,
            memory_owner_name,
            llm_model=llm_model
        ):
            success_count += 1
    
    logger.info(f"成功处理 {success_count}/{len(files_to_process)} 个 episode 文件")

def clear_all_qualifications(confirm: bool = False):
    """
    Remove generated qualification files.

    Args:
        confirm: When False, show a preview only.
    """
    # 扫描所有 episode 文件
    episode_files = scan_episode_files()
    
    qualification_files = []
    for episode_file in episode_files:
        qual_file = get_qualification_path(episode_file)
        if qual_file.exists():
            qualification_files.append(qual_file)
    
    if not qualification_files:
        print("没有找到 qualifications_v1.json 文件")
        return
    
    print(f"找到 {len(qualification_files)} 个 qualifications_v1.json 文件:")
    for qual_file in qualification_files:
        print(f"  - {qual_file}")
    
    if not confirm:
        print("\n这只是预览要实际删除这些文件，请运行: clear_all_qualifications(confirm=True)")
        return
    
    # 实际删除文件
    deleted_count = 0
    for qual_file in qualification_files:
        try:
            qual_file.unlink()
            print(f"已删除 {qual_file}")
            deleted_count += 1
        except Exception as e:
            print(f"删除失败 {qual_file}: {e}")
    
    print(f"\nDeleted {deleted_count}/{len(qualification_files)} files.")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Episode qualification utility")
    parser.add_argument("--scan", action="store_true", help="scan and generate qualification files")
    parser.add_argument("--clear", action="store_true", help="remove generated qualification files")
    parser.add_argument("--confirm", action="store_true", help="confirm deletion when used with --clear")
    
    args = parser.parse_args()
    
    if args.clear:
        clear_all_qualifications(confirm=args.confirm)
    elif args.scan:
        scan_and_qualify_episodes()
    else:
        # 默认行为：扫描并评估
        scan_and_qualify_episodes()
