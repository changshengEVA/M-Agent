#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据构造流程（简化版）：
只需要指定id和kg_prompt_version两个参数
"""

import json
import os
import shutil
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
import sys

# 导入数据加载模块
try:
    from load_data import load_dialogues
except ImportError:
    # 如果导入失败，使用本地定义的函数（向后兼容）
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from load_data.dialog_history_loader import load_dialogues

# 导入工具函数
try:
    from utils import save_dialogue
    from utils.memory_build_utils import build_episodes_with_id, run_memory_build_for_id
    from memory.build_memory.form_kg_candidate import scan_and_form_kg_candidates
except ImportError:
    # 如果导入失败，使用本地定义的函数（向后兼容）
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from utils.dialogue_utils import save_dialogue
    from utils.memory_build_utils import build_episodes_with_id, run_memory_build_for_id
    from memory.build_memory.form_kg_candidate import scan_and_form_kg_candidates

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 路径配置
PROJECT_ROOT = Path(__file__).parent


def get_output_path(process_id: str, stage_name: str) -> Path:
    """
    Args:
        process_id: 处理流ID
        stage_name: 阶段名称（如 "dialogues", "episodes", "kg_candidates"）
        
    Returns:
        输出目录路径
    """
    return Path("data/memory") / process_id / stage_name


def stage1_construct_dialogues_for_id(process_id: str):
    """
    第一阶段：构造 dialogues 并保存到 data/memory/{id}/dialogues 目录
    
    Args:
        process_id: 处理流ID
    """
    logger.info("=" * 50)
    logger.info(f"开始第一阶段：为处理流 {process_id} 构造 dialogues")
    logger.info("=" * 50)
    
    # 1. 加载 dialogue 列表
    dialogues = load_dialogues()
    if not dialogues:
        logger.error("没有加载到 dialogue 数据，退出")
        return False
    
    logger.info(f"共加载 {len(dialogues)} 个 dialogue")
    
    # 2. 构建目标目录
    target_dir = get_output_path(process_id, "dialogues")
    target_dir.mkdir(parents=True, exist_ok=True)
    
    # 3. 保存 dialogues
    successful_count = 0
    failed_count = 0
    
    for i, dialogue in enumerate(dialogues):
        logger.info(f"保存第 {i+1}/{len(dialogues)} 个 dialogue: {dialogue.get('dialogue_id')}")
        
        if save_dialogue(dialogue, str(target_dir)):
            successful_count += 1
        else:
            failed_count += 1
    
    # 4. 输出统计信息
    logger.info("=" * 50)
    logger.info("第一阶段完成")
    logger.info(f"成功保存: {successful_count} 个")
    logger.info(f"失败: {failed_count} 个")
    logger.info(f"输出目录: {target_dir}")
    logger.info("=" * 50)
    
    return successful_count > 0


def stage2_construct_episodes_for_id(process_id: str):
    """
    第二阶段：构造 episodes 并保存到 data/memory/{id}/episodes 目录
    
    Args:
        process_id: 处理流ID
    """
    logger.info("=" * 50)
    logger.info(f"开始第二阶段：为处理流 {process_id} 构造 episodes")
    logger.info("=" * 50)
    
    # 使用新的工具函数构建 episodes
    logger.info(f"调用新的 memory build 方法构建 episodes...")
    if not build_episodes_with_id(process_id, str(PROJECT_ROOT)):
        logger.error("构建 episodes 失败")
        return False
    
    # 统计生成的文件数量
    episodes_root = get_output_path(process_id, "episodes")
    by_dialogue_dir = episodes_root / "by_dialogue"
    
    episode_files_count = 0
    qualification_files_count = 0
    eligibility_files_count = 0
    
    if by_dialogue_dir.exists():
        for dialogue_dir_name in os.listdir(by_dialogue_dir):
            dialogue_dir = by_dialogue_dir / dialogue_dir_name
            if dialogue_dir.is_dir():
                for filename in os.listdir(dialogue_dir):
                    if filename.endswith('.json'):
                        if filename == 'episodes_v1.json':
                            episode_files_count += 1
                        elif filename == 'qualifications_v1.json':
                            qualification_files_count += 1
                        elif filename.startswith('eligibility_'):
                            eligibility_files_count += 1
    
    # 输出统计信息
    logger.info("=" * 50)
    logger.info("第二阶段完成")
    logger.info(f"生成 episodes 文件: {episode_files_count} 个")
    logger.info(f"生成 qualifications 文件: {qualification_files_count} 个")
    logger.info(f"生成 eligibility 文件: {eligibility_files_count} 个")
    logger.info(f"输出目录: {episodes_root}")
    logger.info("=" * 50)
    
    return episode_files_count > 0


def stage3_form_kg_candidates_for_id(process_id: str, prompt_version: str = "v1"):
    """
    第三阶段：形成KG候选，为kg_available为true的episode生成kg_candidate
    
    Args:
        process_id: 处理流ID
        prompt_version: prompt版本（v1 或 v2），默认v1
    """
    logger.info("=" * 50)
    logger.info(f"开始第三阶段：为处理流 {process_id} 形成KG候选")
    logger.info(f"使用 prompt 版本: {prompt_version}")
    logger.info("=" * 50)
    
    # 构建目录路径
    dialogues_root = get_output_path(process_id, "dialogues")
    episodes_root = get_output_path(process_id, "episodes")
    kg_candidates_root = get_output_path(process_id, "kg_candidates")
    
    # 确保目录存在
    dialogues_root.mkdir(parents=True, exist_ok=True)
    episodes_root.mkdir(parents=True, exist_ok=True)
    kg_candidates_root.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"对话目录: {dialogues_root}")
    logger.info(f"Episodes目录: {episodes_root}")
    logger.info(f"KG候选目录: {kg_candidates_root}")
    
    try:
        # 调用 form_kg_candidate 模块的主函数
        logger.info("开始扫描并生成 kg_candidates...")
        scan_and_form_kg_candidates(
            prompt_version=prompt_version,
            dialogues_root=dialogues_root,
            episodes_root=episodes_root,
            kg_candidates_root=kg_candidates_root
        )
        
        # 统计生成的 kg_candidate 文件数量
        kg_candidate_files_count = 0
        if kg_candidates_root.exists():
            for file_path in kg_candidates_root.iterdir():
                if file_path.is_file() and file_path.suffix == '.json':
                    # 检查文件名格式是否为数字（如 00001.json）
                    try:
                        int(file_path.stem)
                        kg_candidate_files_count += 1
                    except ValueError:
                        # 不是数字格式的文件，跳过
                        continue
        
        # 输出统计信息
        logger.info("=" * 50)
        logger.info("第三阶段完成")
        logger.info(f"生成 kg_candidate 文件: {kg_candidate_files_count} 个")
        logger.info(f"使用 prompt 版本: {prompt_version}")
        logger.info(f"输出目录: {kg_candidates_root}")
        logger.info("=" * 50)
        
        return kg_candidate_files_count > 0
        
    except Exception as e:
        logger.error(f"形成KG候选失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def run_full_pipeline_for_id(process_id: str, prompt_version: str = "v1"):
    """
    为指定ID运行完整的数据构造流程
    
    Args:
        process_id: 处理流ID
        prompt_version: prompt版本（v1 或 v2），默认v1
    """
    logger.info(f"开始为处理流 {process_id} 执行完整数据构造流程")
    logger.info(f"使用 prompt 版本: {prompt_version}")
    
    # 第一阶段：构造 dialogues
    if not stage1_construct_dialogues_for_id(process_id):
        logger.warning("第一阶段失败，跳过后续阶段")
        return False
    
    # 第二阶段：构造 episodes
    if not stage2_construct_episodes_for_id(process_id):
        logger.warning("第二阶段失败，跳过第三阶段")
        return False
    
    # 第三阶段：形成KG候选
    if not stage3_form_kg_candidates_for_id(process_id, prompt_version):
        logger.warning("第三阶段失败")
        return False
    
    logger.info("=" * 50)
    logger.info(f"处理流 {process_id} 的所有数据构造流程完成（包含三个阶段）")
    logger.info(f"使用 prompt 版本: {prompt_version}")
    logger.info("=" * 50)
    return True


def main():
    import argparse
    """
    主函数：简化版本，只需要id和kg_prompt_version两个参数
    """
    parser = argparse.ArgumentParser(
        description="数据构造流程（简化版）- 只需要id和kg_prompt_version两个参数"
    )
    parser.add_argument("--id", type=str, required=True,
                       help="处理流ID（必需）")
    parser.add_argument("--kg-prompt-version", type=str, default="v2",
                       help="KG候选生成的prompt版本（v1 或 v2，默认v1）")
    
    args = parser.parse_args()
    
    logger.info(f"开始执行数据构造流程，处理流ID: {args.id}")
    logger.info(f"KG prompt 版本: {args.kg_prompt_version}")
    logger.info("运行模式: full（完整三个阶段）")
    
    # 直接运行完整三个阶段的流程
    success = run_full_pipeline_for_id(args.id, args.kg_prompt_version)
    
    if success:
        logger.info("=" * 50)
        logger.info(f"处理流 {args.id} 的数据构造流程完成（完整三个阶段）")
        logger.info("=" * 50)
    else:
        logger.error("数据构造流程失败")


if __name__ == "__main__":
    main()
