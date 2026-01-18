#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知识图谱工具函数

包含长期记忆管理系统中与知识图谱相关的工具函数。
"""

import os
import json
import uuid
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Union

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def generate_relation_filename() -> str:
    """
    生成不重复的关系文件名
    
    Returns:
        格式为 {uuid}.json 的文件名
    """
    # 使用UUID确保唯一性
    relation_id = str(uuid.uuid4())
    return f"{relation_id}.json"


def sanitize_entity_name(entity_name: str) -> str:
    """
    清理实体名称，使其适合作为文件名
    
    Args:
        entity_name: 原始实体名称
        
    Returns:
        清理后的实体名称
    """
    # 替换可能引起问题的字符
    sanitized = entity_name.strip()
    # 替换空格为下划线
    sanitized = sanitized.replace(' ', '_')
    # 替换其他可能的问题字符
    for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
        sanitized = sanitized.replace(char, '_')
    # 限制长度
    if len(sanitized) > 100:
        sanitized = sanitized[:100]
    return sanitized


def load_kg_candidate_files(kg_candidates_dir: Path) -> List[Path]:
    """
    加载所有KG候选文件
    
    Args:
        kg_candidates_dir: KG候选目录路径
        
    Returns:
        KG候选文件路径列表
    """
    kg_candidate_files = []
    
    if not kg_candidates_dir.exists():
        logger.warning(f"KG候选目录不存在: {kg_candidates_dir}")
        return kg_candidate_files
    
    for file_path in kg_candidates_dir.iterdir():
        if file_path.is_file() and file_path.suffix == '.json':
            try:
                # 检查文件名是否为数字格式（如 00001.json）
                int(file_path.stem)
                kg_candidate_files.append(file_path)
            except ValueError:
                # 不是数字格式的文件，跳过
                continue
    
    logger.info(f"找到 {len(kg_candidate_files)} 个KG候选文件")
    return kg_candidate_files


def load_kg_candidate(file_path: Path) -> Optional[Dict]:
    """
    加载单个KG候选文件
    
    Args:
        file_path: KG候选文件路径
        
    Returns:
        KG候选数据字典，如果加载失败则返回None
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 验证数据结构
        if 'kg_candidate' not in data:
            logger.warning(f"文件 {file_path} 缺少 'kg_candidate' 字段")
            return None
        
        return data
    
    except json.JSONDecodeError as e:
        logger.error(f"解析JSON文件失败 {file_path}: {e}")
        return None
    except Exception as e:
        logger.error(f"加载KG候选文件失败 {file_path}: {e}")
        return None


def process_single_kg_candidate(file_path: Path, kg_entity_dir: Path, kg_relation_dir: Path) -> Dict:
    """
    处理单个KG候选文件，将其写入KG数据目录
    
    Args:
        file_path: KG候选文件路径
        kg_entity_dir: 实体目录路径
        kg_relation_dir: 关系目录路径
        
    Returns:
        包含处理结果的字典
    """
    try:
        # 加载KG候选数据
        kg_candidate_data = load_kg_candidate(file_path)
        if not kg_candidate_data:
            return {
                "success": False,
                "message": f"加载KG候选文件失败: {file_path}",
                "file_path": str(file_path)
            }
        
        # 提取facts数据
        kg_candidate = kg_candidate_data.get('kg_candidate', {})
        facts = kg_candidate.get('facts', {})
        
        if not facts:
            return {
                "success": False,
                "message": f"KG候选文件没有facts数据: {file_path}",
                "file_path": str(file_path)
            }
        
        # 统计信息
        stats = {
            "entities_processed": 0,
            "entities_saved": 0,
            "relations_processed": 0,
            "relations_saved": 0,
            "attributes_processed": 0,
            "attributes_saved": 0
        }
        
        # 处理实体
        entities = facts.get('entities', [])
        for entity in entities:
            stats["entities_processed"] += 1
            if save_entity(entity, kg_entity_dir):
                stats["entities_saved"] += 1
        
        # 处理关系
        relations = facts.get('relations', [])
        for relation in relations:
            stats["relations_processed"] += 1
            if save_relation(relation, kg_relation_dir):
                stats["relations_saved"] += 1
        
        # 处理属性
        attributes = facts.get('attributes', [])
        for attribute in attributes:
            stats["attributes_processed"] += 1
            if save_attribute(attribute, kg_entity_dir):
                stats["attributes_saved"] += 1
        
        # 返回结果
        return {
            "success": stats["entities_saved"] > 0 or stats["relations_saved"] > 0 or stats["attributes_saved"] > 0,
            "message": f"处理KG候选文件完成: {file_path.name}",
            "file_path": str(file_path),
            "stats": stats
        }
        
    except Exception as e:
        logger.error(f"处理KG候选文件失败 {file_path}: {e}")
        return {
            "success": False,
            "message": f"处理KG候选文件失败: {e}",
            "file_path": str(file_path),
            "error": str(e)
        }


