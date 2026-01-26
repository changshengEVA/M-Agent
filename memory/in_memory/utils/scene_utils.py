#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
场景工具函数

包含长期记忆管理系统中与场景相关的工具函数。
集成了FAISS编码方案，支持将scene中的theme进行编码，并按照diary进行召回。
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Union

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 尝试导入FAISS工具
try:
    from memory.in_memory.utils.faiss_utils import FAISSIndexManager, create_faiss_index_manager
    FAISS_AVAILABLE = True
except ImportError as e:
    FAISS_AVAILABLE = False
    logger.warning(f"无法导入FAISS工具: {e}")


def _get_memory_root_from_scenes_dir(scenes_dir: Path) -> Path:
    """
    从场景目录推断记忆根目录
    
    Args:
        scenes_dir: 场景目录路径
        
    Returns:
        记忆根目录路径
    """
    # 假设 scenes_dir 是 {memory_root}/scenes
    memory_root = scenes_dir.parent
    return memory_root


def _get_faiss_manager(scenes_dir: Path) -> Optional[FAISSIndexManager]:
    """
    获取或创建FAISS索引管理器
    
    Args:
        scenes_dir: 场景目录路径
        
    Returns:
        FAISSIndexManager实例，如果FAISS不可用则返回None
    """
    if not FAISS_AVAILABLE:
        logger.warning("FAISS不可用，无法创建索引管理器")
        return None
    
    try:
        memory_root = _get_memory_root_from_scenes_dir(scenes_dir)
        # 从记忆根目录推断memory_id（默认为目录名）
        memory_id = memory_root.name if memory_root.name else "default"
        manager = create_faiss_index_manager(memory_root.parent, memory_id)
        return manager
    except Exception as e:
        logger.error(f"创建FAISS索引管理器失败: {e}")
        return None


def save_scene(scene_data: Dict, scenes_dir: Path) -> Dict:
    """
    保存场景到文件，并将theme编码到FAISS索引
    
    Args:
        scene_data: 场景数据，应包含scene_id, theme, diary等字段
        scenes_dir: 场景目录路径
        
    Returns:
         包含保存结果的字典
    """
    logger.info(f"保存场景: {scene_data.get('scene_id', 'unknown')}")
    
    # 验证必要字段
    scene_id = scene_data.get('scene_id')
    theme = scene_data.get('theme', '')
    diary = scene_data.get('diary', '')
    
    if not scene_id:
        return {
            "success": False,
            "message": "scene_data缺少'scene_id'字段",
            "scene_id": "unknown",
            "scenes_dir": str(scenes_dir)
        }
    
    # 确保场景目录存在
    scenes_dir.mkdir(parents=True, exist_ok=True)
    
    # 保存场景文件
    scene_file = scenes_dir / f"{scene_id}.json"
    try:
        with open(scene_file, 'w', encoding='utf-8') as f:
            json.dump(scene_data, f, ensure_ascii=False, indent=2)
        logger.debug(f"场景文件保存成功: {scene_file}")
    except Exception as e:
        logger.error(f"保存场景文件失败: {e}")
        return {
            "success": False,
            "message": f"保存场景文件失败: {e}",
            "scene_id": scene_id,
            "scenes_dir": str(scenes_dir)
        }
    
    # 添加到FAISS索引
    faiss_result = {"success": False, "message": "FAISS不可用"}
    if FAISS_AVAILABLE:
        manager = _get_faiss_manager(scenes_dir)
        if manager:
            # 提取metadata（排除theme和diary）
            metadata = scene_data.copy()
            metadata.pop('theme', None)
            metadata.pop('diary', None)
            
            success = manager.add_scene(scene_id, theme, diary, metadata)
            if success:
                # 保存索引（定期保存，这里每次添加后都保存）
                memory_root = _get_memory_root_from_scenes_dir(scenes_dir)
                memory_id = memory_root.name if memory_root.name else "default"
                index_dir = memory_root.parent / memory_id / "faiss_index"
                manager.save_index(index_dir)
                faiss_result = {"success": True, "message": "已添加到FAISS索引"}
            else:
                faiss_result = {"success": False, "message": "添加到FAISS索引失败"}
        else:
            faiss_result = {"success": False, "message": "无法获取FAISS管理器"}
    
    # 返回结果
    return {
        "success": True,
        "message": "场景保存成功",
        "scene_id": scene_id,
        "scene_file": str(scene_file),
        "faiss_indexed": faiss_result["success"],
        "faiss_message": faiss_result["message"],
        "scenes_dir": str(scenes_dir)
    }


