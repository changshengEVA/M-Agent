#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对话数据加载接口

两个接口：
1. load_from_dialogue_json: 处理单个 kg_candidate JSON 文件
2. load_from_dialogue_path: 处理 kg_candidate 目录下的所有文件
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from tqdm import tqdm

from memory.memory_core.core.kg_base import KGBase
from memory.memory_core.services_bank.entity_resolution.service import EntityResolutionService

logger = logging.getLogger(__name__)


def load_from_dialogue_json(
    json_data: Dict[str, Any],
    kg_base: KGBase,
    entity_resolution_service: EntityResolutionService,
    memory_core: Optional[Any] = None,
    save: bool = True
) -> Dict[str, Any]:
    """
    从单个对话 JSON 数据加载到 KG

    接收参数：
    1. json数据（格式参照kg_candidate中的单个文件）
    2. kg_base操作类的实例化
    3. EntityResolutionService类的实例化
    4. memory_core: MemoryCore实例（可选，如果提供则使用其resolve_entity方法）

    接口功能：接收并解析json数据，使用kg_base操作类将信息加入到kg本体数据中；
    操作完之后调用EntityResolutionService类扫描KG

    Args:
        json_data: 对话 JSON 数据
        kg_base: KGBase 实例
        entity_resolution_service: EntityResolutionService 实例
        memory_core: MemoryCore实例（可选）
        
    Returns:
        操作结果字典
    """
    logger.info("开始加载单个对话 JSON 数据")
    
    # 提取 kg_candidate 部分
    kg_candidate = json_data.get("kg_candidate", {})
    facts = kg_candidate.get("facts", {})
    
    entities = facts.get("entities", [])
    features = facts.get("features", [])
    attributes = facts.get("attributes", [])
    relations = facts.get("relations", [])
    
    episode_id = json_data.get("episode_id", "unknown")
    dialogue_id = json_data.get("dialogue_id", "unknown")
    
    logger.info(f"处理对话 {dialogue_id} (episode: {episode_id})")
    logger.info(f"发现 {len(entities)} 个实体, {len(features)} 个特征, {len(attributes)} 个属性, {len(relations)} 个关系")
    
    results = {
        "episode_id": episode_id,
        "dialogue_id": dialogue_id,
        "entities_processed": 0,
        "features_processed": 0,
        "attributes_processed": 0,
        "relations_processed": 0,
        "entity_errors": [],
        "feature_errors": [],
        "attribute_errors": [],
        "relation_errors": [],
        "resolution_applied": False
    }
    
    # 1. 处理实体
    for entity_data in entities:
        entity_id = entity_data.get("id")
        entity_type = entity_data.get("type")
        confidence = entity_data.get("confidence", 1.0)
        
        if not entity_id:
            logger.warning("跳过无ID的实体")
            continue
        
        try:
            # 检查实体是否已存在
            check_result = kg_base.assert_entity_exists(entity_id)
            if not check_result.get("success", False):
                # 实体不存在，创建新实体
                add_result = kg_base.add_entity(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    source_info={
                        "episode_id": episode_id,
                        "dialogue_id": dialogue_id,
                        "confidence": confidence
                    }
                )
                
                if add_result.get("success", False):
                    logger.debug(f"创建实体: {entity_id} (类型: {entity_type})")
                    results["entities_processed"] += 1
                else:
                    logger.warning(f"创建实体失败: {entity_id}, 结果: {add_result}")
                    results["entity_errors"].append({
                        "entity_id": entity_id,
                        "error": add_result.get("error", "unknown")
                    })
            else:
                # 实体已存在，跳过创建
                logger.debug(f"实体已存在: {entity_id}")
                results["entities_processed"] += 1
                
        except Exception as e:
            logger.error(f"处理实体时出错 {entity_id}: {e}")
            results["entity_errors"].append({
                "entity_id": entity_id,
                "error": str(e)
            })
    
    # 2. 处理特征
    for feature_data in features:
        entity_id = feature_data.get("entity_id")
        feature_text = feature_data.get("feature")
        scene_id = feature_data.get("scene_id", "unknown")
        
        if not entity_id or not feature_text:
            logger.warning("跳过无效的特征数据")
            continue
        
        try:
            # 追加特征到实体，符合 FeatureRecord 模式
            # 构建来源信息
            source_info = {
                "dialogue_id": dialogue_id,
                "episode_id": episode_id,
                "scene_id": scene_id,
                "generated_at": feature_data.get("timestamp") or datetime.utcnow().isoformat() + "Z"
            }
            
            feature_record = {
                "feature": feature_text,
                "scene_id": scene_id,
                "confidence": feature_data.get("confidence", 1.0),
                "sources": [source_info]
            }
            
            append_result = kg_base.append_feature(
                entity_id=entity_id,
                feature_record=feature_record,
                source_info={
                    "episode_id": episode_id,
                    "dialogue_id": dialogue_id,
                    "scene_id": scene_id
                }
            )
            
            if append_result.get("success", False):
                logger.debug(f"为实体 {entity_id} 添加特征: {feature_text[:50]}...")
                results["features_processed"] += 1
            else:
                logger.warning(f"添加特征失败: {entity_id}, 结果: {append_result}")
                results["feature_errors"].append({
                    "entity_id": entity_id,
                    "feature": feature_text[:100],
                    "error": append_result.get("error", "unknown")
                })
                
        except Exception as e:
            logger.error(f"处理特征时出错 {entity_id}: {e}")
            results["feature_errors"].append({
                "entity_id": entity_id,
                "feature": feature_text[:100],
                "error": str(e)
            })
    
    # 3. 处理属性
    for attribute_data in attributes:
        entity_id = attribute_data.get("entity")
        field = attribute_data.get("field")
        value = attribute_data.get("value")
        confidence = attribute_data.get("confidence", 1.0)
        
        if not entity_id or not field or value is None:
            logger.warning("跳过无效的属性数据")
            continue
        
        try:
            # 追加属性到实体，符合 AttributeRecord 模式
            # 构建来源信息
            source_info = {
                "dialogue_id": dialogue_id,
                "episode_id": episode_id,
                "scene_id": attribute_data.get("scene_id"),
                "generated_at": attribute_data.get("timestamp") or datetime.utcnow().isoformat() + "Z"
            }
            
            attribute_record = {
                "field": field,
                "value": value,
                "confidence": confidence,
                "sources": [source_info]
            }
            
            append_result = kg_base.append_attribute(
                entity_id=entity_id,
                attribute_record=attribute_record,
                source_info={
                    "episode_id": episode_id,
                    "dialogue_id": dialogue_id,
                    "scene_id": attribute_data.get("scene_id")
                }
            )
            
            if append_result.get("success", False):
                logger.debug(f"为实体 {entity_id} 添加属性: {field}={value}")
                results["attributes_processed"] += 1
            else:
                logger.warning(f"添加属性失败: {entity_id}, 结果: {append_result}")
                results["attribute_errors"].append({
                    "entity_id": entity_id,
                    "field": field,
                    "value": str(value)[:100],
                    "error": append_result.get("error", "unknown")
                })
                
        except Exception as e:
            logger.error(f"处理属性时出错 {entity_id}: {e}")
            results["attribute_errors"].append({
                "entity_id": entity_id,
                "field": field,
                "value": str(value)[:100],
                "error": str(e)
            })
    
    # 4. 处理关系
    for relation_data in relations:
        subject = relation_data.get("subject")
        relation_type = relation_data.get("relation")
        obj = relation_data.get("object")
        confidence = relation_data.get("confidence", 1.0)
        
        if not subject or not relation_type or not obj:
            logger.warning("跳过无效的关系数据")
            continue
        
        try:
            # 构建来源信息
            source_info = {
                "episode_id": episode_id,
                "dialogue_id": dialogue_id,
                "scene_id": relation_data.get("scene_id"),
                "confidence": confidence
            }
            
            # 添加关系
            add_result = kg_base.add_relation(
                subject=subject,
                relation=relation_type,
                object=obj,
                confidence=confidence,
                source_info=source_info
            )
            
            if add_result.get("success", False):
                logger.debug(f"添加关系: {subject} -[{relation_type}]-> {obj}")
                results["relations_processed"] += 1
            else:
                logger.warning(f"添加关系失败: {subject} -[{relation_type}]-> {obj}, 结果: {add_result}")
                results["relation_errors"].append({
                    "subject": subject,
                    "relation": relation_type,
                    "object": obj,
                    "error": add_result.get("error", "unknown")
                })
                
        except Exception as e:
            logger.error(f"处理关系时出错 {subject} -[{relation_type}]-> {obj}: {e}")
            results["relation_errors"].append({
                "subject": subject,
                "relation": relation_type,
                "object": obj,
                "error": str(e)
            })
    
    # 5. 调用实体解析扫描
    logger.info("开始实体解析扫描")
    try:
        # 获取当前 KG 中的所有实体
        kg_entity_list = kg_base.list_entity_ids()
        
        if kg_entity_list:
            resolution_stats = None
            
            # 如果有 memory_core，使用其 resolve_entity 方法
            if memory_core and hasattr(memory_core, 'resolve_entity'):
                logger.info("使用 MemoryCore.resolve_entity 进行实体解析")
                resolution_stats = _align_with_memory_core(memory_core, kg_entity_list)
            else:
                # 回退到旧的 align_library_with_kg_entities 方法
                logger.info("使用 EntityResolutionService.align_library_with_kg_entities 进行实体解析")
                resolution_stats = entity_resolution_service.align_library_with_kg_entities(kg_entity_list)
            
            results["resolution_stats"] = resolution_stats
            results["resolution_applied"] = True
            logger.info(f"实体解析扫描完成: {resolution_stats}")
            
            if save == True:
                # 保存 Library 数据到文件
                try:
                    if hasattr(entity_resolution_service, 'data_path') and entity_resolution_service.data_path:
                        save_success = entity_resolution_service.entity_library.save_to_path(entity_resolution_service.data_path)
                        if save_success:
                            logger.info(f"Library 数据已保存到: {entity_resolution_service.data_path}")
                        else:
                            logger.warning(f"Library 数据保存失败: {entity_resolution_service.data_path}")
                    else:
                        logger.debug("未配置 data_path，跳过 Library 保存")
                except Exception as save_e:
                    logger.warning(f"保存 Library 数据时出错: {save_e}")
        else:
            logger.info("KG 中暂无实体，跳过实体解析扫描")
            results["resolution_applied"] = False
            
    except Exception as e:
        logger.error(f"实体解析扫描时出错: {e}")
        results["resolution_error"] = str(e)
    
    logger.info(f"单个对话加载完成: 处理了 {results['entities_processed']} 个实体, {results['features_processed']} 个特征, {results['attributes_processed']} 个属性, {results['relations_processed']} 个关系")
    # 6. 提取关系、特征
    return results