def save_entity(entity_data: Dict, kg_entity_dir: Path) -> bool:
    """
    保存实体到文件
    
    Args:
        entity_data: 实体数据，包含 id, type, confidence 等字段
        kg_entity_dir: 实体目录路径
        
    Returns:
        保存成功返回True，否则返回False
    """
    try:
        entity_id = entity_data.get('id')
        if not entity_id:
            logger.warning("实体数据缺少 'id' 字段")
            return False
        
        # 清理实体名称作为文件名
        sanitized_name = sanitize_entity_name(entity_id)
        entity_file = kg_entity_dir / f"{sanitized_name}.json"
        
        # 如果文件已存在，合并数据
        existing_data = {}
        if entity_file.exists():
            try:
                with open(entity_file, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
            except Exception:
                # 如果读取失败，创建新文件
                existing_data = {}
        
        # 合并数据（简单的覆盖策略，实际可能需要更复杂的合并逻辑）
        merged_data = {**existing_data, **entity_data}
        
        # 保存实体文件
        with open(entity_file, 'w', encoding='utf-8') as f:
            json.dump(merged_data, f, ensure_ascii=False, indent=2)
        
        logger.debug(f"保存实体: {entity_id} -> {entity_file}")
        return True
        
    except Exception as e:
        logger.error(f"保存实体失败 {entity_data.get('id', 'unknown')}: {e}")
        return False


def save_relation(relation_data: Dict, kg_relation_dir: Path) -> bool:
    """
    保存关系到文件
    
    Args:
        relation_data: 关系数据，包含 subject, relation, object, confidence 等字段
        kg_relation_dir: 关系目录路径
        
    Returns:
        保存成功返回True，否则返回False
    """
    try:
        # 生成唯一的关系文件名
        relation_filename = generate_relation_filename()
        relation_file = kg_relation_dir / relation_filename
        
        # 保存关系文件
        with open(relation_file, 'w', encoding='utf-8') as f:
            json.dump(relation_data, f, ensure_ascii=False, indent=2)
        
        # 记录关系信息
        subject = relation_data.get('subject', 'unknown')
        relation_type = relation_data.get('relation', 'unknown')
        obj = relation_data.get('object', 'unknown')
        logger.debug(f"保存关系: {subject} -[{relation_type}]-> {obj} -> {relation_file}")
        
        return True
        
    except Exception as e:
        logger.error(f"保存关系失败: {e}")
        return False


def save_attribute(attribute_data: Dict, kg_entity_dir: Path) -> bool:
    """
    保存属性到对应的实体文件
    
    Args:
        attribute_data: 属性数据，包含 entity, field, value, confidence 等字段
        kg_entity_dir: 实体目录路径
        
    Returns:
        保存成功返回True，否则返回False
    """
    try:
        entity_id = attribute_data.get('entity')
        if not entity_id:
            logger.warning("属性数据缺少 'entity' 字段")
            return False
        
        # 清理实体名称作为文件名
        sanitized_name = sanitize_entity_name(entity_id)
        entity_file = kg_entity_dir / f"{sanitized_name}.json"
        
        # 加载现有实体数据
        existing_data = {}
        if entity_file.exists():
            try:
                with open(entity_file, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
            except Exception:
                # 如果读取失败，创建新数据
                existing_data = {}
        
        # 确保 attributes 字段存在
        if 'attributes' not in existing_data:
            existing_data['attributes'] = []
        
        # 添加新属性
        field = attribute_data.get('field')
        value = attribute_data.get('value')
        
        # 检查是否已存在相同字段的属性
        attribute_found = False
        for attr in existing_data['attributes']:
            if attr.get('field') == field:
                # 更新现有属性
                attr.update(attribute_data)
                attribute_found = True
                break
        
        if not attribute_found:
            # 添加新属性
            existing_data['attributes'].append(attribute_data)
        
        # 保存更新后的实体文件
        with open(entity_file, 'w', encoding='utf-8') as f:
            json.dump(existing_data, f, ensure_ascii=False, indent=2)
        
        logger.debug(f"保存属性: {entity_id}.{field} = {value}")
        return True
        
    except Exception as e:
        logger.error(f"保存属性失败: {e}")
        return False


def get_kg_stats(kg_data_dir: Path) -> Dict:
    """
    获取KG数据统计信息
    
    Args:
        kg_data_dir: KG数据目录路径
        
    Returns:
        包含统计信息的字典
    """
    try:
        kg_entity_dir = kg_data_dir / "entity"
        kg_relation_dir = kg_data_dir / "relation"
        
        entity_files = list(kg_entity_dir.glob("*.json")) if kg_entity_dir.exists() else []
        relation_files = list(kg_relation_dir.glob("*.json")) if kg_relation_dir.exists() else []
        
        # 计算实体总数
        entity_count = len(entity_files)
        
        # 计算关系总数
        relation_count = len(relation_files)
        
        # 计算属性总数（需要读取所有实体文件）
        attribute_count = 0
        for entity_file in entity_files[:100]:  # 限制读取数量，避免性能问题
            try:
                with open(entity_file, 'r', encoding='utf-8') as f:
                    entity_data = json.load(f)
                attributes = entity_data.get('attributes', [])
                attribute_count += len(attributes)
            except Exception:
                continue
        
        return {
            "success": True,
            "entity_count": entity_count,
            "relation_count": relation_count,
            "attribute_count": attribute_count,
            "entity_files": [str(f.name) for f in entity_files[:10]],  # 只显示前10个
            "relation_files": [str(f.name) for f in relation_files[:10]]  # 只显示前10个
        }
        
    except Exception as e:
        logger.error(f"获取KG统计信息失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def cleanup_kg_data(kg_data_dir: Path, confirm: bool = False) -> Dict:
    """
    清理KG数据目录
    
    Args:
        kg_data_dir: KG数据目录路径
        confirm: 如果为True，则实际删除文件；如果为False，只显示将要删除的文件列表
        
    Returns:
        包含清理结果的字典
    """
    try:
        kg_entity_dir = kg_data_dir / "entity"
        kg_relation_dir = kg_data_dir / "relation"
        
        entity_files = list(kg_entity_dir.glob("*.json")) if kg_entity_dir.exists() else []
        relation_files = list(kg_relation_dir.glob("*.json")) if kg_relation_dir.exists() else []
        
        total_files = len(entity_files) + len(relation_files)
        
        if not confirm:
            return {
                "success": True,
                "message": f"预览: 将删除 {len(entity_files)} 个实体文件和 {len(relation_files)} 个关系文件",
                "entity_files": [str(f.name) for f in entity_files[:5]],
                "relation_files": [str(f.name) for f in relation_files[:5]],
                "total_files": total_files,
                "confirmed": False
            }
        
        # 实际删除文件
        deleted_entity_count = 0
        deleted_relation_count = 0
        
        for entity_file in entity_files:
            try:
                entity_file.unlink()
                deleted_entity_count += 1
            except Exception as e:
                logger.error(f"删除实体文件失败 {entity_file}: {e}")
        
        for relation_file in relation_files:
            try:
                relation_file.unlink()
                deleted_relation_count += 1
            except Exception as e:
                logger.error(f"删除关系文件失败 {relation_file}: {e}")
        
        return {
            "success": True,
            "message": f"已删除 {deleted_entity_count} 个实体文件和 {deleted_relation_count} 个关系文件",
            "deleted_entity_count": deleted_entity_count,
            "deleted_relation_count": deleted_relation_count,
            "total_deleted": deleted_entity_count + deleted_relation_count,
            "confirmed": True
        }
        
    except Exception as e:
        logger.error(f"清理KG数据失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


if __name__ == "__main__":
    # 测试KG工具函数
    print("测试知识图谱工具函数")
    
    # 测试实体名称清理
    test_names = ["Test Entity", "Test/Entity", "Test:Entity", "Test*Entity?"]
    for name in test_names:
        sanitized = sanitize_entity_name(name)
        print(f"清理实体名称: '{name}' -> '{sanitized}'")
    
    # 测试生成关系文件名
    for i in range(3):
        filename = generate_relation_filename()
        print(f"生成关系文件名 {i+1}: {filename}")