def load_scene(scene_id: str, scenes_dir: Path) -> Optional[Dict]:
    """
    加载场景文件
    
    Args:
        scene_id: 场景ID
        scenes_dir: 场景目录路径
        
    Returns:
        场景数据字典，如果加载失败则返回None
    """
    logger.info(f"加载场景: {scene_id}")
    
    scene_file = scenes_dir / f"{scene_id}.json"
    if not scene_file.exists():
        logger.warning(f"场景文件不存在: {scene_file}")
        return None
    
    try:
        with open(scene_file, 'r', encoding='utf-8') as f:
            scene_data = json.load(f)
        return scene_data
    except Exception as e:
        logger.error(f"加载场景文件失败: {e}")
        return None


def search_scenes(query: str, scenes_dir: Path, top_k: int = 5) -> List[Dict]:
    """
    搜索场景：按照diary进行召回
    
    Args:
        query: 查询字符串（diary文本）
        scenes_dir: 场景目录路径
        top_k: 返回结果的数量
        
    Returns:
        场景数据列表，每个元素包含场景数据和相似度信息
    """
    logger.info(f"搜索场景: query='{query}', top_k={top_k}")
    
    if not FAISS_AVAILABLE:
        logger.warning("FAISS不可用，无法进行向量搜索")
        return []
    
    manager = _get_faiss_manager(scenes_dir)
    if not manager:
        logger.warning("无法获取FAISS管理器，返回空结果")
        return []
    
    # 使用FAISS搜索
    faiss_results = manager.search_by_diary(query, top_k)
    
    # 加载完整的场景数据
    results = []
    for faiss_result in faiss_results:
        scene_id = faiss_result["scene_id"]
        scene_data = load_scene(scene_id, scenes_dir)
        
        if scene_data:
            result = {
                "scene_data": scene_data,
                "similarity": faiss_result["similarity"],
                "rank": faiss_result["rank"],
                "search_metadata": {
                    "query": query,
                    "matched_by": "diary",
                    "faiss_index_id": faiss_result.get("index_id", -1)
                }
            }
            results.append(result)
        else:
            logger.warning(f"无法加载场景数据: {scene_id}")
    
    logger.info(f"找到 {len(results)} 个匹配的场景")
    return results


def get_scene_stats(scenes_dir: Path) -> Dict:
    """
    获取场景统计信息
    
    Args:
        scenes_dir: 场景目录路径
        
    Returns:
        包含统计信息的字典
    """
    logger.info("获取场景统计信息")
    
    # 统计文件数量
    scene_files = []
    if scenes_dir.exists():
        scene_files = list(scenes_dir.glob("*.json"))
    
    scene_count = len(scene_files)
    
    # FAISS索引统计
    faiss_stats = {}
    if FAISS_AVAILABLE:
        manager = _get_faiss_manager(scenes_dir)
        if manager:
            faiss_stats = manager.get_stats()
    
    return {
        "success": True,
        "message": "获取场景统计信息成功",
        "scene_count": scene_count,
        "scene_files": [str(f.name) for f in scene_files[:10]],  # 只显示前10个
        "faiss_available": FAISS_AVAILABLE,
        "faiss_stats": faiss_stats,
        "scenes_dir": str(scenes_dir)
    }


def cleanup_scenes(scenes_dir: Path, confirm: bool = False) -> Dict:
    """
    清理场景目录
    
    Args:
        scenes_dir: 场景目录路径
        confirm: 如果为True，则实际删除文件；如果为False，只显示将要删除的文件列表
        
    Returns:
        包含清理结果的字典
    """
    logger.info(f"清理场景目录: confirm={confirm}")
    
    if not scenes_dir.exists():
        return {
            "success": True,
            "message": f"场景目录不存在: {scenes_dir}",
            "deleted": False
        }
    
    # 获取所有场景文件
    scene_files = list(scenes_dir.glob("*.json"))
    
    if not scene_files:
        return {
            "success": True,
            "message": "没有找到场景文件",
            "deleted": False
        }
    
    if not confirm:
        return {
            "success": True,
            "message": f"预览: 将删除 {len(scene_files)} 个场景文件",
            "scene_files": [str(f.name) for f in scene_files[:5]],
            "total_files": len(scene_files),
            "confirmed": False
        }
    
    # 实际删除文件
    deleted_count = 0
    for scene_file in scene_files:
        try:
            scene_file.unlink()
            deleted_count += 1
            logger.debug(f"已删除场景文件: {scene_file}")
        except Exception as e:
            logger.error(f"删除场景文件失败 {scene_file}: {e}")
    
    # 清理FAISS索引（可选）
    if FAISS_AVAILABLE and deleted_count > 0:
        try:
            manager = _get_faiss_manager(scenes_dir)
            if manager:
                # 重建空索引
                memory_root = _get_memory_root_from_scenes_dir(scenes_dir)
                memory_id = memory_root.name if memory_root.name else "default"
                index_dir = memory_root.parent / memory_id / "faiss_index"
                
                # 创建新的空管理器并保存
                from memory.in_memory.utils.faiss_utils import FAISSIndexManager
                new_manager = FAISSIndexManager()
                new_manager.save_index(index_dir)
                logger.info("FAISS索引已重置")
        except Exception as e:
            logger.error(f"清理FAISS索引失败: {e}")
    
    return {
        "success": True,
        "message": f"已删除 {deleted_count}/{len(scene_files)} 个场景文件",
        "deleted_count": deleted_count,
        "total_files": len(scene_files),
        "confirmed": True
    }


