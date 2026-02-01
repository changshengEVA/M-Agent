#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实体解析服务主入口

职责：
- 启动阶段：从 KG 重建 EntityLibrary（派生索引）
- 运行阶段：接收新 entity_id，调用解析策略进行判定
- 根据判定结果：更新 EntityLibrary，在必要时调用 kg_core 进行实体合并

设计原则：
- 判定（resolve）与执行（apply）分离
- 不包含具体判定策略逻辑
- 不直接操作 KG 存储
"""

import logging
import time
from typing import Dict, Any, Optional, List, Callable, TYPE_CHECKING
from dataclasses import dataclass, field

try:
    # 尝试相对导入（当作为包的一部分时）
    from .decision import ResolutionDecision, ResolutionType
    from .library import EntityLibrary
    from .strategies import ResolutionStrategy, AliasThenEmbeddingLLMStrategy
except ImportError:
    # 回退到直接导入（当直接运行时）
    from decision import ResolutionDecision, ResolutionType
    from library import EntityLibrary
    from strategies import ResolutionStrategy, AliasThenEmbeddingLLMStrategy

# 类型检查时导入KGBase，避免循环导入
if TYPE_CHECKING:
    from memory.memory_core.core.kg_base import KGBase
else:
    # 运行时使用字符串类型提示
    KGBase = "KGBase"  # type: ignore

logger = logging.getLogger(__name__)


@dataclass
class ResolutionResult:
    """实体解析完整结果"""
    decision: ResolutionDecision  # 解析判定
    applied: bool = False  # 是否已应用判定结果
    library_updated: bool = False  # EntityLibrary 是否已更新
    kg_operation_performed: bool = False  # 是否执行了 KG 操作
    kg_operation_result: Optional[Dict[str, Any]] = None  # KG 操作结果
    error: Optional[str] = None  # 错误信息
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "decision": self.decision.to_dict(),
            "applied": self.applied,
            "library_updated": self.library_updated,
            "kg_operation_performed": self.kg_operation_performed,
            "kg_operation_result": self.kg_operation_result,
            "error": self.error,
            "success": self.error is None
        }


class EntityResolutionService:
    """实体解析服务"""
    
    def __init__(
        self,
        llm_func: Callable[[str], str],
        embed_func: Callable[[str], List[float]],
        kg_base: KGBase,
        similarity_threshold: float = 0.7,
        top_k: int = 3,
        use_threshold: bool = True,
        data_path: Optional[str] = None
    ):
        """
        初始化实体解析服务
        
        Args:
            llm_func: LLM函数，接收prompt返回回答
            embed_func: 嵌入向量生成函数，接收文本返回嵌入向量
            kg_base: KGBase实例，用于获取KG数据和执行合并操作（必需参数）
            similarity_threshold: 向量相似度阈值
            top_k: 返回前K个候选
            use_threshold: 是否使用阈值模式
            data_path: 实体库数据文件路径，如果提供则从该路径加载数据
        """
        if kg_base is None:
            raise ValueError("kg_base 参数是必需的，不能为 None")
        
        # 初始化实体库，传入embed_func和data_path
        self.entity_library = EntityLibrary(embed_func=embed_func, data_path=data_path)
        self.strategies: List[ResolutionStrategy] = []
        self.llm_func = llm_func
        self.embed_func = embed_func
        self.kg_base = kg_base
        self.data_path = data_path
        
        # 使用KGBase实例创建数据提供函数和合并回调
        self.kg_data_provider = self._create_kg_data_provider_from_kg_base(kg_base)
        self.kg_merge_callback = self._create_kg_merge_callback_from_kg_base(kg_base)
        logger.info(f"使用 KGBase 实例: {kg_base}")
        
        # 初始化默认策略（单一策略）
        self._init_default_strategy(
            similarity_threshold=similarity_threshold,
            top_k=top_k,
            use_threshold=use_threshold
        )
        
        logger.info("初始化 EntityResolutionService")
    
    def _create_kg_data_provider_from_kg_base(self, kg_base: KGBase) -> Callable[[], Dict[str, Any]]:
        """
        从 KGBase 实例创建数据提供函数
        
        Args:
            kg_base: KGBase 实例
            
        Returns:
            数据提供函数
        """
        def kg_data_provider() -> Dict[str, Any]:
            try:
                # 获取所有实体ID
                entity_ids = kg_base.list_entity_ids()
                
                # 构建实体数据列表
                entities = []
                for entity_id in entity_ids:
                    # 尝试获取实体信息
                    try:
                        # 这里可以扩展为获取更多实体信息
                        entities.append({
                            "id": entity_id,
                            "name": entity_id,  # 默认使用ID作为名称
                            "type": None,  # 可以扩展为获取实体类型
                            "metadata": {}
                        })
                    except Exception as e:
                        logger.warning(f"获取实体信息失败 {entity_id}: {e}")
                        # 即使失败也添加基本ID信息
                        entities.append({
                            "id": entity_id,
                            "name": entity_id,
                            "type": None,
                            "metadata": {"error": str(e)}
                        })
                
                return {"entities": entities}
            except Exception as e:
                logger.error(f"从 KGBase 获取数据失败: {e}")
                return {"entities": []}
        
        return kg_data_provider
    
    def _create_kg_merge_callback_from_kg_base(self, kg_base: KGBase) -> Callable[[str, str], Dict[str, Any]]:
        """
        从 KGBase 实例创建合并回调函数
        
        Args:
            kg_base: KGBase 实例
            
        Returns:
            合并回调函数
        """
        def kg_merge_callback(source_id: str, target_id: str) -> Dict[str, Any]:
            try:
                # 调用 KGBase 的 merge_entities 方法
                result = kg_base.merge_entities(target_id=target_id, source_id=source_id)
                return result
            except Exception as e:
                logger.error(f"调用 KGBase.merge_entities 失败 {source_id} -> {target_id}: {e}")
                return {"success": False, "error": str(e)}
        
        return kg_merge_callback
    
    def _init_default_strategy(
        self,
        similarity_threshold: float = 0.7,
        top_k: int = 3,
        use_threshold: bool = True
    ) -> None:
        """初始化默认策略（单一策略）"""
        # 创建单一策略：别名→向量相似度→LLM判别
        # 使用与文件顶部相同的导入模式
        try:
            from .strategies import AliasThenEmbeddingLLMStrategy
        except ImportError:
            from strategies import AliasThenEmbeddingLLMStrategy
        
        strategy = AliasThenEmbeddingLLMStrategy(
            llm_func=self.llm_func,
            embed_func=self.embed_func,
            similarity_threshold=similarity_threshold,
            top_k=top_k,
            use_threshold=use_threshold
        )
        
        self.strategies = [strategy]
        logger.info(f"初始化默认策略: {strategy.name}")
    
    def add_strategy(self, strategy: ResolutionStrategy) -> None:
        """添加解析策略"""
        self.strategies.append(strategy)
        logger.info(f"添加解析策略: {strategy.name}")
    
    def set_strategies(self, strategies: List[ResolutionStrategy]) -> None:
        """设置解析策略列表（替换现有策略）"""
        self.strategies = strategies
        logger.info(f"设置解析策略: {[s.name for s in self.strategies]}")
    
    
    def resolve_entity(
        self, 
        entity_id: str, 
        context: Optional[Dict[str, Any]] = None
    ) -> ResolutionDecision:
        """
        解析实体
        
        Args:
            entity_id: 待解析的实体ID
            context: 上下文信息（如嵌入向量等）
            
        Returns:
            ResolutionDecision 判定结果
        """
        logger.info(f"开始解析实体: {entity_id}")
        
        if not self.strategies:
            logger.warning("未配置解析策略，返回新建实体")
            return ResolutionDecision(
                resolution_type=ResolutionType.NEW_ENTITY,
                source_entity_id=entity_id,
                strategy_name="NoStrategy",
                confidence=0.0,
                evidence={"error": "no_strategies_configured"},
                timestamp=time.time()
            )
        
        # 使用第一个策略进行解析（通常是组合策略）
        strategy = self.strategies[0]
        
        try:
            decision = strategy.resolve(entity_id, self.entity_library, context)
            decision.timestamp = time.time()  # 设置时间戳
            
            logger.info(f"实体解析完成: {entity_id} -> {decision.resolution_type.value}")
            if decision.is_same_as_existing():
                logger.info(f"  目标实体: {decision.target_entity_id}, 置信度: {decision.confidence:.2f}")
            
            return decision
            
        except Exception as e:
            logger.error(f"解析实体失败 {entity_id}: {e}")
            return ResolutionDecision(
                resolution_type=ResolutionType.NEW_ENTITY,
                source_entity_id=entity_id,
                strategy_name=strategy.name,
                confidence=0.0,
                evidence={"error": str(e)},
                timestamp=time.time()
            )
    
    def apply_decision(self, decision: ResolutionDecision) -> ResolutionResult:
        """
        应用解析判定结果
        
        根据判定结果更新 EntityLibrary，并在必要时调用 KG 操作
        
        Args:
            decision: 解析判定结果
            
        Returns:
            ResolutionResult 应用结果
        """
        logger.info(f"开始应用解析判定: {decision}")
        
        result = ResolutionResult(decision=decision)
        
        try:
            if decision.is_new_entity():
                # 新建实体
                logger.info(f"新建实体: {decision.source_entity_id}")
                
                # 1. 更新 EntityLibrary（不修改KG_data本体）
                success = self.entity_library.add_entity(
                    entity_id=decision.source_entity_id,
                    canonical_name=decision.source_entity_id,
                    metadata={
                        "resolution_strategy": decision.strategy_name,
                        "resolution_confidence": decision.confidence,
                        "resolution_timestamp": decision.timestamp
                    }
                )
                
                if success:
                    result.library_updated = True
                    logger.info(f"EntityLibrary 更新成功: {decision.source_entity_id}")
                    
                    # 2. 为新实体生成embedding
                    if self.embed_func:
                        try:
                            embedding = self.embed_func(decision.source_entity_id)
                            if embedding:
                                # 更新实体的embedding
                                record = self.entity_library.get_entity(decision.source_entity_id)
                                if record:
                                    record.embedding = embedding
                                    self.entity_library.embeddings[decision.source_entity_id] = embedding
                                    logger.info(f"新实体embedding生成成功: {decision.source_entity_id}")
                        except Exception as e:
                            logger.warning(f"为新实体生成embedding失败: {e}")
                else:
                    logger.warning(f"EntityLibrary 更新失败: {decision.source_entity_id}")
                
                result.applied = True
                return result
            
            elif decision.is_same_as_existing() and decision.target_entity_id:
                # 等价实体
                logger.info(f"等价实体: {decision.source_entity_id} -> {decision.target_entity_id}")
                
                # 1. 更新 EntityLibrary（添加别名）
                success = self.entity_library.add_alias(
                    entity_id=decision.target_entity_id,
                    alias=decision.source_entity_id
                )
                
                if success:
                    result.library_updated = True
                    logger.info(f"EntityLibrary 添加别名成功: {decision.source_entity_id} -> {decision.target_entity_id}")
                else:
                    logger.warning(f"EntityLibrary 添加别名失败: {decision.source_entity_id} -> {decision.target_entity_id}")
                
                # 2. 调用 KG 合并回调（如果提供）
                if self.kg_merge_callback:
                    try:
                        kg_result = self.kg_merge_callback(
                            decision.source_entity_id, 
                            decision.target_entity_id
                        )
                        result.kg_operation_performed = True
                        result.kg_operation_result = kg_result
                        
                        if kg_result.get("success", False):
                            logger.info(f"KG 合并实体成功: {decision.source_entity_id} -> {decision.target_entity_id}")
                        else:
                            logger.warning(f"KG 合并实体失败: {decision.source_entity_id} -> {decision.target_entity_id}, 结果: {kg_result}")
                    except Exception as e:
                        logger.error(f"调用 KG 合并回调失败: {e}")
                        result.error = f"kg_callback_error: {e}"
                
                result.applied = True
                return result
            
            else:
                # 无效的判定结果
                error_msg = f"无效的判定结果: {decision}"
                logger.error(error_msg)
                result.error = error_msg
                return result
            
        except Exception as e:
            error_msg = f"应用解析判定失败: {e}"
            logger.error(error_msg)
            result.error = error_msg
            return result
    
    def resolve_and_apply(
        self, 
        entity_id: str, 
        context: Optional[Dict[str, Any]] = None
    ) -> ResolutionResult:
        """
        解析并应用实体
        
        组合 resolve_entity 和 apply_decision 的便捷方法
        
        Args:
            entity_id: 待解析的实体ID
            context: 上下文信息
            
        Returns:
            ResolutionResult 完整结果
        """
        # 1. 解析实体
        decision = self.resolve_entity(entity_id, context)
        
        # 2. 应用判定结果
        result = self.apply_decision(decision)
        
        return result
    
    def batch_resolve_and_apply(
        self, 
        entity_ids: List[str], 
        contexts: Optional[List[Dict[str, Any]]] = None
    ) -> List[ResolutionResult]:
        """
        批量解析并应用实体
        
        Args:
            entity_ids: 实体ID列表
            contexts: 上下文信息列表（可选，与 entity_ids 一一对应）
            
        Returns:
            ResolutionResult 结果列表
        """
        results = []
        
        for i, entity_id in enumerate(entity_ids):
            context = contexts[i] if contexts and i < len(contexts) else None
            
            try:
                result = self.resolve_and_apply(entity_id, context)
                results.append(result)
                
                logger.info(f"批量处理进度: {i+1}/{len(entity_ids)} - {entity_id} -> {result.decision.resolution_type.value}")
                
            except Exception as e:
                logger.error(f"批量处理实体失败 {entity_id}: {e}")
                error_result = ResolutionResult(
                    decision=ResolutionDecision(
                        resolution_type=ResolutionType.NEW_ENTITY,
                        source_entity_id=entity_id,
                        strategy_name="BatchError",
                        confidence=0.0,
                        evidence={"error": str(e)},
                        timestamp=time.time()
                    ),
                    error=str(e)
                )
                results.append(error_result)
        
        return results
    
    def get_library_stats(self) -> Dict[str, Any]:
        """获取实体库统计信息"""
        return self.entity_library.get_stats()
    
    def clear_library(self) -> None:
        """清空实体库"""
        self.entity_library.clear()
        logger.info("清空实体库")
    
    def reload_from_kg(self) -> bool:
        """
        重新从 KG 加载实体库
        
        注意：此方法已弃用，请使用 align_library_with_kg_entities 进行增量同步
        或 clear_library() 后从 data_path 重新加载
        """
        logger.warning("reload_from_kg 方法已弃用，请使用 align_library_with_kg_entities 进行增量同步")
        return False
    
    def align_library_with_kg_entities(self, kg_entity_list: List[str]) -> Dict[str, Any]:
        """
        对齐Library数据与KG实体数据列表
        
        执行以下操作：
        1. 删掉Library中存在的但是KG的entity_list中不存在的
        2. 标记搜罗entity_list中存在的但是Library中不存在的为一个列表
        3. 对这个列表依次进行逐个的resolve_and_apply操作
        
        Args:
            kg_entity_list: KG中的实体ID列表
            
        Returns:
            对齐操作的结果统计
        """
        logger.info(f"开始对齐Library与KG实体列表，KG实体数量: {len(kg_entity_list)}")
        
        # 获取Library中所有实体ID
        library_entity_ids = set(self.entity_library.entities.keys())
        kg_entity_set = set(kg_entity_list)
        
        # 1. 找出Library中存在但KG中不存在的实体
        library_only = library_entity_ids - kg_entity_set
        removed_count = 0
        
        # 删除这些实体
        for entity_id in library_only:
            try:
                # 从Library中删除实体
                if entity_id in self.entity_library.entities:
                    # 需要先删除名称映射
                    record = self.entity_library.entities[entity_id]
                    for name in record.get_all_names():
                        if name in self.entity_library.name_to_entity:
                            del self.entity_library.name_to_entity[name]
                    
                    # 删除实体记录
                    del self.entity_library.entities[entity_id]
                    
                    # 删除embedding
                    if entity_id in self.entity_library.embeddings:
                        del self.entity_library.embeddings[entity_id]
                    
                    removed_count += 1
                    logger.debug(f"删除Library中存在但KG中不存在的实体: {entity_id}")
            except Exception as e:
                logger.warning(f"删除实体失败 {entity_id}: {e}")
        
        # 2. 找出KG中存在但Library中不存在的实体
        kg_only = kg_entity_set - library_entity_ids
        kg_only_list = list(kg_only)
        
        logger.info(f"对齐结果: Library中存在但KG中不存在 {len(library_only)} 个, KG中存在但Library中不存在 {len(kg_only)} 个")
        
        # 3. 对KG中存在但Library中不存在的实体进行逐个解析和应用
        resolved_results = []
        for entity_id in kg_only_list:
            try:
                result = self.resolve_and_apply(entity_id)
                resolved_results.append({
                    "entity_id": entity_id,
                    "resolution_type": result.decision.resolution_type.value,
                    "success": result.error is None,
                    "error": result.error
                })
                logger.debug(f"处理KG中存在但Library中不存在的实体: {entity_id} -> {result.decision.resolution_type.value}")
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
            "library_entity_count_after": self.entity_library.get_entity_count(),
            "removed_from_library": removed_count,
            "new_from_kg": len(kg_only_list),
            "resolved_success": success_count,
            "resolved_failed": len(resolved_results) - success_count,
            "removed_entities": list(library_only),
            "new_entities": kg_only_list,
            "resolution_results": resolved_results
        }
        
        logger.info(f"对齐完成: 删除 {removed_count} 个实体, 新增 {len(kg_only_list)} 个实体, 成功解析 {success_count} 个")
        return result_stats
    
    def __str__(self) -> str:
        """字符串表示"""
        stats = self.get_library_stats()
        strategy_names = [s.name for s in self.strategies]
        return f"EntityResolutionService(entities={stats['entity_count']}, strategies={strategy_names})"


# 便捷函数
def create_default_resolution_service(
    llm_func: Callable[[str], str],
    embed_func: Callable[[str], List[float]],
    kg_base: KGBase,
    similarity_threshold: float = 0.7,
    top_k: int = 3,
    use_threshold: bool = True,
    data_path: Optional[str] = None
) -> EntityResolutionService:
    """
    创建默认配置的实体解析服务
    
    Args:
        llm_func: LLM函数，接收prompt返回回答
        embed_func: 嵌入向量生成函数，接收文本返回嵌入向量
        kg_base: KGBase实例，用于获取KG数据和执行合并操作（必需参数）
        similarity_threshold: 向量相似度阈值
        top_k: 返回前K个候选
        use_threshold: 是否使用阈值模式
        data_path: 实体库数据文件路径，如果提供则从该路径加载数据
        
    Returns:
        EntityResolutionService 实例
    """
    if kg_base is None:
        raise ValueError("kg_base 参数是必需的，不能为 None")
    
    service = EntityResolutionService(
        llm_func=llm_func,
        embed_func=embed_func,
        kg_base=kg_base,
        similarity_threshold=similarity_threshold,
        top_k=top_k,
        use_threshold=use_threshold,
        data_path=data_path
    )
    
    # 注意：不再自动从 KG 初始化实体库
    # 如果需要从 KG 同步实体，请使用 service.align_library_with_kg_entities(kg_entity_list)
    # 其中 kg_entity_list 可以通过 kg_base.list_entity_ids() 获取
    
    return service