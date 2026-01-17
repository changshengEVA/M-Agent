#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据构造流程：
1. 从 dialog_history.json 构造 dialogues 并保存到 by_data 目录
2. 使用 memory build 方法构造 episodes 并保存到 by_data_tmp 目录
"""

import json
import os
import shutil
import logging
from datetime import datetime
from typing import List, Dict, Any
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
    from utils import cleanup_directory, copy_episodes_to_data_tmp, save_dialogue, build_episodes
except ImportError:
    # 如果导入失败，使用本地定义的函数（向后兼容）
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from utils.file_utils import cleanup_directory, copy_episodes_to_data_tmp
    from utils.dialogue_utils import save_dialogue
    from utils.episode_utils import build_episodes

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 路径配置
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DIALOGUES_BY_DATA_DIR = os.path.join(PROJECT_ROOT, "data", "memory", "dialogues", "by_data")
EPISODES_BY_DIALOGUE_DIR = os.path.join(PROJECT_ROOT, "data", "memory", "episodes", "by_dialogue")
EPISODES_BY_DATA_TMP_DIR = os.path.join(PROJECT_ROOT, "data", "memory", "episodes", "by_data_tmp")


def stage1_construct_dialogues():
    """
    第一阶段：构造 dialogues 并保存到 by_data 目录
    """
    logger.info("=" * 50)
    logger.info("开始第一阶段：构造 dialogues")
    logger.info("=" * 50)
    
    # 1. 加载 dialogue 列表
    dialogues = load_dialogues()
    if not dialogues:
        logger.error("没有加载到 dialogue 数据，退出")
        return False
    
    logger.info(f"共加载 {len(dialogues)} 个 dialogue")
    
    # 2. 保存 dialogues
    successful_count = 0
    failed_count = 0
    
    for i, dialogue in enumerate(dialogues):
        logger.info(f"保存第 {i+1}/{len(dialogues)} 个 dialogue: {dialogue.get('dialogue_id')}")
        
        if save_dialogue(dialogue, DIALOGUES_BY_DATA_DIR):
            successful_count += 1
        else:
            failed_count += 1
    
    # 3. 输出统计信息
    logger.info("=" * 50)
    logger.info("第一阶段完成")
    logger.info(f"成功保存: {successful_count} 个")
    logger.info(f"失败: {failed_count} 个")
    logger.info(f"输出目录: {DIALOGUES_BY_DATA_DIR}")
    logger.info("=" * 50)
    
    return successful_count > 0

def stage2_construct_episodes():
    """
    第二阶段：构造 episodes 并保存到 by_data_tmp 目录
    """
    logger.info("=" * 50)
    logger.info("开始第二阶段：构造 episodes")
    logger.info("=" * 50)
    
    # 1. 清理 by_data_tmp 目录
    logger.info(f"清理目录: {EPISODES_BY_DATA_TMP_DIR}")
    cleanup_directory(EPISODES_BY_DATA_TMP_DIR)
    
    # 2. 确保 by_data_tmp/by_dialogue 目录结构存在
    by_dialogue_dir = os.path.join(EPISODES_BY_DATA_TMP_DIR, "by_dialogue")
    os.makedirs(by_dialogue_dir, exist_ok=True)
    
    # 3. 构建 episodes（直接输出到 by_data_tmp 目录）
    logger.info("调用 memory build 方法构建 episodes...")
    if not build_episodes(PROJECT_ROOT, DIALOGUES_BY_DATA_DIR, EPISODES_BY_DATA_TMP_DIR):
        logger.error("构建 episodes 失败")
        return False
    
    # 4. 统计生成的文件数量
    episode_files_count = 0
    qualification_files_count = 0
    eligibility_files_count = 0
    
    for dialogue_dir_name in os.listdir(by_dialogue_dir):
        dialogue_dir = os.path.join(by_dialogue_dir, dialogue_dir_name)
        if os.path.isdir(dialogue_dir):
            for filename in os.listdir(dialogue_dir):
                if filename.endswith('.json'):
                    if filename == 'episodes_v1.json':
                        episode_files_count += 1
                    elif filename == 'qualifications_v1.json':
                        qualification_files_count += 1
                    elif filename.startswith('eligibility_'):
                        eligibility_files_count += 1
    
    # 5. 输出统计信息
    logger.info("=" * 50)
    logger.info("第二阶段完成")
    logger.info(f"生成 episodes 文件: {episode_files_count} 个")
    logger.info(f"生成 qualifications 文件: {qualification_files_count} 个")
    logger.info(f"生成 eligibility 文件: {eligibility_files_count} 个")
    logger.info(f"输出目录: {EPISODES_BY_DATA_TMP_DIR}")
    logger.info("=" * 50)
    
    return episode_files_count > 0

def main():
    """
    主函数：执行完整的数据构造流程
    """
    logger.info("开始完整数据构造流程")
    
    # 第一阶段：构造 dialogues
    if not stage1_construct_dialogues():
        logger.warning("第一阶段失败，跳过第二阶段")
        return
    
    # 第二阶段：构造 episodes
    stage2_construct_episodes()
    
    logger.info("=" * 50)
    logger.info("所有数据构造流程完成")
    logger.info("=" * 50)

if __name__ == "__main__":
    main()