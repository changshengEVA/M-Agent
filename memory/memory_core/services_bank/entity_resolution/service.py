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
    from .library import EntityLibrary, EntityRecord
    from .strategies import ResolutionStrategy, AliasThenEmbeddingLLMStrategy
except ImportError:
    # 回退到直接导入（当直接运行时）
    from decision import ResolutionDecision, ResolutionType
    from library import EntityLibrary, EntityRecord
    from strategies import ResolutionStrategy, AliasThenEmbeddingLLMStrategy

# 导入事件总线相关模块
try:
    from memory.memory_core.services_bank.base_service import BaseService
    from memory.memory_core.system.event_types import EventType
except ImportError:
    # 回退到相对导入
    import sys
    sys.path.append("..")
    from base_service import BaseService
    from system.event_types import EventType

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


class EntityResolutionService(BaseService):
    """实体解析服务"""
    
    def __init__(
        self,
        llm_func: Callable[[str], str],
        embed_func: Callable[[str], List[float]],
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
            similarity_threshold: 向量相似度阈值
            top_k: 返回前K个候选
            use_threshold: 是否使用阈值模式
            data_path: 实体库数据文件路径，如果提供则从该路径加载数据
        """
        # 初始化实体库，传入embed_func和data_path
        self.entity_library = EntityLibrary(embed_func=embed_func, data_path=data_path)
        self.strategies: List[ResolutionStrategy] = []
        self.llm_func = llm_func
        self.embed_func = embed_func
        self.data_path = data_path
        
        # 初始化默认策略（单一策略）
        self._init_default_strategy(
            similarity_threshold=similarity_threshold,
            top_k=top_k,
            use_threshold=use_threshold
        )
        
        logger.info("初始化 EntityResolutionService")
    
    def get_subscribed_events(self):
        """
        返回监听的 EventType 列表
        
        实体解析服务需要监听实体添加、合并和重命名事件，以便更新 EntityLibrary。
        """
        return [
            EventType.ENTITY_ADDED,
            EventType.ENTITY_MERGED,
            EventType.ENTITY_RENAMED,
        ]
    
    def handle_event(self, event_type: str, payload: dict) -> None:
        """
        处理事件
        
        Args:
            event_type: 事件类型字符串
            payload: 事件负载字典
        """
        self._log_event_handling(event_type, payload)
        
        if event_type == EventType.ENTITY_MERGED:
            source_id = payload.get("source_id")
            target_id = payload.get("target_id")
            if source_id and target_id:
                self.on_entity_merged(source_id, target_id)
        elif event_type == EventType.ENTITY_ADDED:
            entity_id = payload.get("entity_id")
            if entity_id:
                self.on_entity_added(entity_id)
        elif event_type == EventType.ENTITY_RENAMED:
            old_id = payload.get("old_id")
            new_id = payload.get("new_id")
            if old_id and new_id:
                self.on_entity_renamed(old_id, new_id)
        else:
            logger.debug(f"EntityResolutionService 忽略未处理事件: {event_type}")
    
    def on_entity_merged(self, source_id: str, target_id: str, **kwargs) -> None:
        """
        监听实体合并事件
        
        当 MemoryCore 执行实体合并时调用此方法，用于更新 EntityLibrary
        
        Args:
            source_id: 源实体ID（将被合并）
            target_id: 目标实体ID（保留）
            **kwargs: 其他参数（如合并结果等）
        """
        logger.info(f"收到实体合并事件: {source_id} -> {target_id}")
        
        try:
            # 检查目标实体是否在 EntityLibrary 中
            if target_id not in self.entity_library.entities:
                # 如果目标实体不在 Library 中，先添加它
                logger.info(f"目标实体 {target_id} 不在 EntityLibrary 中，先添加")
                add_success = self.entity_library.add_entity(
                    entity_id=target_id,
                    canonical_name=target_id,
                    metadata={
                        "added_via": "entity_merge_event",
                        "source_entity": source_id,
                        "timestamp": time.time()
                    }
                )
                
                if not add_success:
                    logger.warning(f"无法添加目标实体到 EntityLibrary: {target_id}")
                    return
            
            # 更新 EntityLibrary：将源实体ID添加为目标实体的别名
            success = self.entity_library.add_alias(
                entity_id=target_id,
                alias=source_id
            )
            
            if success:
                logger.info(f"EntityLibrary 更新成功: {source_id} 作为 {target_id} 的别名")
            else:
                # 如果添加别名失败，可能是别名已存在或其他原因
                # 检查源实体是否已经在 Library 中
                if source_id in self.entity_library.entities:
                    # 如果源实体在 Library 中，可能需要更新其记录
                    logger.info(f"源实体 {source_id} 已在 EntityLibrary 中，可能需要特殊处理")
                
                logger.warning(f"EntityLibrary 更新失败: {source_id} -> {target_id}")
                
        except Exception as e:
            logger.error(f"处理实体合并事件时出错: {e}")
    
    def on_entity_added(self, entity_id: str) -> None:
        """
        监听实体添加事件
        
        当 KG 中添加新实体时，仅同步状态到 EntityLibrary，不触发解析。
        
        Args:
            entity_id: 新添加的实体ID
        """
        logger.info(f"收到实体添加事件，同步到 EntityLibrary: {entity_id}")
        
        try:
            # 检查实体是否已在 EntityLibrary 中
            if not self.entity_library.entity_exists(entity_id):
                # 添加实体到 EntityLibrary
                success = self.entity_library.add_entity(
                    entity_id=entity_id,
                    canonical_name=entity_id,
                    metadata={
                        "added_via": "entity_added_event",
                        "timestamp": time.time()
                    }
                )
                
                if success:
                    # 标记实体为未解析状态
                    record = self.entity_library.get_entity(entity_id)
                    if record:
                        record.mark_as_unresolved()
                        logger.info(f"实体已添加到 EntityLibrary 并标记为未解析: {entity_id}")
                    else:
                        logger.warning(f"无法获取新添加的实体记录: {entity_id}")
                else:
                    logger.warning(f"无法添加实体到 EntityLibrary: {entity_id}")
            else:
                # 实体已存在，确保标记为未解析状态
                record = self.entity_library.get_entity(entity_id)
                if record:
                    record.mark_as_unresolved()
                    logger.info(f"实体已存在，标记为未解析: {entity_id}")
                else:
                    logger.warning(f"实体存在但无法获取记录: {entity_id}")
                
        except Exception as e:
            logger.error(f"处理实体添加事件时出错 {entity_id}: {e}")
    
    def on_entity_renamed(self, old_id: str, new_id: str) -> None:
        """
        监听实体重命名事件
        
        当 KG 中实体重命名时，更新 EntityLibrary 以保持同步。
        
        Args:
            old_id: 原实体 ID
            new_id: 新实体 ID
        """
        logger.info(f"收到实体重命名事件: {old_id} -> {new_id}")
        
        # 检查原实体是否在 EntityLibrary 中
        if old_id not in self.entity_library.entities:
            logger.debug(f"原实体 {old_id} 不在 EntityLibrary 中，跳过")
            return
        
        try:
            # 获取原实体记录
            old_record = self.entity_library.entities[old_id]
            
            # 创建新实体记录，继承原记录的所有属性
            new_record = EntityRecord(
                entity_id=new_id,
                canonical_name=new_id,  # 使用新ID作为规范化名称
                aliases=old_record.aliases.copy(),
                embedding=old_record.embedding,
                entity_type=old_record.entity_type,
                metadata=old_record.metadata.copy(),
                resolved=old_record.resolved,
                last_decision=old_record.last_decision
            )
            
            # 将 old_id 添加为新实体的别名
            new_record.aliases.append(old_id)
            
            # 从索引中移除原实体
            del self.entity_library.entities[old_id]
            
            # 从名称映射中移除原实体的所有名称
            for name in old_record.get_all_names():
                if name in self.entity_library.name_to_entity and self.entity_library.name_to_entity[name] == old_id:
                    del self.entity_library.name_to_entity[name]
            
            # 从嵌入向量映射中移除原实体
            if old_id in self.entity_library.embeddings:
                del self.entity_library.embeddings[old_id]
            
            # 添加新实体到索引
            self.entity_library.entities[new_id] = new_record
            
            # 建立新实体的名称映射
            for name in new_record.get_all_names():
                self.entity_library.name_to_entity[name] = new_id
            
            # 添加嵌入向量
            if new_record.embedding:
                self.entity_library.embeddings[new_id] = new_record.embedding
            
            logger.info(f"EntityLibrary 更新成功: {old_id} -> {new_id}")
            
        except Exception as e:
            logger.error(f"处理实体重命名事件失败 {old_id} -> {new_id}: {e}")
    
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
    
    def get_library_stats(self) -> Dict[str, Any]:
        """获取实体库统计信息"""
        return self.entity_library.get_stats()
    
    def clear_library(self) -> None:
        """清空实体库"""
        self.entity_library.clear()
        logger.info("清空实体库")
    
    def save_library(self) -> bool:
        """
        保存实体库数据到文件
        
        Returns:
            是否成功保存
        """
        if not self.data_path:
            logger.warning("未配置 data_path，无法保存实体库数据")
            return False
        
        try:
            success = self.entity_library.save_to_path(self.data_path)
            if success:
                logger.info(f"实体库数据保存成功: {self.data_path}")
            else:
                logger.warning(f"实体库数据保存失败: {self.data_path}")
            return success
        except Exception as e:
            logger.error(f"保存实体库数据时出错: {e}")
            return False
    
    def resolve_unresolved_entities(self) -> List[ResolutionDecision]:
        """
        批量解析未解析的实体
        
        遍历 EntityLibrary 中所有实体，找到 resolved == False 的实体，
        调用策略进行解析，保存解析结果，并标记为已解析。
        
        返回解析建议集合（proposal），不执行任何 KG 修改。
        
        Returns:
            List[ResolutionDecision] 解析建议列表
        """
        logger.info("开始批量解析未解析的实体")
        
        decisions = []
        
        if not self.strategies:
            logger.warning("未配置解析策略，无法解析")
            return decisions
        
        # 使用第一个策略进行解析
        strategy = self.strategies[0]
        
        # 首先统计未解析实体的总数
        unresolved_entities = []
        for entity_id, record in self.entity_library.entities.items():
            if not record.resolved:
                unresolved_entities.append((entity_id, record))
        
        total_unresolved = len(unresolved_entities)
        logger.info(f"发现 {total_unresolved} 个未解析实体需要处理")
        
        if total_unresolved == 0:
            logger.info("没有未解析的实体需要处理")
            return decisions
        
        # 使用 tqdm 显示进度条
        try:
            from tqdm import tqdm
            
            # 配置 tqdm 以确保进度条能正确显示
            # 使用 ascii 进度条确保在不同终端中都能显示
            # 设置 mininterval 以减少刷新频率，避免与日志冲突
            tqdm_kwargs = {
                "desc": "解析实体",
                "unit": "实体",
                "total": total_unresolved,
                "ascii": True,  # 使用 ASCII 字符确保兼容性
                "mininterval": 0.5,  # 最小刷新间隔
                "bar_format": "{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"
            }
            
            # 创建进度条迭代器
            progress_iterator = tqdm(unresolved_entities, **tqdm_kwargs)
            
        except ImportError:
            logger.warning("tqdm 未安装，使用简单进度显示")
            # 回退到简单枚举
            progress_iterator = unresolved_entities
        
        # 使用进度条迭代器遍历未解析实体
        for entity_id, record in progress_iterator:
            try:
                # 更新进度条描述，显示当前处理的实体
                if hasattr(progress_iterator, "set_description"):
                    progress_iterator.set_description(f"解析: {entity_id[:20]}...")
                
                # 调用策略进行解析
                decision = strategy.resolve(entity_id, self.entity_library, context=None)
                decision.timestamp = time.time()
                decision.source_entity_id = entity_id
                
                # 保存 decision 到 last_decision
                record.last_decision = decision.to_dict()
                
                # 标记为已解析
                record.mark_as_resolved(record.last_decision)
                
                # 添加到返回列表
                decisions.append(decision)
                
                # 更新进度条后描述
                if hasattr(progress_iterator, "set_postfix"):
                    result_type = decision.resolution_type.value[:10]
                    progress_iterator.set_postfix(result=result_type, conf=f"{decision.confidence:.2f}")
                
            except Exception as e:
                logger.error(f"解析实体失败 {entity_id}: {e}")
                # 创建一个错误决策
                error_decision = ResolutionDecision(
                    resolution_type=ResolutionType.NEW_ENTITY,
                    source_entity_id=entity_id,
                    strategy_name=strategy.name,
                    confidence=0.0,
                    evidence={"error": str(e)},
                    timestamp=time.time()
                )
                decisions.append(error_decision)
                
                # 更新进度条显示错误
                if hasattr(progress_iterator, "set_postfix"):
                    progress_iterator.set_postfix(error="失败")
        
        logger.info(f"批量解析完成，共解析 {len(decisions)} 个实体")
        
        # 解析完成后自动存档
        if decisions and self.data_path:
            save_success = self.save_library()
            if save_success:
                logger.info(f"解析完成后自动存档成功: {self.data_path}")
            else:
                logger.warning(f"解析完成后自动存档失败: {self.data_path}")
        
        return decisions
    
    def __str__(self) -> str:
        """字符串表示"""
        stats = self.get_library_stats()
        strategy_names = [s.name for s in self.strategies]
        
        # 统计未解析实体数量
        unresolved_count = sum(1 for record in self.entity_library.entities.values() if not record.resolved)
        
        return f"EntityResolutionService(entities={stats['entity_count']}, unresolved={unresolved_count}, strategies={strategy_names})"


# 便捷函数
def create_default_resolution_service(
    llm_func: Callable[[str], str],
    embed_func: Callable[[str], List[float]],
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
        similarity_threshold: 向量相似度阈值
        top_k: 返回前K个候选
        use_threshold: 是否使用阈值模式
        data_path: 实体库数据文件路径，如果提供则从该路径加载数据
        
    Returns:
        EntityResolutionService 实例
    """
    service = EntityResolutionService(
        llm_func=llm_func,
        embed_func=embed_func,
        similarity_threshold=similarity_threshold,
        top_k=top_k,
        use_threshold=use_threshold,
        data_path=data_path
    )
    
    return service