#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据构造流程（新版本）：
使用ID标识的处理流，将生成的文件夹保存在 data/memory/{id}/dialogues... 目录下面
"""

import json
import os
import shutil
import logging
import argparse
from datetime import datetime
from typing import List, Dict, Any
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
except ImportError:
    # 如果导入失败，使用本地定义的函数（向后兼容）
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from utils.dialogue_utils import save_dialogue
    from utils.memory_build_utils import build_episodes_with_id, run_memory_build_for_id

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 路径配置
PROJECT_ROOT = Path(__file__).parent


def stage1_construct_dialogues_for_id(process_id: str):
    """
    第一阶段：构造 dialogues 并保存到 data/memory/{id}/dialogues 目录
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
    
    # 2. 构建目标目录（不使用by_data中间目录）
    target_dir = PROJECT_ROOT / "data" / "memory" / process_id / "dialogues"
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
    episodes_root = PROJECT_ROOT / "data" / "memory" / process_id / "episodes"
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

def run_full_pipeline_for_id(process_id: str):
    """
    为指定ID运行完整的数据构造流程
    """
    logger.info(f"开始为处理流 {process_id} 执行完整数据构造流程")
    
    # 第一阶段：构造 dialogues
    if not stage1_construct_dialogues_for_id(process_id):
        logger.warning("第一阶段失败，跳过第二阶段")
        return False
    
    # 第二阶段：构造 episodes
    if not stage2_construct_episodes_for_id(process_id):
        logger.warning("第二阶段失败")
        return False
    
    logger.info("=" * 50)
    logger.info(f"处理流 {process_id} 的所有数据构造流程完成")
    logger.info("=" * 50)
    return True

def run_episodes_only_for_id(process_id: str, source_dialogues_dir: Path = None):
    """
    仅运行 episodes 构建流程（假设 dialogues 已存在）
    """
    logger.info(f"开始为处理流 {process_id} 运行 episodes 构建流程")
    
    # 使用工具函数运行完整的 memory build
    success = run_memory_build_for_id(process_id, source_dialogues_dir)
    
    if success:
        logger.info(f"处理流 {process_id} 的 episodes 构建流程完成")
    else:
        logger.error(f"处理流 {process_id} 的 episodes 构建流程失败")
    
    return success

def main():
    """
    主函数：支持命令行参数
    """
    parser = argparse.ArgumentParser(description="数据构造流程（新版本）")
    parser.add_argument("--id", required=True, help="处理流的ID标识")
    parser.add_argument("--full", action="store_true", help="运行完整流程（包括构造dialogues）")
    parser.add_argument("--episodes-only", action="store_true", help="仅运行episodes构建流程")
    parser.add_argument("--source-dialogues", type=Path, help="源对话目录（用于episodes-only模式）")
    
    args = parser.parse_args()
    
    if args.full:
        # 运行完整流程
        run_full_pipeline_for_id(args.id)
    elif args.episodes_only:
        # 仅运行episodes构建流程
        run_episodes_only_for_id(args.id, args.source_dialogues)
    else:
        # 默认运行完整流程
        logger.info(f"未指定模式，默认运行完整流程")
        run_full_pipeline_for_id(args.id)

if __name__ == "__main__":
    main()