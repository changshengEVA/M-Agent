#!/usr/bin/env python3
# 2025-12-27 changshengEVA
"""
构建记忆 episodes 的模块。
扫描 dialogues 目录下的原始对话文件，将其分割为语义 episodes。
"""

import os
import sys
import json
import yaml
import logging
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
    return config.get('dialogue_segmentation', {})

def ensure_directory(path: Path):
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)

def scan_dialogue_files() -> List[Path]:
    """
    扫描所有对话文件。
    返回所有找到的对话文件路径列表。
    """
    dialogue_files = []
    # 扫描 by_user 和 by_flipflop 目录
    for user_dir in DIALOGUES_ROOT.iterdir():
        if user_dir.is_dir():
            for user_id_dir in user_dir.iterdir():
                if user_id_dir.is_dir():
                    for year_month_dir in user_id_dir.iterdir():
                        if year_month_dir.is_dir():
                            for file in year_month_dir.glob("*.json"):
                                dialogue_files.append(file)
    return dialogue_files

def get_episode_path(dialogue_file: Path) -> Path:
    """
    根据对话文件路径生成对应的 episode 文件路径。
    格式: episodes/by_dialogue/{dialogue_id}/episodes_v1.json
    """
    dialogue_id = dialogue_file.stem  # 例如 dlg_2025-12-23_21-53-05
    episode_dir = EPISODES_ROOT / "by_dialogue" / dialogue_id
    return episode_dir / "episodes_v1.json"

def dialogue_needs_episodes(dialogue_file: Path) -> bool:
    """检查对话是否需要生成 episodes（episode 文件不存在）"""
    episode_file = get_episode_path(dialogue_file)
    return not episode_file.exists()

def load_dialogue(dialogue_file: Path) -> Dict:
    """加载对话 JSON 文件"""
    with open(dialogue_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def call_openai_for_segmentation(dialogue_json: Dict, prompts: Dict) -> Dict:
    """
    调用 OpenAI 进行对话分割。
    返回包含 episodes 的字典。
    """
    from load_model.OpenAIcall import get_llm
    
    # 获取 LLM 实例，温度设为 0.1 以获得更确定性的输出
    llm = get_llm(model_temperature=0.1)
    
    # 构建完整的 prompt
    system_prompt = prompts.get('system_prompt', '')
    user_prompt_template = prompts.get('user_prompt', '')
    
    # 将对话 JSON 转换为字符串用于插入
    dialogue_str = json.dumps(dialogue_json, ensure_ascii=False, indent=2)
    user_prompt = user_prompt_template.replace('<INPUT_JSON>', dialogue_str)
    
    # 组合 system 和 user prompt（适用于 text completion 模型）
    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    
    try:
        response_text = llm(full_prompt)
        # 解析 JSON 响应
        # 响应可能包含额外的文本，尝试提取 JSON 部分
        import re
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

def build_episode_structure(dialogue_id: str, openai_result: Dict) -> Dict:
    """
    构建最终的 episode 结构，符合要求的格式。
    """
    return {
        "dialogue_id": dialogue_id,
        "episode_version": "v1",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "episodes": openai_result.get("episodes", [])
    }

def save_episodes(episode_data: Dict, episode_file: Path):
    """保存 episodes 到文件"""
    ensure_directory(episode_file.parent)
    with open(episode_file, 'w', encoding='utf-8') as f:
        json.dump(episode_data, f, ensure_ascii=False, indent=2)

def process_dialogue_file(dialogue_file: Path, prompts: Dict) -> bool:
    """处理单个对话文件，生成 episodes"""
    try:
        # 加载对话
        dialogue_data = load_dialogue(dialogue_file)
        dialogue_id = dialogue_data.get('dialogue_id', dialogue_file.stem)
        
        # 调用 OpenAI 进行分割
        openai_result = call_openai_for_segmentation(dialogue_data, prompts)
        
        # 构建最终结构
        episode_data = build_episode_structure(dialogue_id, openai_result)
        
        # 保存文件
        episode_file = get_episode_path(dialogue_file)
        save_episodes(episode_data, episode_file)
        
        return True
    except Exception as e:
        logger.error(f"处理对话文件 {dialogue_file} 失败: {e}")
        return False

def scan_and_build_episodes(use_tqdm: bool = True):
    """
    主函数：扫描所有对话文件，为需要生成 episodes 的对话创建 episodes。
    
    Args:
        use_tqdm: 是否使用 tqdm 显示进度条
    """
    # 确保 episodes 根目录存在
    ensure_directory(EPISODES_ROOT)
    
    # 加载 prompts
    prompts = load_prompts()
    if not prompts:
        logger.error("未找到 dialogue_segmentation prompts")
        return
    
    # 扫描所有对话文件
    dialogue_files = scan_dialogue_files()
    
    # 过滤需要处理的文件
    files_to_process = []
    for file in dialogue_files:
        if dialogue_needs_episodes(file):
            files_to_process.append(file)
    
    if not files_to_process:
        # 没有需要处理的文件，静默退出
        return
    
    # 处理文件
    if use_tqdm:
        file_iter = tqdm(files_to_process, desc="构建 episodes")
    else:
        file_iter = files_to_process
    
    success_count = 0
    for dialogue_file in file_iter:
        if process_dialogue_file(dialogue_file, prompts):
            success_count += 1

if __name__ == "__main__":
    # 直接运行此脚本时执行扫描和构建
    scan_and_build_episodes()