#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Union, Tuple

from .storage.entity_storage import EntityStorage
from .storage.relation_storage import RelationStorage
from .storage.feature_attribute_storage import FeatureAttributeStorage
from .storage.entity_library import EntityLibrary
from .source_manager import SourceManager
from .entity_operations import EntityOperations

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class KGManager:
    """知识图谱管理类（重构版）"""
    
    def __init__(self, kg_data_dir: Union[str, Path], workflow_id: str = "test3"):
        """
        初始化知识图谱管理器
        
        Args:
            kg_data_dir: KG数据目录路径，例如 "data/memory/test3/kg_data"
            workflow_id: 工作流ID，用于确定数据位置
        """
        self.kg_data_dir = Path(kg_data_dir)
        self.workflow_id = workflow_id
        
        # 初始化存储模块
        entity_dir = self.kg_data_dir / "entity"
        relation_dir = self.kg_data_dir / "relation"
        library_path = self.kg_data_dir / "entity_library.json"
        
        self.entity_storage = EntityStorage(entity_dir)
        self.relation_storage = RelationStorage(relation_dir)
        self.feature_attribute_storage = FeatureAttributeStorage(self.entity_storage)
        self.source_manager = SourceManager()
        
        # 初始化实体库
        self.entity_library = EntityLibrary(library_path)
        
        # 初始化模型（延迟加载）
        self.embed_model = None
        self.llm = None
        self.similarity_threshold = 0.7  # 相似度阈值
        
        # 初始化实体操作模块
        self.entity_operations = EntityOperations(
            entity_storage=self.entity_storage,
            relation_storage=self.relation_storage,
            feature_attribute_storage=self.feature_attribute_storage,
            source_manager=self.source_manager
        )
        
        logger.info(f"初始化KG管理器，数据目录: {self.kg_data_dir}")
        logger.info(f"实体目录: {entity_dir}")
        logger.info(f"关系目录: {relation_dir}")
        logger.info(f"实体库路径: {library_path}")
    
    def _load_models(self):
        """加载嵌入模型和LLM模型（延迟加载）"""
        try:
            if self.embed_model is None:
                from load_model.OpenAIcall import get_embed_model
                self.embed_model = get_embed_model()
                logger.info("嵌入模型加载成功")
            
            if self.llm is None:
                from load_model.OpenAIcall import get_llm
                self.llm = get_llm(model_temperature=0.1)  # 使用较低的温度以获得更确定的输出
                logger.info("LLM模型加载成功")
                
        except Exception as e:
            logger.error(f"加载模型失败: {e}")
            raise
    
    def _get_entity_embedding(self, entity_id: str) -> List[float]:
        """获取实体的嵌入向量"""
        self._load_models()
        return self.embed_model(entity_id)
    
    def _ask_llm_for_entity_match(self, new_entity: str, similar_entities: List[Tuple[str, float]]) -> Optional[str]:
        """
        使用LLM判断新实体是否与相似实体匹配
        
        Args:
            new_entity: 新实体ID
            similar_entities: 相似实体列表，每个元素为 (实体ID, 相似度)
            
        Returns:
            匹配的实体ID，如果不匹配则返回None
        """
        self._load_models()
        
        if not similar_entities:
            return None
        
        # 构建prompt
        prompt = self._build_entity_match_prompt(new_entity, similar_entities)
        
        try:
            response = self.llm(prompt)
            logger.info(f"LLM响应: {response}")
            
            # 解析LLM响应
            matched_entity = self._parse_llm_response(response, similar_entities)
            return matched_entity
            
        except Exception as e:
            logger.error(f"LLM判断失败: {e}")
            return None
    
    def _build_entity_match_prompt(self, new_entity: str, similar_entities: List[Tuple[str, float]]) -> str:
        """构建实体匹配的prompt"""
        similar_entities_str = "\n".join([
            f"- {entity_id} (相似度: {similarity:.3f})"
            for entity_id, similarity in similar_entities
        ])
        
        prompt = f"""你是一个知识图谱实体匹配专家。请判断新实体是否与现有实体匹配。

新实体: "{new_entity}"

相似的现有实体（按相似度排序）:
{similar_entities_str}

请仔细分析这些实体是否表示同一个现实世界中的事物。考虑以下因素：
1. 名称的语义相似性
2. 可能的别名、缩写或变体
3. 上下文中的含义

如果新实体与任何一个现有实体匹配，请回复该实体的ID。
如果不匹配，请回复 "NO_MATCH"。

请只回复实体ID或"NO_MATCH"，不要添加其他解释。"""
        
        return prompt
    
    def _parse_llm_response(self, response: str, similar_entities: List[Tuple[str, float]]) -> Optional[str]:
        """解析LLM响应"""
        response = response.strip()
        
        # 检查是否是NO_MATCH
        if response.upper() == "NO_MATCH":
            return None
        
        # 检查是否匹配某个实体ID
        for entity_id, _ in similar_entities:
            if entity_id in response:
                return entity_id
        
        # 尝试直接匹配
        for entity_id, _ in similar_entities:
            if response == entity_id:
                return entity_id
        
        logger.warning(f"无法解析LLM响应: {response}")
        return None
    
    def _post_process_entities(self, source_info: Dict) -> Dict:
        """
        后处理实体：检查新实体是否与实体库中的实体匹配
        
        Args:
            source_info: 来源信息
            
        Returns:
            后处理统计信息
        """
        stats = {
            "processed": 0,
            "added_to_library": 0,
            "matched_with_existing": 0,
            "merged": 0,
            "aliases_added": 0
        }
        
        try:
            # 获取所有实体文件
            entity_files = self.entity_storage.get_all_entity_files()
            logger.info(f"开始后处理实体，共 {len(entity_files)} 个实体文件")
            
            for entity_file in entity_files:
                stats["processed"] += 1
                
                # 提取实体ID（文件名去掉扩展名）
                entity_id = entity_file.stem
                
                # 检查实体是否已在实体库中（通过名称或别名匹配）
                if self.entity_library.entity_exists(entity_id):
                    logger.debug(f"实体已在实体库中: {entity_id}")
                    continue
                
                logger.info(f"处理新实体: {entity_id}")
                
                # 获取实体嵌入向量
                try:
                    embedding = self._get_entity_embedding(entity_id)
                except Exception as e:
                    logger.warning(f"获取实体嵌入失败 {entity_id}: {e}")
                    embedding = None
                
                # 查找相似实体
                similar_entities = []
                if embedding:
                    similar_entities = self.entity_library.find_similar_entities(
                        entity_id, embedding, self.similarity_threshold
                    )
                
                if not similar_entities:
                    # 没有相似实体，直接添加到实体库
                    self.entity_library.add_entity(entity_id, embedding)
                    stats["added_to_library"] += 1
                    logger.info(f"添加新实体到实体库: {entity_id}")
                else:
                    # 有相似实体，使用LLM判断是否匹配
                    logger.info(f"找到 {len(similar_entities)} 个相似实体: {[e[0] for e in similar_entities]}")
                    
                    matched_entity_id = self._ask_llm_for_entity_match(entity_id, similar_entities)
                    
                    if matched_entity_id is None:
                        # LLM判定为不匹配，添加到实体库
                        self.entity_library.add_entity(entity_id, embedding)
                        stats["added_to_library"] += 1
                        logger.info(f"LLM判定不匹配，添加新实体到实体库: {entity_id}")
                    else:
                        # LLM判定为匹配，进行实体合并
                        stats["matched_with_existing"] += 1
                        logger.info(f"LLM判定匹配: {entity_id} -> {matched_entity_id}")
                        
                        # 1. 将新实体作为别名添加到匹配的实体
                        if self.entity_library.add_alias(matched_entity_id, entity_id):
                            stats["aliases_added"] += 1
                            logger.info(f"添加别名: {entity_id} -> {matched_entity_id}")
                        
                        # 2. 合并实体数据
                        merge_result = self.combine_entity(matched_entity_id, entity_id)
                        if merge_result.get("success", False):
                            stats["merged"] += 1
                            logger.info(f"实体合并成功: {entity_id} -> {matched_entity_id}")
                        else:
                            logger.warning(f"实体合并失败: {merge_result.get('message', '未知错误')}")
            
            # 保存实体库
            if self.entity_library.save():
                logger.info("实体库保存成功")
            else:
                logger.warning("实体库保存失败")
            
            logger.info(f"后处理完成: 处理 {stats['processed']} 个实体, "
                       f"新增 {stats['added_to_library']} 个, "
                       f"匹配 {stats['matched_with_existing']} 个, "
                       f"合并 {stats['merged']} 个, "
                       f"添加别名 {stats['aliases_added']} 个")
            
        except Exception as e:
            logger.error(f"后处理实体失败: {e}")
        
        return stats
    
    def receive_kg_candidate(self, kg_candidate_json: Dict) -> Dict:
        """
        接收一个kg_candidate JSON对象，将其合并到KG中
        
        Args:
            kg_candidate_json: kg_candidate JSON对象，格式如下：
                {
                    "file_number": 1,
                    "generated_at": "2026-01-27T20:52:09.800086Z",
                    "episode_id": "ep_001",
                    "dialogue_id": "dlg_2025-10-21_22-24-25",
                    "kg_candidate": {
                        "facts": {
                            "entities": [...],
                            "features": [...]
                        }
                    },
                    "prompt_version": "v2",
                    "prompt_key": "kg_strong_filter_v2"
                }
        
        Returns:
            处理结果字典，包含成功状态和统计信息
        """
        try:
            logger.info(f"开始处理kg_candidate: {kg_candidate_json.get('file_number', 'unknown')}")
            
            # 提取基本信息
            file_number = kg_candidate_json.get('file_number')
            episode_id = kg_candidate_json.get('episode_id')
            dialogue_id = kg_candidate_json.get('dialogue_id')
            generated_at = kg_candidate_json.get('generated_at')
            
            # 构建基本来源信息
            source_info = self.source_manager.create_source_info(
                dialogue_id=dialogue_id,
                episode_id=episode_id,
                generated_at=generated_at
            )
            
            # 提取kg_candidate数据
            kg_candidate = kg_candidate_json.get('kg_candidate', {})
            facts = kg_candidate.get('facts', {})
            
            if not facts:
                logger.warning("kg_candidate中没有facts数据")
                return {
                    "success": False,
                    "message": "kg_candidate中没有facts数据",
                    "file_number": file_number
                }
            
            # 处理实体
            entities = facts.get('entities', [])
            entity_stats = self._process_entities(entities, source_info)
            
            # 处理特征
            features = facts.get('features', [])
            feature_stats = self._process_features(features, source_info)
            
            # 处理关系（如果存在）
            relations = facts.get('relations', [])
            relation_stats = self._process_relations(relations, source_info)
            
            # 处理属性（如果存在）
            attributes = facts.get('attributes', [])
            attribute_stats = self._process_attributes(attributes, source_info)
            
            # 汇总统计信息
            stats = {
                "entities": entity_stats,
                "features": feature_stats,
                "relations": relation_stats,
                "attributes": attribute_stats,
                "total_processed": (
                    entity_stats.get("processed", 0) +
                    feature_stats.get("processed", 0) +
                    relation_stats.get("processed", 0) +
                    attribute_stats.get("processed", 0)
                ),
                "total_saved": (
                    entity_stats.get("saved", 0) +
                    feature_stats.get("saved", 0) +
                    relation_stats.get("saved", 0) +
                    attribute_stats.get("saved", 0)
                )
            }
            
            logger.info(f"处理完成: 文件 {file_number}, 实体: {entity_stats.get('saved', 0)}个, "
                       f"特征: {feature_stats.get('saved', 0)}个, "
                       f"关系: {relation_stats.get('saved', 0)}个")
            
            # 后处理：实体匹配和合并
            logger.info("开始后处理实体匹配和合并")
            post_process_stats = self._post_process_entities(source_info)
            stats["post_processing"] = post_process_stats
            
            logger.info(f"后处理完成: 新增 {post_process_stats.get('added_to_library', 0)} 个实体到库, "
                       f"匹配 {post_process_stats.get('matched_with_existing', 0)} 个, "
                       f"合并 {post_process_stats.get('merged', 0)} 个实体")
            
            return {
                "success": True,
                "message": f"成功处理kg_candidate文件 {file_number}",
                "file_number": file_number,
                "stats": stats
            }
            
        except Exception as e:
            logger.error(f"处理kg_candidate失败: {e}")
            return {
                "success": False,
                "message": f"处理kg_candidate失败: {str(e)}",
                "file_number": kg_candidate_json.get('file_number', 'unknown')
            }
    
    def _process_entities(self, entities: List[Dict], source_info: Dict) -> Dict:
        """
        处理实体列表
        
        Args:
            entities: 实体数据列表
            source_info: 来源信息
            
        Returns:
            统计信息字典
        """
        stats = {
            "processed": 0,
            "saved": 0,
            "skipped": 0
        }
        
        for entity in entities:
            stats["processed"] += 1
            
            entity_id = entity.get('id')
            if not entity_id:
                logger.warning("实体数据缺少'id'字段，跳过")
                stats["skipped"] += 1
                continue
            
            # 保存实体
            if self.entity_storage.save_entity(entity, source_info):
                stats["saved"] += 1
            else:
                stats["skipped"] += 1
        
        return stats
    
    def _process_features(self, features: List[Dict], source_info: Dict) -> Dict:
        """
        处理特征列表
        
        Args:
            features: 特征数据列表
            source_info: 来源信息
            
        Returns:
            统计信息字典
        """
        stats = {
            "processed": 0,
            "saved": 0,
            "skipped": 0
        }
        
        for feature in features:
            stats["processed"] += 1
            
            entity_id = feature.get('entity_id')
            feature_text = feature.get('feature')
            
            if not entity_id or not feature_text:
                logger.warning("特征数据缺少必要字段，跳过")
                stats["skipped"] += 1
                continue
            
            # 保存特征
            if self.feature_attribute_storage.save_feature(feature, source_info):
                stats["saved"] += 1
            else:
                stats["skipped"] += 1
        
        return stats
    
    def _process_relations(self, relations: List[Dict], source_info: Dict) -> Dict:
        """
        处理关系列表
        
        Args:
            relations: 关系数据列表
            source_info: 来源信息
            
        Returns:
            统计信息字典
        """
        stats = {
            "processed": 0,
            "saved": 0,
            "skipped": 0
        }
        
        for relation in relations:
            stats["processed"] += 1
            
            subject = relation.get('subject')
            relation_type = relation.get('relation')
            obj = relation.get('object')
            
            if not subject or not relation_type or not obj:
                logger.warning("关系数据缺少必要字段，跳过")
                stats["skipped"] += 1
                continue
            
            # 保存关系
            success, _ = self.relation_storage.save_relation(relation, source_info)
            if success:
                stats["saved"] += 1
            else:
                stats["skipped"] += 1
        
        return stats
    
    def _process_attributes(self, attributes: List[Dict], source_info: Dict) -> Dict:
        """
        处理属性列表
        
        Args:
            attributes: 属性数据列表
            source_info: 来源信息
            
        Returns:
            统计信息字典
        """
        stats = {
            "processed": 0,
            "saved": 0,
            "skipped": 0
        }
        
        for attribute in attributes:
            stats["processed"] += 1
            
            entity = attribute.get('entity')
            field = attribute.get('field')
            value = attribute.get('value')
            
            if not entity or not field or value is None:
                logger.warning("属性数据缺少必要字段，跳过")
                stats["skipped"] += 1
                continue
            
            # 保存属性
            if self.feature_attribute_storage.save_attribute(attribute, source_info):
                stats["saved"] += 1
            else:
                stats["skipped"] += 1
        
        return stats
    
    def get_stats(self) -> Dict:
        """
        获取KG数据统计信息
        
        Returns:
            包含统计信息的字典
        """
        try:
            entity_count = self.entity_storage.get_entity_count()
            relation_count = self.relation_storage.get_relation_count()
            
            # 计算特征和属性总数（需要读取实体文件）
            entity_files = self.entity_storage.get_all_entity_files()
            feature_count = 0
            attribute_count = 0
            
            for entity_file in entity_files[:50]:  # 限制读取数量，避免性能问题
                entity_data = self.entity_storage.load_entity(entity_file.stem)
                if entity_data:
                    features = entity_data.get('features', [])
                    attributes = entity_data.get('attributes', [])
                    feature_count += len(features)
                    attribute_count += len(attributes)
            
            return {
                "success": True,
                "entity_count": entity_count,
                "relation_count": relation_count,
                "feature_count": feature_count,
                "attribute_count": attribute_count,
                "entity_files": [str(f.name) for f in entity_files[:10]],  # 只显示前10个
                "relation_files": [str(f.name) for f in self.relation_storage.get_all_relation_files()[:10]]  # 只显示前10个
            }
            
        except Exception as e:
            logger.error(f"获取KG统计信息失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def combine_entity(self, entity_a_id: str, entity_b_id: str) -> Dict:
        """
        合并两个实体，将实体B的属性、关系、特征添加到实体A上
        
        Args:
            entity_a_id: 目标实体ID（合并到该实体）
            entity_b_id: 源实体ID（从该实体合并数据）
            
        Returns:
            包含合并结果的字典
        """
        # 委托给EntityOperations处理
        return self.entity_operations.combine_entities(entity_a_id, entity_b_id)
    
    def process_kg_candidates_directory(self, kg_candidates_dir: Union[str, Path]) -> Dict:
        """
        批量处理kg_candidates目录中的所有文件
        
        Args:
            kg_candidates_dir: kg_candidates目录路径
            
        Returns:
            包含批量处理结果的字典
        """
        kg_candidates_dir = Path(kg_candidates_dir)
        if not kg_candidates_dir.exists():
            logger.error(f"kg_candidates目录不存在: {kg_candidates_dir}")
            return {
                "success": False,
                "message": f"kg_candidates目录不存在: {kg_candidates_dir}"
            }
        
        # 查找所有kg_candidate文件（数字命名的JSON文件）
        kg_candidate_files = []
        for file_path in kg_candidates_dir.iterdir():
            if file_path.is_file() and file_path.suffix == '.json':
                try:
                    # 检查文件名是否为数字格式（如 00001.json）
                    int(file_path.stem)
                    kg_candidate_files.append(file_path)
                except ValueError:
                    # 不是数字格式的文件，跳过
                    continue
        
        if not kg_candidate_files:
            logger.warning(f"在目录 {kg_candidates_dir} 中没有找到kg_candidate文件")
            return {
                "success": True,
                "message": "没有找到kg_candidate文件",
                "total_files": 0,
                "processed_files": 0,
                "successful_files": 0,
                "failed_files": 0,
                "results": []
            }
        
        logger.info(f"找到 {len(kg_candidate_files)} 个kg_candidate文件，开始批量处理")
        
        results = []
        successful_files = 0
        failed_files = 0
        
        for file_path in sorted(kg_candidate_files):
            try:
                # 加载JSON文件
                with open(file_path, 'r', encoding='utf-8') as f:
                    kg_candidate_json = json.load(f)
                
                # 处理kg_candidate
                result = self.receive_kg_candidate(kg_candidate_json)
                result["file_path"] = str(file_path)
                results.append(result)
                
                if result.get("success", False):
                    successful_files += 1
                    logger.info(f"处理成功: {file_path.name}")
                else:
                    failed_files += 1
                    logger.warning(f"处理失败: {file_path.name} - {result.get('message', '未知错误')}")
                    
            except Exception as e:
                logger.error(f"处理文件 {file_path} 时发生异常: {e}")
                results.append({
                    "success": False,
                    "message": f"处理文件时发生异常: {str(e)}",
                    "file_path": str(file_path)
                })
                failed_files += 1
        
        # 汇总统计
        total_stats = {
            "entities_processed": 0,
            "entities_saved": 0,
            "features_processed": 0,
            "features_saved": 0,
            "relations_processed": 0,
            "relations_saved": 0,
            "attributes_processed": 0,
            "attributes_saved": 0
        }
        
        for result in results:
            if result.get("success") and "stats" in result:
                stats = result["stats"]
                total_stats["entities_processed"] += stats.get("entities", {}).get("processed", 0)
                total_stats["entities_saved"] += stats.get("entities", {}).get("saved", 0)
                total_stats["features_processed"] += stats.get("features", {}).get("processed", 0)
                total_stats["features_saved"] += stats.get("features", {}).get("saved", 0)
                total_stats["relations_processed"] += stats.get("relations", {}).get("processed", 0)
                total_stats["relations_saved"] += stats.get("relations", {}).get("saved", 0)
                total_stats["attributes_processed"] += stats.get("attributes", {}).get("processed", 0)
                total_stats["attributes_saved"] += stats.get("attributes", {}).get("saved", 0)
        
        logger.info(f"批量处理完成: 成功 {successful_files}/{len(kg_candidate_files)} 个文件，失败 {failed_files} 个文件")
        
        return {
            "success": True,
            "message": f"批量处理完成，成功 {successful_files} 个文件，失败 {failed_files} 个文件",
            "total_files": len(kg_candidate_files),
            "processed_files": len(results),
            "successful_files": successful_files,
            "failed_files": failed_files,
            "total_stats": total_stats,
            "results": results[:10]  # 只返回前10个结果，避免响应过大
        }