def rebuild_faiss_index(scenes_dir: Path) -> Dict:
    """
    重建FAISS索引（从现有场景文件）
    
    Args:
        scenes_dir: 场景目录路径
        
    Returns:
        包含重建结果的字典
    """
    if not FAISS_AVAILABLE:
        return {
            "success": False,
            "message": "FAISS不可用，无法重建索引"
        }
    
    logger.info("开始重建FAISS索引")
    
    # 获取所有场景文件
    scene_files = list(scenes_dir.glob("*.json"))
    if not scene_files:
        return {
            "success": False,
            "message": "没有找到场景文件，无法重建索引"
        }
    
    # 创建新的FAISS管理器
    memory_root = _get_memory_root_from_scenes_dir(scenes_dir)
    memory_id = memory_root.name if memory_root.name else "default"
    
    try:
        from memory.in_memory.utils.faiss_utils import FAISSIndexManager
        manager = FAISSIndexManager()
        
        added_count = 0
        error_count = 0
        
        for scene_file in scene_files:
            try:
                with open(scene_file, 'r', encoding='utf-8') as f:
                    scene_data = json.load(f)
                
                scene_id = scene_data.get('scene_id')
                theme = scene_data.get('theme', '')
                diary = scene_data.get('diary', '')
                
                if not scene_id:
                    logger.warning(f"场景文件缺少scene_id: {scene_file}")
                    continue
                
                # 提取metadata
                metadata = scene_data.copy()
                metadata.pop('theme', None)
                metadata.pop('diary', None)
                
                success = manager.add_scene(scene_id, theme, diary, metadata)
                if success:
                    added_count += 1
                else:
                    error_count += 1
                    
            except Exception as e:
                logger.error(f"处理场景文件失败 {scene_file}: {e}")
                error_count += 1
        
        # 保存索引
        index_dir = memory_root.parent / memory_id / "faiss_index"
        manager.save_index(index_dir)
        
        return {
            "success": True,
            "message": f"FAISS索引重建完成，成功添加 {added_count} 个场景，失败 {error_count} 个",
            "added_count": added_count,
            "error_count": error_count,
            "total_files": len(scene_files),
            "index_dir": str(index_dir)
        }
        
    except Exception as e:
        logger.error(f"重建FAISS索引失败: {e}")
        return {
            "success": False,
            "message": f"重建FAISS索引失败: {e}"
        }


if __name__ == "__main__":
    # 测试场景工具函数
    print("测试场景工具函数")
    
    # 创建测试目录
    test_scenes_dir = Path("test_scenes")
    test_scenes_dir.mkdir(exist_ok=True)
    
    try:
        # 测试保存场景
        test_scene = {
            "scene_id": "scene_test_001",
            "scene_version": "v1",
            "theme": "人工智能与机器学习",
            "diary": "今天学习了深度神经网络的基本原理",
            "meta": {
                "created_at": "2025-01-21T09:00:00Z",
                "memory_owner": "test"
            }
        }
        
        save_result = save_scene(test_scene, test_scenes_dir)
        print(f"save_scene 结果: {save_result['message']}")
        print(f"  FAISS索引: {save_result.get('faiss_message', 'N/A')}")
        
        # 测试加载场景
        loaded_scene = load_scene("scene_test_001", test_scenes_dir)
        print(f"load_scene 结果: {'成功' if loaded_scene else '失败'}")
        
        # 测试搜索场景
        search_results = search_scenes("深度学习模型", test_scenes_dir, top_k=2)
        print(f"search_scenes 结果: 找到 {len(search_results)} 个匹配")
        
        # 测试统计信息
        stats = get_scene_stats(test_scenes_dir)
        print(f"get_scene_stats 结果: {stats['scene_count']} 个场景")
        
        # 测试重建索引（如果FAISS可用）
        if FAISS_AVAILABLE:
            rebuild_result = rebuild_faiss_index(test_scenes_dir)
            print(f"rebuild_faiss_index 结果: {rebuild_result['message']}")
        
        # 测试清理（预览模式）
        cleanup_preview = cleanup_scenes(test_scenes_dir, confirm=False)
        print(f"cleanup_scenes 预览: {cleanup_preview['message']}")
        
    finally:
        # 清理测试目录
        import shutil
        if test_scenes_dir.exists():
            shutil.rmtree(test_scenes_dir, ignore_errors=True)
            print(f"清理测试目录: {test_scenes_dir}")