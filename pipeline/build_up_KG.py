#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建知识图谱（增量写入）

加载 LongMemorySystem（id=test）完成对其 KG_data 的增量写入。
从 data/memory/{id}/kg_candidates/ 读取候选文件，通过 LongMemorySystem.write_kg_facts 写入 KG 数据。
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

# 添加项目根目录到 sys.path，以便导入模块
script_dir = Path(__file__).parent
project_root = script_dir.parent
sys.path.insert(0, str(project_root))

# 导入 LongMemorySystem 和 KG 工具函数
try:
    from memory.in_memory.long_memory_sys import LongMemorySystem
    from memory.in_memory.utils.KG_utils import (
        load_kg_candidate_files,
        load_kg_candidate,
        process_single_kg_candidate
    )
    from memory.in_memory.utils.sys_utils import load_kg
except ImportError as e:
    logging.error(f"导入模块失败: {e}")
    sys.exit(1)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def build_kg_for_memory_id(memory_id: str = "test", base_path: str = "data/memory") -> Dict:
    """
    为指定 memory_id 构建知识图谱（增量写入）
    
    Args:
        memory_id: 记忆ID，默认为 "test"
        base_path: 基础路径，默认为 "data/memory"
        
    Returns:
        包含处理结果的字典
    """
    logger.info(f"开始为记忆ID '{memory_id}' 构建知识图谱")
    
    # 初始化 LongMemorySystem
    try:
        memory_system = LongMemorySystem(memory_id=memory_id, base_path=base_path)
        logger.info(f"LongMemorySystem 初始化成功，记忆ID: {memory_id}")
        logger.info(f"KG数据目录: {memory_system.kg_data_dir}")
    except Exception as e:
        error_msg = f"初始化 LongMemorySystem 失败: {e}"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}
    
    # 获取 KG 候选文件列表
    kg_candidates_dir = memory_system.kg_candidates_dir
    logger.info(f"扫描 KG 候选目录: {kg_candidates_dir}")
    
    candidate_files = load_kg_candidate_files(kg_candidates_dir)
    if not candidate_files:
        logger.warning(f"未找到 KG 候选文件，目录: {kg_candidates_dir}")
        return {"success": True, "message": "没有需要处理的候选文件", "processed_count": 0}
    
    logger.info(f"找到 {len(candidate_files)} 个 KG 候选文件")
    
    # 统计信息
    stats = {
        "total_files": len(candidate_files),
        "processed_files": 0,
        "successful_files": 0,
        "failed_files": 0,
        "details": []
    }
    
    # 处理每个候选文件
    for file_path in candidate_files:
        file_stats = {
            "file_path": str(file_path),
            "success": False,
            "message": "",
            "error": None
        }
        
        try:
            # 加载候选文件
            candidate_data = load_kg_candidate(file_path)
            if not candidate_data:
                file_stats["message"] = "加载候选文件失败"
                stats["failed_files"] += 1
                stats["details"].append(file_stats)
                logger.warning(f"加载候选文件失败: {file_path}")
                continue
            
            # 提取 kg_candidate 数据
            kg_candidate = candidate_data.get('kg_candidate', {})
            if not kg_candidate:
                file_stats["message"] = "候选文件中缺少 kg_candidate 字段"
                stats["failed_files"] += 1
                stats["details"].append(file_stats)
                logger.warning(f"候选文件中缺少 kg_candidate 字段: {file_path}")
                continue
            
            # 调用 LongMemorySystem.write_kg_facts 写入 KG 数据
            # write_kg_facts 现在期望完整的候选数据（包含 dialogue_id, episode_id 等字段）
            # 传递完整的 candidate_data 以记录来源信息
            # 同时传递源文件路径以启用自动清理（删除候选文件并更新episode）
            result = memory_system.write_kg_facts(
                candidate_data=candidate_data,
                source_file=file_path,
                auto_cleanup=True
            )
            
            if result.get("success", False):
                file_stats["success"] = True
                file_stats["message"] = result.get("message", "写入成功")
                stats["successful_files"] += 1
                logger.info(f"处理成功: {file_path.name} - {result.get('stats', {})}")
            else:
                file_stats["message"] = result.get("error", "写入失败")
                file_stats["error"] = result.get("error")
                stats["failed_files"] += 1
                logger.error(f"处理失败: {file_path.name} - {result.get('error', '未知错误')}")
            
        except Exception as e:
            file_stats["message"] = f"处理过程中发生异常: {e}"
            file_stats["error"] = str(e)
            stats["failed_files"] += 1
            logger.exception(f"处理文件时发生异常: {file_path}")
        
        stats["processed_files"] += 1
        stats["details"].append(file_stats)
    
    # 重新加载 KG 数据以获取最新统计
    kg_data = load_kg(memory_system.kg_data_dir)
    if kg_data["success"]:
        kg_stats = kg_data.get("stats", {})
        logger.info(f"KG数据统计: {kg_stats}")
    else:
        logger.warning(f"无法获取KG数据统计: {kg_data.get('error', '未知错误')}")
        kg_stats = {}
    
    # 汇总结果
    result = {
        "success": stats["successful_files"] > 0 or stats["total_files"] == 0,
        "memory_id": memory_id,
        "total_files": stats["total_files"],
        "processed_files": stats["processed_files"],
        "successful_files": stats["successful_files"],
        "failed_files": stats["failed_files"],
        "kg_stats": kg_stats,
        "details": stats["details"]
    }
    
    logger.info(f"知识图谱构建完成: 成功 {stats['successful_files']}/{stats['total_files']} 个文件")
    return result


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="构建知识图谱（增量写入） - 加载 LongMemorySystem 并处理 KG 候选文件"
    )
    parser.add_argument("--id", type=str, default="test",
                       help="记忆ID（默认为 'test'）")
    parser.add_argument("--base-path", type=str, default="data/memory",
                       help="基础路径（默认为 'data/memory'）")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="启用详细日志输出")
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # 执行构建
    result = build_kg_for_memory_id(memory_id=args.id, base_path=args.base_path)
    
    # 输出结果
    if result["success"]:
        logger.info("=" * 50)
        logger.info(f"知识图谱构建成功完成")
        logger.info(f"记忆ID: {result['memory_id']}")
        logger.info(f"处理文件总数: {result['total_files']}")
        logger.info(f"成功文件数: {result['successful_files']}")
        logger.info(f"失败文件数: {result['failed_files']}")
        logger.info(f"实体数量: {result['kg_stats'].get('entity_count', 'N/A')}")
        logger.info(f"关系数量: {result['kg_stats'].get('relation_count', 'N/A')}")
        logger.info(f"属性数量: {result['kg_stats'].get('attribute_count', 'N/A')}")
        logger.info("=" * 50)
    else:
        logger.error("知识图谱构建失败")
        if "error" in result:
            logger.error(f"错误: {result['error']}")
    
    # 返回退出码
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()