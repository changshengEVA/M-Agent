#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件操作工具函数
包含与主线过程无关的通用文件操作工具
"""

import os
import shutil
import logging

logger = logging.getLogger(__name__)

def cleanup_directory(directory: str):
    """
    清理目录：删除目录中的所有内容，但保留目录本身
    
    Args:
        directory: 要清理的目录路径
    """
    if not os.path.exists(directory):
        logger.info(f"目录不存在，无需清理: {directory}")
        return
    
    try:
        # 删除目录中的所有文件和子目录
        for item in os.listdir(directory):
            item_path = os.path.join(directory, item)
            if os.path.isfile(item_path) or os.path.islink(item_path):
                os.unlink(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)
        
        logger.info(f"已清理目录: {directory}")
    except Exception as e:
        logger.error(f"清理目录失败 {directory}: {e}")
        raise

def copy_episodes_to_data_tmp(source_dir: str, target_dir: str):
    """
    将 episodes 从源目录复制到目标目录
    
    复制结构：
    - 源: {source_dir}/{dialogue_id}/*.json
    - 目标: {target_dir}/{dialogue_id}/*.json
    
    Args:
        source_dir: 源目录路径
        target_dir: 目标目录路径
        
    Returns:
        复制的文件数量
    """
    if not os.path.exists(source_dir):
        logger.warning(f"源目录不存在: {source_dir}")
        return 0
    
    copied_count = 0
    # 遍历源目录下的所有 dialogue 目录
    for dialogue_dir_name in os.listdir(source_dir):
        source_dialogue_dir = os.path.join(source_dir, dialogue_dir_name)
        if not os.path.isdir(source_dialogue_dir):
            continue
        
        # 创建对应的目标目录
        target_dialogue_dir = os.path.join(target_dir, dialogue_dir_name)
        os.makedirs(target_dialogue_dir, exist_ok=True)
        
        # 复制所有 JSON 文件
        for filename in os.listdir(source_dialogue_dir):
            if filename.endswith('.json'):
                source_file = os.path.join(source_dialogue_dir, filename)
                target_file = os.path.join(target_dialogue_dir, filename)
                
                try:
                    shutil.copy2(source_file, target_file)
                    copied_count += 1
                    logger.debug(f"已复制: {source_file} -> {target_file}")
                except Exception as e:
                    logger.error(f"复制文件失败 {source_file}: {e}")
    
    logger.info(f"已复制 {copied_count} 个 episode 文件到 {target_dir}")
    return copied_count