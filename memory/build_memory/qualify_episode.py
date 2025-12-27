#!/usr/bin/env python3
# 2025-12-27 changshengEVA
"""
Episode Qualification 模块。
扫描 episodes 目录下的 episode 文件，对每个 episode 进行打分和资格评估。
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

def call_openai_for_qualification(episode_json: Dict, prompts: Dict) -> Dict:
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
    episode_str = json.dumps(episode_json, ensure_ascii=False, indent=2)
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
        
        # 获取所有 episodes
        episodes = episode_data.get('episodes', [])
        if not episodes:
            logger.warning(f"对话 {dialogue_id} 没有 episodes，跳过")
            return False
        
        # 为每个 episode 生成 qualification
        qualifications = []
        for episode in episodes:
            episode_id = episode.get('episode_id', 'unknown')
            
            # 调用 OpenAI 进行 qualification
            openai_result = call_openai_for_qualification(episode, prompts)
            
            # 构建最终结构
            qualification = build_qualification_structure(dialogue_id, episode_id, openai_result)
            qualifications.append(qualification)
        
        # 保存文件
        qualification_file = get_qualification_path(episode_file)
        save_qualifications(qualifications, qualification_file)
        
        return True
    except Exception as e:
        logger.error(f"处理 episode 文件 {episode_file} 失败: {e}")
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

if __name__ == "__main__":
    # 直接运行此脚本时执行扫描和评估
    scan_and_qualify_episodes()