def load_from_dialogue_path(
    path: Path,
    kg_base: KGBase,
    entity_resolution_service: EntityResolutionService,
    memory_core: Optional[Any] = None,
    use_tqdm: bool = True
) -> Dict[str, Any]:
    """
    从对话数据目录加载所有文件到 KG

    接收参数:
    1. path路径（kg_candidate的路径）
    2. kg_base操作类的实例化
    3. EntityResolutionService类的实例化
    4. memory_core: MemoryCore实例（可选）

    接口功能循环一遍文件夹下的文件操作一遍上面的单个处理的功能，最后才调用EntityResolutionService类扫描KG

    Args:
        path: kg_candidate 目录路径
        kg_base: KGBase 实例
        entity_resolution_service: EntityResolutionService 实例
        memory_core: MemoryCore实例（可选）
        
    Returns:
        操作结果字典
    """
    logger.info(f"开始加载对话目录: {path}")
    
    if not path.exists():
        error_msg = f"路径不存在: {path}"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}
    
    if not path.is_dir():
        error_msg = f"路径不是目录: {path}"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}
    
    # 查找所有 JSON 文件
    json_files = list(path.glob("*.json"))
    if not json_files:
        logger.warning(f"目录中没有找到 JSON 文件: {path}")
        return {"success": True, "message": "没有找到 JSON 文件", "files_processed": 0}
    
    logger.info(f"找到 {len(json_files)} 个 JSON 文件")
    
    results = {
        "path": str(path),
        "total_files": len(json_files),
        "files_processed": 0,
        "files_failed": 0,
        "total_entities_processed": 0,
        "total_features_processed": 0,
        "total_attributes_processed": 0,
        "total_relations_processed": 0,
        "file_results": [],
        "resolution_applied": False
    }
    
    # 逐个处理文件
    j_files = tqdm(sorted(json_files)) if use_tqdm else sorted(json_files)
    for json_file in j_files:
        logger.info(f"处理文件: {json_file.name}")
        
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            
            # 调用单个文件处理函数
            file_result = load_from_dialogue_json(
                json_data=json_data,
                kg_base=kg_base,
                entity_resolution_service=entity_resolution_service,
                memory_core=memory_core,
                save=False
            )
            
            # 注意：这里不调用 EntityResolutionService 扫描，留到最后统一扫描
            # 移除 resolution_applied 标志，因为我们在最后才扫描
            if "resolution_applied" in file_result:
                file_result.pop("resolution_applied")
            
            results["file_results"].append({
                "file": json_file.name,
                "success": True,
                "result": file_result
            })
            
            results["files_processed"] += 1
            results["total_entities_processed"] += file_result.get("entities_processed", 0)
            results["total_features_processed"] += file_result.get("features_processed", 0)
            results["total_attributes_processed"] += file_result.get("attributes_processed", 0)
            results["total_relations_processed"] += file_result.get("relations_processed", 0)
            
            logger.info(f"文件处理完成: {json_file.name}")
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析错误 {json_file}: {e}")
            results["file_results"].append({
                "file": json_file.name,
                "success": False,
                "error": f"JSON 解析错误: {e}"
            })
            results["files_failed"] += 1
            
        except Exception as e:
            logger.error(f"处理文件时出错 {json_file}: {e}")
            results["file_results"].append({
                "file": json_file.name,
                "success": False,
                "error": str(e)
            })
            results["files_failed"] += 1
    
    # 所有文件处理完成后，调用实体解析扫描
    logger.info("所有文件处理完成，开始实体解析扫描")
    try:
        kg_entity_list = kg_base.list_entity_ids()
        
        if kg_entity_list:
            resolution_stats = None
            
            # 如果有 memory_core，使用其 resolve_entity 方法
            if memory_core and hasattr(memory_core, 'resolve_entity'):
                logger.info("使用 MemoryCore.resolve_entity 进行实体解析")
                resolution_stats = _align_with_memory_core(memory_core, kg_entity_list)
            else:
                # 回退到旧的 align_library_with_kg_entities 方法
                logger.info("使用 EntityResolutionService.align_library_with_kg_entities 进行实体解析")
                resolution_stats = entity_resolution_service.align_library_with_kg_entities(kg_entity_list)
            
            results["resolution_stats"] = resolution_stats
            results["resolution_applied"] = True
            logger.info(f"实体解析扫描完成: {resolution_stats}")
            
            # 保存 Library 数据到文件
            try:
                if hasattr(entity_resolution_service, 'data_path') and entity_resolution_service.data_path:
                    save_success = entity_resolution_service.entity_library.save_to_path(entity_resolution_service.data_path)
                    if save_success:
                        logger.info(f"Library 数据已保存到: {entity_resolution_service.data_path}")
                    else:
                        logger.warning(f"Library 数据保存失败: {entity_resolution_service.data_path}")
                else:
                    logger.debug("未配置 data_path，跳过 Library 保存")
            except Exception as save_e:
                logger.warning(f"保存 Library 数据时出错: {save_e}")
        else:
            logger.info("KG 中暂无实体，跳过实体解析扫描")
            results["resolution_applied"] = False
            
    except Exception as e:
        logger.error(f"实体解析扫描时出错: {e}")
        results["resolution_error"] = str(e)
    
    logger.info(f"目录加载完成: 处理了 {results['files_processed']} 个文件, "
                f"{results['total_entities_processed']} 个实体, "
                f"{results['total_features_processed']} 个特征, "
                f"{results['total_attributes_processed']} 个属性, "
                f"{results['total_relations_processed']} 个关系")
    
    results["success"] = results["files_failed"] == 0
    return results


