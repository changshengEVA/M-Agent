#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统工具函数

包含长期记忆管理系统中与系统相关的通用工具函数。
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Union

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def ensure_directory(directory: Path) -> bool:
    """
    确保目录存在
    
    Args:
        directory: 目录路径
        
    Returns:
        如果目录存在或创建成功返回True，否则返回False
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        logger.error(f"创建目录失败 {directory}: {e}")
        return False


def list_json_files(directory: Path) -> List[Path]:
    """
    列出目录中的所有JSON文件
    
    Args:
        directory: 目录路径
        
    Returns:
        JSON文件路径列表
    """
    json_files = []
    
    if not directory.exists():
        logger.warning(f"目录不存在: {directory}")
        return json_files
    
    for file_path in directory.iterdir():
        if file_path.is_file() and file_path.suffix == '.json':
            json_files.append(file_path)
    
    return json_files


def load_json_file(file_path: Path) -> Optional[Dict]:
    """
    加载JSON文件
    
    Args:
        file_path: JSON文件路径
        
    Returns:
        JSON数据字典，如果加载失败则返回None
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data
    except json.JSONDecodeError as e:
        logger.error(f"解析JSON文件失败 {file_path}: {e}")
        return None
    except Exception as e:
        logger.error(f"加载JSON文件失败 {file_path}: {e}")
        return None


def save_json_file(data: Dict, file_path: Path, indent: int = 2) -> bool:
    """
    保存数据到JSON文件
    
    Args:
        data: 要保存的数据
        file_path: JSON文件路径
        indent: JSON缩进空格数
        
    Returns:
        保存成功返回True，否则返回False
    """
    try:
        # 确保目录存在
        ensure_directory(file_path.parent)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        
        logger.debug(f"保存JSON文件: {file_path}")
        return True
    except Exception as e:
        logger.error(f"保存JSON文件失败 {file_path}: {e}")
        return False


def merge_dicts(base_dict: Dict, new_dict: Dict) -> Dict:
    """
    合并两个字典
    
    Args:
        base_dict: 基础字典
        new_dict: 新字典
        
    Returns:
        合并后的字典
    """
    # 简单的字典合并，新字典的值覆盖基础字典的值
    merged = base_dict.copy()
    merged.update(new_dict)
    return merged


def validate_facts_structure(facts_data: Dict) -> bool:
    """
    验证facts数据结构
    
    Args:
        facts_data: facts数据字典
        
    Returns:
        如果数据结构有效返回True，否则返回False
    """
    if not isinstance(facts_data, dict):
        return False
    
    # 检查必要的字段
    if 'facts' not in facts_data:
        return False
    
    facts = facts_data['facts']
    
    # 检查facts中的字段
    if not isinstance(facts, dict):
        return False
    
    # entities应该是列表
    if 'entities' in facts and not isinstance(facts['entities'], list):
        return False
    
    # relations应该是列表
    if 'relations' in facts and not isinstance(facts['relations'], list):
        return False
    
    # attributes应该是列表
    if 'attributes' in facts and not isinstance(facts['attributes'], list):
        return False
    
    return True


def get_memory_structure(memory_root: Path) -> Dict:
    """
    获取记忆系统目录结构
    
    Args:
        memory_root: 记忆根目录路径
        
    Returns:
        包含目录结构的字典
    """
    try:
        structure = {
            "memory_root": str(memory_root),
            "exists": memory_root.exists(),
            "directories": {}
        }
        
        if not memory_root.exists():
            return structure
        
        # 检查主要目录
        directories = [
            "kg_candidates",
            "kg_data",
            "kg_data/entity",
            "kg_data/relation",
            "scenes"  # 为未来扩展预留
        ]
        
        for dir_path in directories:
            full_path = memory_root / dir_path
            structure["directories"][dir_path] = {
                "path": str(full_path),
                "exists": full_path.exists()
            }
            
            if full_path.exists():
                # 统计文件数量
                json_files = list_json_files(full_path)
                structure["directories"][dir_path]["file_count"] = len(json_files)
        
        return structure
        
    except Exception as e:
        logger.error(f"获取记忆结构失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "memory_root": str(memory_root)
        }


def cleanup_memory_directory(memory_root: Path, confirm: bool = False) -> Dict:
    """
    清理记忆目录
    
    Args:
        memory_root: 记忆根目录路径
        confirm: 如果为True，则实际删除文件；如果为False，只显示将要删除的文件列表
        
    Returns:
        包含清理结果的字典
    """
    try:
        if not memory_root.exists():
            return {
                "success": True,
                "message": f"记忆目录不存在: {memory_root}",
                "deleted": False
            }
        
        # 获取目录结构
        structure = get_memory_structure(memory_root)
        
        if not confirm:
            return {
                "success": True,
                "message": f"预览: 将删除记忆目录 {memory_root}",
                "structure": structure,
                "confirmed": False
            }
        
        # 实际删除目录
        import shutil
        shutil.rmtree(memory_root)
        
        return {
            "success": True,
            "message": f"已删除记忆目录: {memory_root}",
            "deleted": True,
            "confirmed": True
        }
        
    except Exception as e:
        logger.error(f"清理记忆目录失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "memory_root": str(memory_root)
        }


if __name__ == "__main__":
    # 测试系统工具函数
    print("测试系统工具函数")
    
    # 测试目录操作
    test_dir = Path("test_directory")
    ensure_result = ensure_directory(test_dir)
    print(f"ensure_directory 结果: {ensure_result}")
    
    # 测试JSON文件操作
    test_data = {"test": "data", "number": 123}
    test_file = test_dir / "test.json"
    save_result = save_json_file(test_data, test_file)
    print(f"save_json_file 结果: {save_result}")
    
    # 测试加载JSON文件
    loaded_data = load_json_file(test_file)
    print(f"load_json_file 结果: {loaded_data}")
    
    # 测试字典合并
    dict1 = {"a": 1, "b": 2}
    dict2 = {"b": 3, "c": 4}
    merged = merge_dicts(dict1, dict2)
    print(f"merge_dicts 结果: {merged}")
    
    # 清理测试文件
    import shutil
    if test_dir.exists():
        shutil.rmtree(test_dir)
        print(f"清理测试目录: {test_dir}")