#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Memory Build 工具函数 - 替代猴子补丁的新方法
直接调用支持自定义目录参数的 build 方法
"""

import os
import sys
import logging
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)

def build_episodes_with_id(
    process_id: str,
    project_root: str = None,
    memory_owner_name: str = "changshengEVA",
    llm_model: Optional[Callable[[str], str]] = None
):
    """
    使用指定的处理ID构建 episodes，生成到 data/memory/{id}/ 目录下
    
    Args:
        process_id: 处理流的ID标识
        project_root: 项目根目录，如果为None则使用当前文件所在目录的父目录的父目录
        memory_owner_name: 记忆所有者的名称，用于替换prompt中的<memory_owner_name>占位符
    
    Returns:
        成功返回 True，失败返回 False
    """
    try:
        # 确定项目根目录
        if project_root is None:
            project_root = Path(__file__).parent.parent
        
        # 构建目录路径
        dialogues_root = Path(project_root) / "data" / "memory" / process_id / "dialogues"
        episodes_root = Path(project_root) / "data" / "memory" / process_id / "episodes"
        
        # 确保目录存在
        dialogues_root.mkdir(parents=True, exist_ok=True)
        episodes_root.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"开始为处理流 {process_id} 构建 episodes")
        logger.info(f"对话目录: {dialogues_root}")
        logger.info(f"Episodes目录: {episodes_root}")
        logger.info(f"记忆所有者名称: {memory_owner_name}")
        
        # 1. 构建 episodes
        logger.info("开始构建 episodes...")
        from memory.build_memory.build_episode import scan_and_build_episodes
        scan_and_build_episodes(
            use_tqdm=True,
            dialogues_root=dialogues_root,
            episodes_root=episodes_root,
            memory_owner_name=memory_owner_name,
            llm_model=llm_model
        )
        
        # 2. 评分 qualifications
        logger.info("开始评分 qualifications...")
        from memory.build_memory.qualify_episode import scan_and_qualify_episodes
        scan_and_qualify_episodes(
            use_tqdm=True,
            dialogues_root=dialogues_root,
            episodes_root=episodes_root,
            memory_owner_name=memory_owner_name,
            llm_model=llm_model
        )
        
        # 3. 过滤 eligibility
        logger.info("开始过滤 eligibility...")
        from memory.build_memory.filter_episode import scan_and_filter_episodes
        scan_and_filter_episodes(
            episode_version="v1",
            eligibility_version="v1",
            use_tqdm=True,
            force_update_situation=True,
            episodes_root=episodes_root
        )
        
        logger.info(f"处理流 {process_id} 的 episode 构建流程完成")
        return True
        
    except Exception as e:
        logger.error(f"构建 episodes 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def build_episodes_custom(
    dialogues_root: Path,
    episodes_root: Path,
    memory_owner_name: str = "changshengEVA",
    llm_model: Optional[Callable[[str], str]] = None
):
    """
    使用自定义目录构建 episodes
    
    Args:
        dialogues_root: 对话根目录
        episodes_root: episodes根目录
        memory_owner_name: 记忆所有者的名称，用于替换prompt中的<memory_owner_name>占位符
    
    Returns:
        成功返回 True，失败返回 False
    """
    try:
        logger.info(f"开始使用自定义目录构建 episodes")
        logger.info(f"对话目录: {dialogues_root}")
        logger.info(f"Episodes目录: {episodes_root}")
        logger.info(f"记忆所有者名称: {memory_owner_name}")
        
        # 确保目录存在
        dialogues_root.mkdir(parents=True, exist_ok=True)
        episodes_root.mkdir(parents=True, exist_ok=True)
        
        # 1. 构建 episodes
        logger.info("开始构建 episodes...")
        from memory.build_memory.build_episode import scan_and_build_episodes
        scan_and_build_episodes(
            use_tqdm=True,
            dialogues_root=dialogues_root,
            episodes_root=episodes_root,
            memory_owner_name=memory_owner_name,
            llm_model=llm_model
        )
        
        # 2. 评分 qualifications
        logger.info("开始评分 qualifications...")
        from memory.build_memory.qualify_episode import scan_and_qualify_episodes
        scan_and_qualify_episodes(
            use_tqdm=True,
            dialogues_root=dialogues_root,
            episodes_root=episodes_root,
            memory_owner_name=memory_owner_name,
            llm_model=llm_model
        )
        
        # 3. 过滤 eligibility
        logger.info("开始过滤 eligibility...")
        from memory.build_memory.filter_episode import scan_and_filter_episodes
        scan_and_filter_episodes(
            episode_version="v1",
            eligibility_version="v1",
            use_tqdm=True,
            force_update_situation=True,
            episodes_root=episodes_root
        )
        
        logger.info("episode 构建流程完成")
        return True
        
    except Exception as e:
        logger.error(f"构建 episodes 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def run_memory_build_for_id(
    process_id: str,
    source_dialogues_dir: Path = None,
    llm_model: Optional[Callable[[str], str]] = None
):
    """
    为指定ID运行完整的memory build流程
    
    Args:
        process_id: 处理流的ID标识
        source_dialogues_dir: 源对话目录，如果为None则使用默认的data/memory/dialogues
    
    Returns:
        成功返回 True，失败返回 False
    """
    try:
        from pathlib import Path
        import shutil
        
        # 确定项目根目录
        project_root = Path(__file__).parent.parent
        
        # 构建目标目录
        target_dialogues_root = project_root / "data" / "memory" / process_id / "dialogues"
        episodes_root = project_root / "data" / "memory" / process_id / "episodes"
        
        # 确保目录存在
        target_dialogues_root.mkdir(parents=True, exist_ok=True)
        episodes_root.mkdir(parents=True, exist_ok=True)
        
        # 如果提供了源对话目录，则复制对话文件
        if source_dialogues_dir and source_dialogues_dir.exists():
            logger.info(f"从 {source_dialogues_dir} 复制对话文件到 {target_dialogues_root}")
            # 复制目录内容
            for item in source_dialogues_dir.iterdir():
                if item.is_dir():
                    dest = target_dialogues_root / item.name
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(item, dest)
                else:
                    dest = target_dialogues_root / item.name
                    shutil.copy2(item, dest)
        
        # 运行构建流程
        return build_episodes_with_id(process_id, project_root, llm_model=llm_model)
        
    except Exception as e:
        logger.error(f"运行 memory build 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False