def _align_with_memory_core(memory_core: Any, kg_entity_list: List[str]) -> Dict[str, Any]:
    """
    使用 MemoryCore 对齐实体库与 KG 实体
    
    替代 EntityResolutionService.align_library_with_kg_entities 的新实现
    
    Args:
        memory_core: MemoryCore 实例
        kg_entity_list: KG 中的实体ID列表
        
    Returns:
        对齐操作的结果统计
    """
    logger.info(f"使用 MemoryCore 对齐实体库，KG实体数量: {len(kg_entity_list)}")
    
    # 获取 EntityResolutionService 实例
    entity_resolution_service = memory_core.entity_resolution_service
    entity_library = entity_resolution_service.entity_library
    
    # 获取Library中所有实体ID
    library_entity_ids = set(entity_library.entities.keys())
    kg_entity_set = set(kg_entity_list)
    
    # 1. 找出Library中存在但KG中不存在的实体
    library_only = library_entity_ids - kg_entity_set
    removed_count = 0
    
    # 删除这些实体
    for entity_id in library_only:
        try:
            # 从Library中删除实体
            if entity_id in entity_library.entities:
                # 需要先删除名称映射
                record = entity_library.entities[entity_id]
                for name in record.get_all_names():
                    if name in entity_library.name_to_entity:
                        del entity_library.name_to_entity[name]
                
                # 删除实体记录
                del entity_library.entities[entity_id]
                
                # 删除embedding
                if entity_id in entity_library.embeddings:
                    del entity_library.embeddings[entity_id]
                
                removed_count += 1
                logger.debug(f"删除Library中存在但KG中不存在的实体: {entity_id}")
        except Exception as e:
            logger.warning(f"删除实体失败 {entity_id}: {e}")
    
    # 2. 找出KG中存在但Library中不存在的实体
    kg_only = kg_entity_set - library_entity_ids
    kg_only_list = list(kg_only)
    
    logger.info(f"对齐结果: Library中存在但KG中不存在 {len(library_only)} 个, KG中存在但Library中不存在 {len(kg_only)} 个")
    
    # 3. 对KG中存在但Library中不存在的实体进行逐个解析
    resolved_results = []
    for entity_id in kg_only_list:
        try:
            # 使用 MemoryCore.resolve_entity 进行解析（这会自动处理合并）
            result = memory_core.resolve_entity(entity_id)
            
            # 提取解析结果信息
            decision = result.get("decision")
            resolution_type = decision.resolution_type.value if decision else "UNKNOWN"
            success = True  # 假设成功，因为resolve_entity会处理错误
            
            resolved_results.append({
                "entity_id": entity_id,
                "resolution_type": resolution_type,
                "success": success,
                "error": None
            })
            
            logger.debug(f"处理KG中存在但Library中不存在的实体: {entity_id} -> {resolution_type}")
        except Exception as e:
            logger.error(f"处理实体失败 {entity_id}: {e}")
            resolved_results.append({
                "entity_id": entity_id,
                "resolution_type": "ERROR",
                "success": False,
                "error": str(e)
            })
    
    # 统计结果
    success_count = sum(1 for r in resolved_results if r["success"])
    
    result_stats = {
        "kg_entity_count": len(kg_entity_list),
        "library_entity_count_before": len(library_entity_ids),
        "library_entity_count_after": entity_library.get_entity_count(),
        "removed_from_library": removed_count,
        "new_from_kg": len(kg_only_list),
        "resolved_success": success_count,
        "resolved_failed": len(resolved_results) - success_count,
        "removed_entities": list(library_only),
        "new_entities": kg_only_list,
        "resolution_results": resolved_results,
        "method": "memory_core_resolve_entity"
    }
    
    logger.info(f"MemoryCore 对齐完成: 删除 {removed_count} 个实体, 新增 {len(kg_only_list)} 个实体, 成功解析 {success_count} 个")
    return result_stats