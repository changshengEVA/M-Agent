#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实体解析策略集合

根据修改需求，当前应有且仅有一种策略：
1. 先匹配library里面的别名
2. 如果没有命中就匹配向量相似度，根据阈值或者topk提取出相似度高的实体ID
3. 交给LLM进行判别
4. 如果别名和LLM皆判定无命中，则为NEW_ENTITY，反之则为SAME_AS_EXISTING
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Tuple, Callable
import numpy as np

try:
    # 尝试相对导入（当作为包的一部分时）
    from .decision import (
        ResolutionDecision, ResolutionType,
        create_new_entity_decision, create_same_as_existing_decision
    )
    from .library import EntityLibrary
except ImportError:
    # 回退到直接导入（当直接运行时）
    from decision import (
        ResolutionDecision, ResolutionType,
        create_new_entity_decision, create_same_as_existing_decision
    )
    from library import EntityLibrary

logger = logging.getLogger(__name__)


class ResolutionStrategy(ABC):
    """实体解析策略抽象接口"""
    
    def __init__(self, name: str):
        """
        初始化策略
        
        Args:
            name: 策略名称
        """
        self.name = name
        self.description = ""
    
    @abstractmethod
    def resolve(
        self, 
        entity_id: str, 
        entity_library: EntityLibrary,
        context: Optional[Dict[str, Any]] = None
    ) -> ResolutionDecision:
        """
        解析实体
        
        Args:
            entity_id: 待解析的实体ID
            entity_library: 实体库索引
            context: 上下文信息（可选）
            
        Returns:
            ResolutionDecision 判定结果
        """
        pass
    
    def __str__(self) -> str:
        """字符串表示"""
        return f"{self.name}: {self.description}"


