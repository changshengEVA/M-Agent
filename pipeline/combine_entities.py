#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实体合并工具：使用重构后的 KGManager 合并知识图谱中的实体

功能：
1. 合并两个实体，将源实体的属性、关系、特征添加到目标实体上
2. 更新所有相关的关系文件
3. 删除源实体文件

使用示例：
# 合并 test3 中的"北大"到"Peking_University"
python pipeline/combine_entities.py --id test3 --target Peking_University --source 北大

# 查看合并前的实体信息
python pipeline/combine_entities.py --id test3 --target Peking_University --source 北大 --dry-run

# 查看 test3 中的所有实体
python pipeline/combine_entities.py --id test3 --list
"""

import json
import os
import logging
import sys
from pathlib import Path
import argparse

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 添加项目根目录到sys.path以便导入KGManager
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
sys.path.append(project_root)

try:
    from memory.memory_sys.kg_manager import KGManager
except ImportError as e:
    logger.error(f"导入KGManager失败: {e}")
    logger.error("请确保memory/memory_sys/kg_manager.py文件存在")
    sys.exit(1)


def get_kg_data_dir(process_id: str) -> Path:
    """
    获取KG数据目录路径
    
    Args:
        process_id: 处理流ID
        
    Returns:
        KG数据目录路径
    """
    return Path(project_root) / "data" / "memory" / process_id / "kg_data"


def list_entities(process_id: str):
    """
    列出指定处理流中的所有实体
    
    Args:
        process_id: 处理流ID
    """
    kg_data_dir = get_kg_data_dir(process_id)
    
    if not kg_data_dir.exists():
        logger.error(f"KG数据目录不存在: {kg_data_dir}")
        return
    
    entity_dir = kg_data_dir / "entity"
    
    if not entity_dir.exists():
        logger.error(f"实体目录不存在: {entity_dir}")
        return
    
    # 获取所有实体文件
    entity_files = list(entity_dir.glob("*.json"))
    
    if not entity_files:
        logger.info(f"在 {process_id} 中没有找到实体文件")
        return
    
    logger.info(f"在 {process_id} 中找到 {len(entity_files)} 个实体:")
    logger.info("=" * 60)
    
    for entity_file in sorted(entity_files):
        try:
            with open(entity_file, 'r', encoding='utf-8') as f:
                entity_data = json.load(f)
            
            entity_id = entity_data.get('id', entity_file.stem)
            entity_type = entity_data.get('type', '未知')
            feature_count = len(entity_data.get('features', []))
            attribute_count = len(entity_data.get('attributes', []))
            source_count = len(entity_data.get('sources', []))
            
            logger.info(f"实体: {entity_id}")
            logger.info(f"  文件: {entity_file.name}")
            logger.info(f"  类型: {entity_type}")
            logger.info(f"  特征数: {feature_count}")
            logger.info(f"  属性数: {attribute_count}")
            logger.info(f"  来源数: {source_count}")
            logger.info("-" * 40)
            
        except Exception as e:
            logger.warning(f"读取实体文件 {entity_file} 失败: {e}")
            continue


def show_entity_details(process_id: str, entity_id: str):
    """
    显示实体的详细信息
    
    Args:
        process_id: 处理流ID
        entity_id: 实体ID
    """
    kg_data_dir = get_kg_data_dir(process_id)
    
    if not kg_data_dir.exists():
        logger.error(f"KG数据目录不存在: {kg_data_dir}")
        return
    
    # 初始化 KGManager
    kg_manager = KGManager(
        kg_data_dir=str(kg_data_dir),
        workflow_id=process_id
    )
    
    # 使用 EntityStorage 加载实体
    from memory.memory_sys.storage.entity_storage import EntityStorage
    entity_storage = EntityStorage(kg_data_dir / "entity")
    
    entity_data = entity_storage.load_entity(entity_id)
    
    if entity_data is None:
        logger.error(f"实体 '{entity_id}' 不存在")
        return
    
    logger.info(f"实体 '{entity_id}' 的详细信息:")
    logger.info("=" * 60)
    
    # 显示基本信息
    logger.info(f"ID: {entity_data.get('id')}")
    logger.info(f"类型: {entity_data.get('type', '未指定')}")
    logger.info(f"置信度: {entity_data.get('confidence', '未指定')}")
    
    # 显示来源信息
    sources = entity_data.get('sources', [])
    logger.info(f"来源数: {len(sources)}")
    if sources:
        logger.info("来源列表:")
        for i, source in enumerate(sources[:5], 1):  # 只显示前5个
            logger.info(f"  {i}. 对话: {source.get('dialogue_id')}, "
                       f"情节: {source.get('episode_id')}, "
                       f"场景: {source.get('scene_id', '未指定')}")
        if len(sources) > 5:
            logger.info(f"  ... 还有 {len(sources) - 5} 个来源")
    
    # 显示特征
    features = entity_data.get('features', [])
    logger.info(f"特征数: {len(features)}")
    if features:
        logger.info("特征列表:")
        for i, feature in enumerate(features[:5], 1):  # 只显示前5个
            feature_text = feature.get('feature', '')
            # 截断过长的特征文本
            if len(feature_text) > 100:
                feature_text = feature_text[:100] + "..."
            logger.info(f"  {i}. {feature_text}")
        if len(features) > 5:
            logger.info(f"  ... 还有 {len(features) - 5} 个特征")
    
    # 显示属性
    attributes = entity_data.get('attributes', [])
    logger.info(f"属性数: {len(attributes)}")
    if attributes:
        logger.info("属性列表:")
        for attr in attributes:
            field = attr.get('field', '未知')
            value = attr.get('value', '未知')
            confidence = attr.get('confidence', '未指定')
            logger.info(f"  - {field}: {value} (置信度: {confidence})")
    
    logger.info("=" * 60)


def dry_run_combine(process_id: str, target_entity: str, source_entity: str):
    """
    模拟合并操作，显示合并前的信息但不实际执行
    
    Args:
        process_id: 处理流ID
        target_entity: 目标实体ID
        source_entity: 源实体ID
    """
    kg_data_dir = get_kg_data_dir(process_id)
    
    if not kg_data_dir.exists():
        logger.error(f"KG数据目录不存在: {kg_data_dir}")
        return
    
    # 初始化 KGManager
    kg_manager = KGManager(
        kg_data_dir=str(kg_data_dir),
        workflow_id=process_id
    )
    
    logger.info("=" * 60)
    logger.info("模拟合并操作（dry-run）")
    logger.info(f"目标实体: {target_entity}")
    logger.info(f"源实体: {source_entity}")
    logger.info("=" * 60)
    
    # 显示两个实体的详细信息
    logger.info("合并前的实体信息:")
    logger.info("-" * 40)
    show_entity_details(process_id, target_entity)
    logger.info("-" * 40)
    show_entity_details(process_id, source_entity)
    
    # 获取统计信息
    stats = kg_manager.get_stats()
    if stats.get('success', False):
        logger.info(f"当前实体总数: {stats.get('entity_count', 0)}")
        logger.info(f"当前关系总数: {stats.get('relation_count', 0)}")
    
    logger.info("=" * 60)
    logger.info("注意：这是模拟运行，不会实际执行合并操作")
    logger.info("要实际执行合并，请移除 --dry-run 参数")
    logger.info("=" * 60)


def combine_entities(process_id: str, target_entity: str, source_entity: str):
    """
    实际合并两个实体
    
    Args:
        process_id: 处理流ID
        target_entity: 目标实体ID
        source_entity: 源实体ID
        
    Returns:
        合并结果字典
    """
    kg_data_dir = get_kg_data_dir(process_id)
    
    if not kg_data_dir.exists():
        logger.error(f"KG数据目录不存在: {kg_data_dir}")
        return {"success": False, "error": f"KG数据目录不存在: {kg_data_dir}"}
    
    # 初始化 KGManager
    logger.info("初始化 KGManager...")
    kg_manager = KGManager(
        kg_data_dir=str(kg_data_dir),
        workflow_id=process_id
    )
    
    logger.info("=" * 60)
    logger.info(f"开始合并实体: {source_entity} -> {target_entity}")
    logger.info("=" * 60)
    
    # 获取合并前的统计信息
    stats_before = kg_manager.get_stats()
    
    # 执行合并操作
    logger.info(f"执行合并操作: {source_entity} -> {target_entity}")
    result = kg_manager.combine_entity(target_entity, source_entity)
    
    # 显示合并结果
    logger.info("=" * 60)
    logger.info("合并操作完成")
    logger.info(f"成功: {result.get('success', False)}")
    logger.info(f"消息: {result.get('message', '无消息')}")
    
    if result.get("success", False):
        # 显示合并统计
        stats = result.get("stats", {})
        logger.info("合并统计:")
        logger.info(f"  特征添加: {stats.get('features_added', 0)}个")
        logger.info(f"  特征合并: {stats.get('features_merged', 0)}个")
        logger.info(f"  属性添加: {stats.get('attributes_added', 0)}个")
        logger.info(f"  属性合并: {stats.get('attributes_merged', 0)}个")
        logger.info(f"  来源添加: {stats.get('sources_added', 0)}个")
        logger.info(f"  关系更新: {stats.get('relations_updated', 0)}个")
        logger.info(f"  关系删除: {stats.get('relations_deleted', 0)}个")
        logger.info(f"  关系合并: {stats.get('relations_merged', 0)}个")
        
        # 获取合并后的统计信息
        stats_after = kg_manager.get_stats()
        
        logger.info("合并前后对比:")
        logger.info(f"  实体数量: {stats_before.get('entity_count', 0)} -> {stats_after.get('entity_count', 0)}")
        logger.info(f"  关系数量: {stats_before.get('relation_count', 0)} -> {stats_after.get('relation_count', 0)}")
        
        # 显示更新后的目标实体信息
        logger.info("-" * 40)
        logger.info(f"合并后的目标实体 '{target_entity}' 信息:")
        show_entity_details(process_id, target_entity)
        
    else:
        logger.error(f"合并失败: {result.get('message', '未知错误')}")
    
    logger.info("=" * 60)
    
    return result


def main():
    """
    主函数：命令行接口
    """
    parser = argparse.ArgumentParser(
        description="实体合并工具：使用重构后的 KGManager 合并知识图谱中的实体"
    )
    parser.add_argument("--id", type=str, required=True,
                       help="处理流ID（必需）")
    parser.add_argument("--target", type=str,
                       help="目标实体ID（合并到该实体）")
    parser.add_argument("--source", type=str,
                       help="源实体ID（从该实体合并数据）")
    parser.add_argument("--dry-run", action="store_true",
                       help="模拟运行，不实际执行合并操作")
    parser.add_argument("--list", action="store_true",
                       help="列出所有实体")
    parser.add_argument("--show", type=str,
                       help="显示指定实体的详细信息")
    
    args = parser.parse_args()
    
    logger.info(f"实体合并工具 - 处理流ID: {args.id}")
    
    # 检查参数组合
    if args.list:
        # 列出所有实体
        list_entities(args.id)
        return
    
    if args.show:
        # 显示实体详细信息
        show_entity_details(args.id, args.show)
        return
    
    if not args.target or not args.source:
        logger.error("必须指定 --target 和 --source 参数来合并实体")
        logger.error("或使用 --list 列出实体，或使用 --show 显示实体详情")
        return
    
    if args.dry_run:
        # 模拟合并
        dry_run_combine(args.id, args.target, args.source)
    else:
        # 实际合并
        combine_entities(args.id, args.target, args.source)


if __name__ == "__main__":
    main()