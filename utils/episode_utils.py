#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Episode 相关工具函数，包括猴子补丁和构建逻辑
"""

import os
import sys
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def patch_episode_modules(project_root: str, dialogues_by_data_dir: str, episodes_by_data_tmp_dir: str):
    """
    猴子补丁：修改 episode 相关模块的路径配置，使其输出到 by_data_tmp 目录
    
    Args:
        project_root: 项目根目录
        dialogues_by_data_dir: dialogues by_data 目录
        episodes_by_data_tmp_dir: episodes by_data_tmp 目录
        
    Returns:
        包含原始值的字典，用于恢复（如果需要）
    """
    import sys
    from pathlib import Path
    
    # 1. 补丁 build_episode.py 模块
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    import memory.build_memory.build_episode as build_episode_module
    
    # 保存原始值以便恢复（如果需要）
    original_project_root = build_episode_module.PROJECT_ROOT
    original_dialogues_root = build_episode_module.DIALOGUES_ROOT
    original_episodes_root = build_episode_module.EPISODES_ROOT
    
    # 修改路径
    build_episode_module.PROJECT_ROOT = Path(project_root)
    build_episode_module.DIALOGUES_ROOT = Path(dialogues_by_data_dir)
    build_episode_module.EPISODES_ROOT = Path(episodes_by_data_tmp_dir)
    
    # 修改 scan_dialogue_files 函数，使其扫描 by_data 目录
    original_scan_func = build_episode_module.scan_dialogue_files
    
    def patched_scan_dialogue_files():
        """扫描 by_data 目录下的对话文件"""
        import os
        from pathlib import Path
        
        dialogue_files = []
        dialogues_dir = Path(dialogues_by_data_dir)
        
        if not dialogues_dir.exists():
            logger.warning(f"对话目录不存在: {dialogues_dir}")
            return []
        
        # 扫描 by_data 目录结构: by_data/{user_id}/{year-month}/{dialogue_id}.json
        for user_dir in dialogues_dir.iterdir():
            if user_dir.is_dir():
                for year_month_dir in user_dir.iterdir():
                    if year_month_dir.is_dir():
                        for file in year_month_dir.glob("*.json"):
                            dialogue_files.append(file)
        
        logger.info(f"从 by_data 目录扫描到 {len(dialogue_files)} 个对话文件")
        return dialogue_files
    
    build_episode_module.scan_dialogue_files = patched_scan_dialogue_files
    
    # 修改 get_episode_path 函数
    original_get_episode_path = build_episode_module.get_episode_path
    
    def patched_get_episode_path(dialogue_file: Path):
        """生成 episode 文件路径到 by_data_tmp 目录"""
        dialogue_id = dialogue_file.stem
        episode_dir = Path(episodes_by_data_tmp_dir) / "by_dialogue" / dialogue_id
        return episode_dir / "episodes_v1.json"
    
    build_episode_module.get_episode_path = patched_get_episode_path
    
    # 2. 补丁 qualify_episode.py 模块
    import memory.build_memory.qualify_episode as qualify_episode_module
    
    # 修改路径
    qualify_episode_module.PROJECT_ROOT = Path(project_root)
    qualify_episode_module.DIALOGUES_ROOT = Path(dialogues_by_data_dir)
    qualify_episode_module.EPISODES_ROOT = Path(episodes_by_data_tmp_dir)
    
    # 修改 scan_episode_files 函数
    original_qualify_scan_func = qualify_episode_module.scan_episode_files
    
    def patched_scan_episode_files():
        """扫描 by_data_tmp 目录下的 episode 文件"""
        from pathlib import Path
        
        episode_files = []
        episodes_dir = Path(episodes_by_data_tmp_dir) / "by_dialogue"
        
        if not episodes_dir.exists():
            return episode_files
        
        for dialogue_dir in episodes_dir.iterdir():
            if dialogue_dir.is_dir():
                episode_file = dialogue_dir / "episodes_v1.json"
                if episode_file.exists():
                    episode_files.append(episode_file)
        
        return episode_files
    
    qualify_episode_module.scan_episode_files = patched_scan_episode_files
    
    # 修改 find_dialogue_file 函数，使其在 by_data 目录中查找
    original_find_dialogue_file = qualify_episode_module.find_dialogue_file
    
    def patched_find_dialogue_file(dialogue_id: str):
        """在 by_data 目录中查找对话文件"""
        from pathlib import Path
        
        dialogues_dir = Path(dialogues_by_data_dir)
        
        # 搜索 by_data 目录结构
        for user_dir in dialogues_dir.iterdir():
            if user_dir.is_dir():
                for year_month_dir in user_dir.iterdir():
                    if year_month_dir.is_dir():
                        dialogue_file = year_month_dir / f"{dialogue_id}.json"
                        if dialogue_file.exists():
                            return dialogue_file
        
        return None
    
    qualify_episode_module.find_dialogue_file = patched_find_dialogue_file
    
    # 3. 补丁 filter_episode.py 模块
    import memory.build_memory.filter_episode as filter_episode_module
    
    # 修改路径
    filter_episode_module.PROJECT_ROOT = Path(project_root)
    filter_episode_module.EPISODES_ROOT = Path(episodes_by_data_tmp_dir)
    
    # 修改 scan_qualification_files 函数
    original_filter_scan_func = filter_episode_module.scan_qualification_files
    
    def patched_scan_qualification_files():
        """扫描 by_data_tmp 目录下的 qualification 文件"""
        from pathlib import Path
        
        qualification_files = []
        episodes_dir = Path(episodes_by_data_tmp_dir) / "by_dialogue"
        
        if not episodes_dir.exists():
            return qualification_files
        
        for dialogue_dir in episodes_dir.iterdir():
            if dialogue_dir.is_dir():
                qualification_file = dialogue_dir / "qualifications_v1.json"
                if qualification_file.exists():
                    qualification_files.append(qualification_file)
        
        return qualification_files
    
    filter_episode_module.scan_qualification_files = patched_scan_qualification_files
    
    logger.info("已成功补丁 episode 相关模块，路径已重定向到 by_data_tmp 目录")
    
    # 返回原始值，以便需要时恢复（但本函数中不需要恢复）
    return {
        'build_episode': {
            'original_project_root': original_project_root,
            'original_dialogues_root': original_dialogues_root,
            'original_episodes_root': original_episodes_root,
            'original_scan_func': original_scan_func,
            'original_get_episode_path': original_get_episode_path
        },
        'qualify_episode': {
            'original_project_root': qualify_episode_module.PROJECT_ROOT,
            'original_dialogues_root': qualify_episode_module.DIALOGUES_ROOT,
            'original_episodes_root': qualify_episode_module.EPISODES_ROOT,
            'original_scan_func': original_qualify_scan_func,
            'original_find_dialogue_file': original_find_dialogue_file
        },
        'filter_episode': {
            'original_project_root': filter_episode_module.PROJECT_ROOT,
            'original_episodes_root': filter_episode_module.EPISODES_ROOT,
            'original_scan_func': original_filter_scan_func
        }
    }

def build_episodes(project_root: str, dialogues_by_data_dir: str, episodes_by_data_tmp_dir: str):
    """
    构建 episodes：调用现有的 memory build 方法，使用补丁后的模块
    
    步骤：
    1. 构建 episodes (build_episode.py)
    2. 评分 qualifications (qualify_episode.py)
    3. 过滤 eligibility (filter_episode.py)
    
    Args:
        project_root: 项目根目录
        dialogues_by_data_dir: dialogues by_data 目录
        episodes_by_data_tmp_dir: episodes by_data_tmp 目录
        
    Returns:
        成功返回 True，失败返回 False
    """
    try:
        # 应用猴子补丁
        logger.info("应用猴子补丁修改模块路径...")
        patch_episode_modules(project_root, dialogues_by_data_dir, episodes_by_data_tmp_dir)
        
        # 导入现有模块（必须在补丁后导入，以确保使用补丁后的函数）
        sys.path.append(os.path.dirname(os.path.abspath(__file__)))
        
        # 1. 构建 episodes
        logger.info("开始构建 episodes...")
        from memory.build_memory.build_episode import scan_and_build_episodes
        scan_and_build_episodes(use_tqdm=True)
        
        # 2. 评分 qualifications
        logger.info("开始评分 qualifications...")
        from memory.build_memory.qualify_episode import scan_and_qualify_episodes
        scan_and_qualify_episodes(use_tqdm=True)
        
        # 3. 过滤 eligibility
        logger.info("开始过滤 eligibility...")
        from memory.build_memory.filter_episode import scan_and_filter_episodes
        scan_and_filter_episodes(
            episode_version="v1",
            eligibility_version="v1",
            use_tqdm=True,
            force_update_situation=True
        )
        
        logger.info("episode 构建流程完成")
        return True
        
    except Exception as e:
        logger.error(f"构建 episodes 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False