class AliasThenEmbeddingLLMStrategy(ResolutionStrategy):
    """
    别名→向量相似度→LLM判别策略
    
    策略流程：
    1. 先匹配library里面的别名
    2. 如果没有命中就匹配向量相似度，根据阈值或者topk提取出相似度高的实体ID
    3. 交给LLM进行判别
    4. 如果别名和LLM皆判定无命中，则为NEW_ENTITY，反之则为SAME_AS_EXISTING
    """
    
    def __init__(
        self,
        llm_func: Callable[[str], str],
        embed_func: Callable[[str], List[float]],
        similarity_threshold: float = 0.7,
        top_k: int = 3,
        use_threshold: bool = True
    ):
        """
        初始化策略
        
        Args:
            llm_func: LLM函数，接收prompt返回回答
            embed_func: 嵌入向量生成函数，接收文本返回嵌入向量
            similarity_threshold: 向量相似度阈值（当use_threshold=True时使用）
            top_k: 返回前K个候选（当use_threshold=False时使用）
            use_threshold: 是否使用阈值模式（True=阈值模式，False=topk模式）
        """
        super().__init__("AliasThenEmbeddingLLMStrategy")
        self.description = "别名匹配→向量相似度→LLM判别策略"
        self.llm_func = llm_func
        self.embed_func = embed_func
        self.similarity_threshold = similarity_threshold
        self.top_k = top_k
        self.use_threshold = use_threshold
        
        logger.info(f"初始化 {self.name}: threshold={similarity_threshold}, top_k={top_k}, use_threshold={use_threshold}")
    
    def _alias_match(self, entity_id: str, entity_library: EntityLibrary, exclude_unresolved=False) -> Optional[str]:
        """
        别名匹配
        
        检查entity_id是否与现有实体名称或别名匹配
        
        Returns:
            匹配到的目标实体ID，如果没有匹配则返回None
        """
        # 检查entity_id是否已存在于实体库中（作为名称或别名）
        if entity_library.name_exists(entity_id):
            # 获取对应的实体记录
            record = entity_library.get_entity_by_name(entity_id)
            if exclude_unresolved:
                if record and record.resolved:
                    return record.entity_id
            else:
                if record:
                    return record.entity_id
        
        return None
    
    def _embedding_similarity_match(
        self, 
        entity_id: str, 
        entity_library: EntityLibrary
    ) -> List[Tuple[str, float]]:
        """
        向量相似度匹配
        
        生成entity_id的embedding，与实体库中的embedding比较相似度
        
        Returns:
            相似实体列表，每个元素为 (entity_id, 相似度)
        """
        # 首先尝试从 library 中获取实体的 embedding
        query_embedding = None
        
        # 检查实体是否在 library 中
        if entity_id in entity_library.embeddings:
            query_embedding = entity_library.embeddings[entity_id]
            logger.debug(f"从 library 中获取实体 embedding: {entity_id}")
        else:
            # 如果实体不在 library 中，尝试调用 init_entity_embedding 生成并保存
            logger.info(f"实体 {entity_id} 没有 embedding，尝试初始化")
            
            # 调用 library 的 init_entity_embedding 方法
            success = entity_library.init_entity_embedding(entity_id)
            if success:
                query_embedding = entity_library.embeddings.get(entity_id)
                logger.info(f"初始化实体 embedding 成功: {entity_id}")
            else:
                logger.warning(f"初始化实体 embedding 失败: {entity_id}")
                return []
        
        if not query_embedding:
            logger.warning(f"实体 embedding 为空: {entity_id}")
            return []
        
        # 使用 library 的 search_by_embedding 方法进行搜索
        # 注意：使用 exclude_unresolved=True 防止实体解析成自己（self-match）
        candidate_entities = entity_library.search_by_embedding(
            embedding=query_embedding,
            threshold=self.similarity_threshold if self.use_threshold else 0.0,
            top_k=self.top_k,
            exclude_unresolved=True  # 只匹配已解析的实体，防止 self-match
        )
        
        return candidate_entities
    
    def _llm_judgment(
        self, 
        source_entity_id: str, 
        candidate_entities: List[Tuple[str, float]],
        entity_library: EntityLibrary
    ) -> Optional[str]:
        """
        LLM判别
        
        将候选实体交给LLM进行判别，判断source_entity_id是否与某个候选实体相同
        
        Returns:
            LLM判定的目标实体ID，如果LLM认为都不是则返回None
        """
        if not candidate_entities:
            return None
        
        # 准备候选实体信息
        candidate_info = []
        for candidate_id, similarity in candidate_entities:
            record = entity_library.get_entity(candidate_id)
            if record:
                candidate_info.append({
                    'id': candidate_id,
                    'name': record.canonical_name,
                    'similarity': similarity
                })
        
        if not candidate_info:
            return None
        
        # 构建LLM prompt
        prompt = self._build_llm_prompt(source_entity_id, candidate_info)
        
        try:
            # 调用LLM
            llm_response = self.llm_func(prompt)
            
            # 解析LLM响应
            target_entity_id = self._parse_llm_response(llm_response, candidate_info)
            
            if target_entity_id:
                logger.info(f"LLM判定: {source_entity_id} -> {target_entity_id}")
                return target_entity_id
            else:
                logger.info(f"LLM判定: {source_entity_id} 不与任何候选实体相同")
                return None
                
        except Exception as e:
            logger.error(f"LLM判别失败 {source_entity_id}: {e}")
            return None
    
    def _build_llm_prompt(self, source_entity_id: str, candidate_info: List[Dict]) -> str:
        """构建LLM prompt"""
        candidate_text = ""
        for i, candidate in enumerate(candidate_info):
            candidate_text += f"{i+1}. 实体ID: {candidate['id']}, 名称: {candidate['name']}, 相似度: {candidate['similarity']:.3f}\n"
        
        prompt = f"""请判断以下实体是否指向同一个现实对象。

需要判定的实体："{source_entity_id}"

候选实体列表（已按相似度排序）：
{candidate_text}

请根据实体名称的语义判断"{source_entity_id}"是否与某个候选实体指向同一个现实对象。
如果认为是同一个实体，请返回对应的实体ID。
如果认为不是同一个实体，请返回"NEW_ENTITY"。

请只返回实体ID或"NEW_ENTITY"，不要有其他内容。"""
        
        return prompt
    
    def _parse_llm_response(self, llm_response: str, candidate_info: List[Dict]) -> Optional[str]:
        """解析LLM响应"""
        response = llm_response.strip()
        
        # 检查是否是"NEW_ENTITY"
        if response.upper() == "NEW_ENTITY":
            return None
        
        # 检查是否是候选实体ID
        for candidate in candidate_info:
            if response == candidate['id']:
                return candidate['id']
        
        # 尝试从响应中提取实体ID
        import re
        # 查找类似实体ID的模式
        patterns = [
            r'实体ID[:：]\s*(\S+)',
            r'ID[:：]\s*(\S+)',
            r'实体[:：]\s*(\S+)',
            r'(\S+)\s*\(ID\)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, response)
            if match:
                candidate_id = match.group(1)
                for candidate in candidate_info:
                    if candidate_id == candidate['id']:
                        return candidate_id
        
        logger.warning(f"无法解析LLM响应: {response}")
        return None
    
    def resolve(
        self, 
        entity_id: str, 
        entity_library: EntityLibrary,
        context: Optional[Dict[str, Any]] = None
    ) -> ResolutionDecision:
        """
        解析实体
        
        策略流程：
        1. 先匹配library里面的别名
        2. 如果没有命中就匹配向量相似度
        3. 交给LLM进行判别
        4. 如果别名和LLM皆判定无命中，则为NEW_ENTITY，反之则为SAME_AS_EXISTING
        """
        logger.info(f"开始解析实体: {entity_id}")
        
        # 1. 别名匹配
        alias_match_target = self._alias_match(entity_id, entity_library, exclude_unresolved=True)
        if alias_match_target:
            logger.info(f"别名匹配成功: {entity_id} -> {alias_match_target}")
            return create_same_as_existing_decision(
                source_entity_id=entity_id,
                target_entity_id=alias_match_target,
                strategy_name=self.name,
                confidence=0.95,  # 别名匹配置信度较高
                evidence={
                    "match_type": "alias_match",
                    "matched_name": entity_id,
                    "target_entity": alias_match_target
                }
            )
        
        logger.info(f"别名匹配未命中: {entity_id}")
        
        # 2. 向量相似度匹配
        similar_entities = self._embedding_similarity_match(entity_id, entity_library)
        
        if not similar_entities:
            logger.info(f"向量相似度匹配未找到候选: {entity_id}")
            # 别名和向量相似度都未命中，判定为新实体
            return create_new_entity_decision(
                entity_id=entity_id,
                strategy_name=self.name,
                confidence=0.8,
                evidence={
                    "match_type": "no_candidate_found",
                    "alias_match": False,
                    "embedding_match": False,
                    "similar_entities_found": 0
                }
            )
        
        logger.info(f"向量相似度匹配找到 {len(similar_entities)} 个候选: {entity_id}")
        
        # 3. LLM判别
        llm_target = self._llm_judgment(entity_id, similar_entities, entity_library)
        
        if llm_target:
            # LLM判定为同一实体
            # 计算置信度（基于相似度和LLM判断）
            best_similarity = similar_entities[0][1] if similar_entities else 0.0
            confidence = min(0.9, 0.7 + best_similarity * 0.2)  # 基础0.7 + 相似度加成
            
            return create_same_as_existing_decision(
                source_entity_id=entity_id,
                target_entity_id=llm_target,
                strategy_name=self.name,
                confidence=confidence,
                evidence={
                    "match_type": "llm_judgment",
                    "alias_match": False,
                    "embedding_match": True,
                    "similar_entities": similar_entities,
                    "llm_target": llm_target,
                    "best_similarity": best_similarity
                }
            )
        else:
            # LLM判定不是同一实体，判定为新实体
            return create_new_entity_decision(
                entity_id=entity_id,
                strategy_name=self.name,
                confidence=0.85,  # LLM判定为新实体，置信度较高
                evidence={
                    "match_type": "llm_judgment_new",
                    "alias_match": False,
                    "embedding_match": True,
                    "similar_entities": similar_entities,
                    "llm_judgment": "NEW_ENTITY"
                }
            )
