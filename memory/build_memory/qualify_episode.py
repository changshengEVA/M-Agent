#!/usr/bin/env python3
# 2025-12-27 changshengEVA
"""
Episode Qualification 模块。
扫描 episodes 目录下的 episode 文件，对每个 episode 进行打分和资格评估。
提供清理 qualifications 文件的接口。
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

# 配置日志：只显示 WARNING 及以上级别，减少输出噪音
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 路径配置
DIALOGUES_ROOT = PROJECT_ROOT / "data" / "memory" / "dialogues"
EPISODES_ROOT = PROJECT_ROOT / "data" / "memory" / "episodes"
CONFIG_PATH = PROJECT_ROOT / "config" / "prompt.yaml"

def load_prompts() -> Dict:
    """从 config/prompt.yaml 加载 prompts"""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config.get('episode_qualification', {})

def ensure_directory(path: Path):
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)

def scan_episode_files() -> List[Path]:
    """
    扫描所有 episode 文件。
    返回所有找到的 episode 文件路径列表。
    """
    episode_files = []
    # 扫描 by_dialogue 目录
    by_dialogue_dir = EPISODES_ROOT / "by_dialogue"
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
    根据 episode 文件路径生成对应的 qualification 文件路径。
    格式: episodes/by_dialogue/{dialogue_id}/qualifications_v1.json
    """
    dialogue_dir = episode_file.parent
    return dialogue_dir / "qualifications_v1.json"

def episode_needs_qualification(episode_file: Path) -> bool:
    """检查 episode 是否需要生成 qualification（qualification 文件不存在）"""
    qualification_file = get_qualification_path(episode_file)
    return not qualification_file.exists()

def load_episodes(episode_file: Path) -> Dict:
    """加载 episode JSON 文件"""
    with open(episode_file, 'r', encoding='utf-8') as f:
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

def call_openai_for_qualification(episode_with_content: Dict, prompts: Dict) -> Dict:
    """
    调用 OpenAI 进行 episode qualification 打分。
    返回包含 qualification 结果的字典。
    """
    from load_model.OpenAIcall import get_llm
    
    # 获取 LLM 实例，温度设为 0.1 以获得更确定性的输出
    llm = get_llm(model_temperature=0.1)
    
    # 构建完整的 prompt
    system_prompt = prompts.get('system_prompt', '')
    user_prompt_template = prompts.get('user_prompt', '')
    
    # 将 episode JSON 转换为字符串用于插入
    episode_str = json.dumps(episode_with_content, ensure_ascii=False, indent=2)
    user_prompt = user_prompt_template.replace('<EPISODE_JSON>', episode_str)
    
    # 组合 system 和 user prompt（适用于 text completion 模型）
    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    
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

def build_qualification_structure(dialogue_id: str, episode_id: str, openai_result: Dict) -> Dict:
    """
    构建最终的 qualification 结构，符合要求的格式。
    """
    # 确保 openai_result 包含所有必需字段
    if "scene_potential_score" not in openai_result:
        openai_result["scene_potential_score"] = {
            "topic_clarity": 0,
            "context_closure": 0,
            "intent_stability": 0,
            "information_density": 0,
            "total": 0
        }
    
    if "decision" not in openai_result:
        total = openai_result["scene_potential_score"].get("total", 0)
        if total >= 5:
            openai_result["decision"] = "scene_candidate"
        elif total >= 3:
            openai_result["decision"] = "pending"
        else:
            openai_result["decision"] = "discard"
    
    if "rationale" not in openai_result:
        openai_result["rationale"] = {
            "topic_clarity": "No rationale provided",
            "context_closure": "No rationale provided",
            "intent_stability": "No rationale provided",
            "information_density": "No rationale provided"
        }
    
    if "confidence" not in openai_result:
        openai_result["confidence"] = 0.5
    
    return {
        "episode_id": episode_id,
        "dialogue_id": dialogue_id,
        "qualification_version": "v1",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "scene_potential_score": openai_result["scene_potential_score"],
        "decision": openai_result["decision"],
        "rationale": openai_result["rationale"],
        "confidence": openai_result["confidence"]
    }

def process_episode_file(episode_file: Path, prompts: Dict) -> bool:
    """处理单个 episode 文件，生成 qualification"""
    try:
        # 加载 episodes
        episode_data = load_episodes(episode_file)
        dialogue_id = episode_data.get('dialogue_id', episode_file.parent.name)
        
        # 查找并加载对应的对话文件
        dialogue_file = find_dialogue_file(dialogue_id)
        if not dialogue_file:
            logger.error(f"找不到对话文件: {dialogue_id}")
            return False
        
        dialogue_data = load_dialogue(dialogue_file)
        
        # 获取所有 episodes
        episodes = episode_data.get('episodes', [])
        if not episodes:
            logger.warning(f"对话 {dialogue_id} 没有 episodes，跳过")
            return False
        
        # 为每个 episode 生成 qualification
        qualifications = []
        for episode in episodes:
            episode_id = episode.get('episode_id', 'unknown')
            
            # 构建包含完整内容的 episode
            episode_with_content = build_episode_with_content(episode, dialogue_data)
            
            # 调用 OpenAI 进行 qualification
            openai_result = call_openai_for_qualification(episode_with_content, prompts)
            
            # 构建最终结构
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
    """保存 qualifications 到文件"""
    ensure_directory(qualification_file.parent)
    with open(qualification_file, 'w', encoding='utf-8') as f:
        json.dump({
            "qualifications": qualifications,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "qualification_version": "v1"
        }, f, ensure_ascii=False, indent=2)

def scan_and_qualify_episodes(use_tqdm: bool = True):
    """
    主函数：扫描所有 episode 文件，为需要生成 qualification 的 episode 创建 qualification。
    
    Args:
        use_tqdm: 是否使用 tqdm 显示进度条
    """
    # 确保 episodes 根目录存在
    ensure_directory(EPISODES_ROOT)
    
    # 加载 prompts
    prompts = load_prompts()
    if not prompts:
        logger.error("未找到 episode_qualification prompts")
        return
    
    # 扫描所有 episode 文件
    episode_files = scan_episode_files()
    
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
        if process_episode_file(episode_file, prompts):
            success_count += 1
    
    logger.info(f"成功处理 {success_count}/{len(files_to_process)} 个 episode 文件")

def clear_all_qualifications(confirm: bool = False):
    """
    清理所有 qualifications_v1.json 文件。
    
    Args:
        confirm: 如果为 True，则实际删除文件；如果为 False，只显示将要删除的文件列表
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
        print("\n这只是预览。要实际删除这些文件，请运行: clear_all_qualifications(confirm=True)")
        return
    
    # 实际删除文件
    deleted_count = 0
    for qual_file in qualification_files:
        try:
            qual_file.unlink()
            print(f"已删除: {qual_file}")
            deleted_count += 1
        except Exception as e:
            print(f"删除失败 {qual_file}: {e}")
    
    print(f"\n成功删除 {deleted_count}/{len(qualification_files)} 个文件")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Episode Qualification 模块")
    parser.add_argument("--scan", action="store_true", help="扫描并评估 episodes")
    parser.add_argument("--clear", action="store_true", help="清理所有 qualifications 文件")
    parser.add_argument("--confirm", action="store_true", help="确认删除（与 --clear 一起使用）")
    
    args = parser.parse_args()
    
    if args.clear:
        clear_all_qualifications(confirm=args.confirm)
    elif args.scan:
        scan_and_qualify_episodes()
    else:
        # 默认行为：扫描并评估
        scan_and_qualify_episodes()