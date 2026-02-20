#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Memory Core 系统

核心入口类，封装 KGBase 和 EntityResolutionService，提供对话数据加载接口。
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List

from memory.memory_core.core.kg_base import KGBase
from memory.memory_core.services_bank.entity_resolution.service import (
    EntityResolutionService,
    create_default_resolution_service
)
from memory.memory_core.system.event_bus import EventBus
from memory.memory_core.system.event_types import EventType

# 导入 load_model 中的 OpenAI 模型函数
from load_model.OpenAIcall import get_llm, get_embed_model

logger = logging.getLogger(__name__)


class MemoryCore:
    """
    Memory Core 主类
    
    初始化步骤：
    1. 根据工作流ID确定数据路径
    2. 初始化 KGBase（实体目录和关系目录）
    3. 初始化 EntityResolutionService（使用 KGBase 和 LLM/Embed 模型）
    4. 暴露两个数据加载接口
    """
    
    def __init__(
        self,
        workflow_id: str,
        llm_func: Optional[Callable[[str], str]] = None,
        embed_func: Optional[Callable[[str], List[float]]] = None,
        llm_temperature: float = 0.0,
        similarity_threshold: float = 0.7,
        top_k: int = 3,
        use_threshold: bool = True
    ):
        """
        初始化 MemoryCore
        
        Args:
            workflow_id: 工作流ID，用于确定数据存储路径
            llm_func: 可选的LLM函数，如果为None则使用默认OpenAI LLM
            embed_func: 可选的嵌入函数，如果为None则使用默认OpenAI Embedding
            llm_temperature: LLM温度参数（仅当使用默认LLM时有效）
            similarity_threshold: 实体解析相似度阈值
            top_k: 实体解析返回前K个候选
            use_threshold: 是否使用阈值模式
        """
        self.workflow_id = workflow_id
        self.llm_temperature = llm_temperature
        self.similarity_threshold = similarity_threshold
        self.top_k = top_k
        self.use_threshold = use_threshold
        
        # 1. 构建数据路径
        self.kg_data_path = Path(f"data/memory/{workflow_id}/kg_data")
        self.entity_dir = self.kg_data_path / "entity"
        self.relation_dir = self.kg_data_path / "relation"
        self.entity_library_path = self.kg_data_path / "entity_library"
        
        # 确保目录存在
        self.entity_dir.mkdir(parents=True, exist_ok=True)
        self.relation_dir.mkdir(parents=True, exist_ok=True)
        self.entity_library_path.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"初始化 MemoryCore，工作流ID: {workflow_id}")
        logger.info(f"KG数据路径: {self.kg_data_path}")
        logger.info(f"实体目录: {self.entity_dir}")
        logger.info(f"关系目录: {self.relation_dir}")
        logger.info(f"实体库路径: {self.entity_library_path}")
        
        # 2. 初始化 EventBus
        self.event_bus = EventBus()
        logger.info("EventBus 初始化完成")
        
        # 3. 初始化 KGBase（传入 event_bus）
        self.kg_base = self._init_kg_base()
        
        # 4. 初始化 LLM 和 Embed 函数
        self.llm_func, self.embed_func = self._init_llm_embed(llm_func, embed_func)
        
        # 5. 初始化 EntityResolutionService并进行注册
        self.entity_resolution_service = self._init_entity_resolution_service()
        self.register_service(self.entity_resolution_service)

        self.feature_search = None
        
        # 8. 根据重构原则，MemoryCore 不再注册自身到 EventBus
        # 不再监听 ENTITY_ADDED 事件来自动触发解析
        # 解析将由显式的 Resolution Pass 调度
        
        # 9. 发布系统初始化事件
        self.event_bus.publish(EventType.SYSTEM_INITIALIZED, {})
        
        logger.info("MemoryCore 初始化完成")
    
    def _init_kg_base(self) -> KGBase:
        """初始化 KGBase 实例"""
        logger.info(f"初始化 KGBase: entity_dir={self.entity_dir}, relation_dir={self.relation_dir}")
        return KGBase(
            entity_dir=self.entity_dir,
            relation_dir=self.relation_dir,
            event_bus=self.event_bus
        )
    
    def _init_llm_embed(
        self,
        llm_func: Optional[Callable[[str], str]],
        embed_func: Optional[Callable[[str], List[float]]]
    ) -> tuple[Callable[[str], str], Callable[[str], List[float]]]:
        """
        初始化 LLM 和 Embed 函数
        
        Returns:
            (llm_func, embed_func) 元组
        """
        if llm_func is None:
            logger.info("使用默认 OpenAI LLM")
            llm_func = get_llm(self.llm_temperature)
        
        if embed_func is None:
            logger.info("使用默认 OpenAI Embedding")
            embed_func = get_embed_model()
        
        return llm_func, embed_func
    
    def _init_entity_resolution_service(self) -> EntityResolutionService:
        """初始化 EntityResolutionService 实例"""
        logger.info("初始化 EntityResolutionService")
        
        # 使用便捷函数创建默认服务（不再传入kg_base）
        service = create_default_resolution_service(
            llm_func=self.llm_func,
            embed_func=self.embed_func,
            similarity_threshold=self.similarity_threshold,
            top_k=self.top_k,
            use_threshold=self.use_threshold,
            data_path=str(self.entity_library_path)
        )
        
        # 对齐实体库与 KG 中的实体
        self._align_entity_library_with_kg(service)
        
        return service
    
    def _align_entity_library_with_kg(self, service: EntityResolutionService) -> None:
        """
        初始化 EntityLibrary 与 KG 的同步
        
        比对 library 和 kg_data 中的内容，如果对不上就直接调用 library 中的 rebuild_from_kg 进行重建，
        然后再用 resolve_unresolved_entities，这样就对齐了。
        """
        try:
            # 辅助函数：规范化实体ID（与 EntityRepository._sanitize_entity_name 保持一致）
            def normalize_entity_id(entity_id: str) -> str:
                """规范化实体ID，将空格替换为下划线，处理特殊字符"""
                if not entity_id:
                    return entity_id
                # 去除首尾空格
                normalized = entity_id.strip()
                # 替换空格为下划线
                normalized = normalized.replace(' ', '_')
                # 替换其他可能的问题字符（简化版本，与 EntityRepository 保持一致）
                for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
                    normalized = normalized.replace(char, '_')
                # 限制长度（可选）
                if len(normalized) > 100:
                    normalized = normalized[:100]
                return normalized
            
            # 1. 获取 KG 中的所有实体 ID
            kg_entity_ids_raw = set(self.kg_base.list_entity_ids())
            logger.info(f"KG 中有 {len(kg_entity_ids_raw)} 个实体（原始ID）")
            
            # 2. 获取 EntityLibrary 中的所有实体 ID
            library_entity_ids_raw = set()
            for entity_id in service.entity_library.entities.keys():
                library_entity_ids_raw.add(entity_id)
            logger.info(f"EntityLibrary 中有 {len(library_entity_ids_raw)} 个实体（原始ID）")
            
            # 3. 创建规范化后的ID集合用于比较
            kg_entity_ids_normalized = {normalize_entity_id(entity_id) for entity_id in kg_entity_ids_raw}
            library_entity_ids_normalized = {normalize_entity_id(entity_id) for entity_id in library_entity_ids_raw}
            
            logger.info(f"KG 规范化后有 {len(kg_entity_ids_normalized)} 个实体")
            logger.info(f"EntityLibrary 规范化后有 {len(library_entity_ids_normalized)} 个实体")
            
            # 4. 比较规范化后的ID是否一致
            if kg_entity_ids_normalized == library_entity_ids_normalized:
                logger.info("EntityLibrary 与 KG 实体一致（规范化后），无需重建")
                
                # 尝试加载已有的 Library 数据
                try:
                    if hasattr(service, 'data_path') and service.data_path:
                        load_success = service.entity_library.load_from_path(service.data_path)
                        if load_success:
                            logger.info(f"EntityLibrary 从文件加载成功: {service.data_path}")
                        else:
                            logger.info(f"EntityLibrary 文件不存在或加载失败，将从头构建: {service.data_path}")
                    else:
                        logger.debug("未配置 data_path，EntityLibrary 将从头构建")
                except Exception as load_e:
                    logger.warning(f"加载 EntityLibrary 数据时出错: {load_e}")
                    
            else:
                # 计算规范化后的差异
                kg_only_normalized = kg_entity_ids_normalized - library_entity_ids_normalized
                library_only_normalized = library_entity_ids_normalized - kg_entity_ids_normalized
                
                # 计算原始ID的差异（用于显示）
                kg_only_raw = kg_entity_ids_raw - library_entity_ids_raw
                library_only_raw = library_entity_ids_raw - kg_entity_ids_raw
                
                logger.warning(f"EntityLibrary 与 KG 实体不一致，需要重建")
                logger.warning(f"实体数量不匹配（规范化后）: KG 有 {len(kg_entity_ids_normalized)} 个实体，EntityLibrary 有 {len(library_entity_ids_normalized)} 个实体")
                logger.warning(f"原始ID数量: KG 有 {len(kg_entity_ids_raw)} 个，EntityLibrary 有 {len(library_entity_ids_raw)} 个")
                logger.warning(f"差异详情（规范化后）:")
                logger.warning(f"  - KG 中有但 EntityLibrary 中没有的实体 ({len(kg_only_normalized)} 个): {sorted(list(kg_only_normalized))[:10]}{'...' if len(kg_only_normalized) > 10 else ''}")
                logger.warning(f"  - EntityLibrary 中有但 KG 中没有的实体 ({len(library_only_normalized)} 个): {sorted(list(library_only_normalized))[:10]}{'...' if len(library_only_normalized) > 10 else ''}")
                
                # 显示原始ID差异（如果与规范化后不同）
                if kg_only_raw != kg_only_normalized or library_only_raw != library_only_normalized:
                    logger.info(f"原始ID差异（可能因规范化而不同）:")
                    if kg_only_raw:
                        logger.info(f"  KG 原始ID有但 Library 原始ID没有: {sorted(list(kg_only_raw))[:10]}{'...' if len(kg_only_raw) > 10 else ''}")
                    if library_only_raw:
                        logger.info(f"  Library 原始ID有但 KG 原始ID没有: {sorted(list(library_only_raw))[:10]}{'...' if len(library_only_raw) > 10 else ''}")
                
                # 如果差异数量较少，显示全部
                if len(kg_only_normalized) <= 20:
                    logger.info(f"KG 中有但 Library 中没有的实体（规范化后）: {sorted(list(kg_only_normalized))}")
                if len(library_only_normalized) <= 20:
                    logger.info(f"Library 中有但 KG 中没有的实体（规范化后）: {sorted(list(library_only_normalized))}")
                
                # 4. 构建 kg_data 用于重建
                kg_data = {"entities": []}
                # 使用原始KG实体ID进行加载（因为文件系统使用的是规范化后的ID）
                for entity_id in kg_entity_ids_raw:
                    success, entity_data = self.kg_base.repos.entity.load(entity_id)
                    if success:
                        # 确保实体数据包含必要的字段
                        # 注意：entity_id 是规范化后的ID（带下划线），但实体数据中的name可能是原始名称（带空格）
                        entity_info = {
                            "id": entity_id,
                            "name": entity_data.get("name", entity_id),
                            "type": entity_data.get("type"),
                            "metadata": entity_data.get("metadata", {})
                        }
                        kg_data["entities"].append(entity_info)
                    else:
                        logger.warning(f"无法加载实体 {entity_id} 的详细信息")
                
                # 5. 调用 rebuild_from_kg 进行重建
                logger.info(f"开始从 KG 重建 EntityLibrary，共 {len(kg_data['entities'])} 个实体")
                rebuild_success = service.entity_library.rebuild_from_kg(kg_data)
                if rebuild_success:
                    logger.info("EntityLibrary 重建成功")
                    
                    # 6. 调用 resolve_unresolved_entities 对齐
                    logger.info("开始解析未解析的实体以完成对齐")
                    self.run_entity_resolution_pass()
                    
                else:
                    logger.error("EntityLibrary 重建失败")
                    
        except Exception as e:
            logger.warning(f"初始化 EntityLibrary 同步时出错: {e}")
    
    # ============================================================================
    # 公开接口
    # ============================================================================
    
    def load_from_dialogue_json(self, json_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        从单个对话 JSON 数据加载到 KG
        
        Args:
            json_data: 对话 JSON 数据（格式参照 kg_candidate 中的单个文件）
            
        Returns:
            操作结果字典
        """
        from .workflow.load_dialogue_data import load_from_dialogue_json
        
        logger.info("调用 load_from_dialogue_json 接口")
        return load_from_dialogue_json(
            json_data=json_data,
            kg_base=self.kg_base,
            entity_resolution_service=self.entity_resolution_service,
            memory_core=self  # 传递 MemoryCore 实例
        )
    
    def load_from_dialogue_path(self, path: Path) -> Dict[str, Any]:
        """
        从对话数据目录加载所有文件到 KG
        
        Args:
            path: kg_candidate 目录路径
            
        Returns:
            操作结果字典
        """
        from .workflow.load_dialogue_data import load_from_dialogue_path
        
        logger.info(f"调用 load_from_dialogue_path 接口，路径: {path}")
        return load_from_dialogue_path(
            path=path,
            kg_base=self.kg_base,
            entity_resolution_service=self.entity_resolution_service,
            memory_core=self  # 传递 MemoryCore 实例
        )
    
    def search_feature_by_entity_id():
        
        pass
    # ============================================================================
    # 服务注册
    # ============================================================================
    
    def register_service(self, service: Any) -> None:
        """
        注册服务到 EventBus
        
        Args:
            service: 服务实例，必须是 BaseService 的子类
        """
        # 验证服务是 BaseService 的子类
        if hasattr(service, 'get_subscribed_events') and hasattr(service, 'handle_event'):
            self.event_bus.register(service)
            logger.info(f"注册服务到 EventBus: {service.__class__.__name__}")
        else:
            logger.warning(f"服务 {service.__class__.__name__} 不是 BaseService 子类，无法注册到 EventBus")
    
    # ============================================================================
    # Service 功能执行部件
    # ============================================================================
    def run_entity_resolution_pass(self) -> Dict[str, Any]:
        """
        执行实体解析阶段（Resolution Pass）
        
        重构为四个阶段：
        Phase 1：收集解析结果（不修改 KG）
        Phase 2：构建 Identity Graph（无向等价关系图）
        Phase 3：计算 Identity Groups（连通分量）
        Phase 4：稳定 Merge（统一指向 canonical entity）
        
        Returns:
            解析阶段执行结果
        """
        logger.info("开始执行实体解析阶段（Resolution Pass）")
        
        logger.info("Phase 1: 收集解析结果")
        decisions = self.entity_resolution_service.resolve_unresolved_entities()
        logger.info(f"获取到 {len(decisions)} 个解析决策")
        
        results = {
            "total_decisions": len(decisions),
            "same_as_existing": 0,
            "new_entities": 0,
            "merged": 0,
            "merge_errors": [],
            "decisions": [],
            "identity_groups": 0,
            "canonical_entities": 0
        }
        
        # 记录所有决策
        for decision in decisions:
            decision_dict = decision.to_dict()
            results["decisions"].append(decision_dict)
            
            if decision.is_same_as_existing():
                results["same_as_existing"] += 1
            elif decision.is_new_entity():
                results["new_entities"] += 1
        
        logger.info("Phase 2: 构建 Identity Graph")
        
        # 收集所有 SAME_AS_EXISTING 关系
        same_as_relations = []
        entity_set = set()
        
        for decision in decisions:
            if decision.is_same_as_existing() and decision.target_entity_id:
                source = decision.source_entity_id
                target = decision.target_entity_id
                same_as_relations.append((source, target))
                entity_set.add(source)
                entity_set.add(target)
        
        logger.info(f"构建 Identity Graph: {len(same_as_relations)} 个等价关系, {len(entity_set)} 个唯一实体")
        
        logger.info("Phase 3: 计算 Identity Groups（连通分量）")
        
        # 实现 Union-Find（并查集）
        parent = {}
        rank = {}
        
        def find(x):
            if x not in parent:
                parent[x] = x
                rank[x] = 0
                return x
            if parent[x] != x:
                parent[x] = find(parent[x])  # 路径压缩
            return parent[x]
        
        def union(x, y):
            root_x = find(x)
            root_y = find(y)
            if root_x != root_y:
                # 按秩合并
                if rank[root_x] < rank[root_y]:
                    parent[root_x] = root_y
                elif rank[root_x] > rank[root_y]:
                    parent[root_y] = root_x
                else:
                    parent[root_y] = root_x
                    rank[root_x] += 1
        
        # 应用所有等价关系
        for source, target in same_as_relations:
            union(source, target)
        
        # 收集连通分量
        groups = {}
        for entity in entity_set:
            root = find(entity)
            if root not in groups:
                groups[root] = []
            groups[root].append(entity)
        
        # 过滤掉只有一个实体的组（这些实体没有等价关系）
        identity_groups = {root: entities for root, entities in groups.items() if len(entities) > 1}
        
        logger.info(f"计算得到 {len(identity_groups)} 个 Identity Groups（连通分量）")
        results["identity_groups"] = len(identity_groups)
        
        logger.info("Phase 4: 稳定 Merge（统一指向 canonical entity）")
        
        # 为每个 Identity Group 选择 canonical entity
        for root, entities in identity_groups.items():
            logger.info(f"处理 Identity Group: {entities}")
            
            # 选择 canonical entity 的规则：
            # 1. 优先选择在决策中作为 target 出现次数最多的实体
            # 2. 如果平局，选择第一个实体
            
            # 统计每个实体作为 target 出现的次数
            target_count = {}
            for decision in decisions:
                if decision.is_same_as_existing() and decision.target_entity_id:
                    target = decision.target_entity_id
                    if target in entities:
                        target_count[target] = target_count.get(target, 0) + 1
            
            # 选择 canonical entity
            canonical = None
            if target_count:
                # 选择出现次数最多的实体
                canonical = max(target_count.items(), key=lambda x: x[1])[0]
            else:
                # 如果没有 target 统计，选择第一个实体
                canonical = entities[0]
            
            logger.info(f"  选择 canonical entity: {canonical}")
            results["canonical_entities"] += 1
            
            # 对 group 中其余实体执行 merge（直接指向 canonical）
            for entity in entities:
                if entity == canonical:
                    continue  # 跳过 canonical 实体本身
                
                try:
                    logger.info(f"  执行实体合并: {entity} -> {canonical}")
                    merge_result = self.kg_base.merge_entities(
                        target_id=canonical,
                        source_id=entity
                    )
                    
                    if merge_result.get("success", False):
                        results["merged"] += 1
                        logger.info(f"  实体合并成功: {entity} -> {canonical}")
                    else:
                        error_msg = f"合并失败: {merge_result.get('error', 'unknown')}"
                        results["merge_errors"].append({
                            "source": entity,
                            "target": canonical,
                            "error": error_msg
                        })
                        logger.warning(f"  实体合并失败: {error_msg}")
                        
                except Exception as e:
                    error_msg = f"合并异常: {str(e)}"
                    results["merge_errors"].append({
                        "source": entity,
                        "target": canonical,
                        "error": error_msg
                    })
                    logger.error(f"  实体合并异常: {e}")
        
        # 处理新建实体判定（不执行任何操作，仅记录）
        for decision in decisions:
            if decision.is_new_entity():
                logger.info(f"新建实体判定: {decision.source_entity_id}（不执行任何操作）")
        
        # ============================================================================
        # 完成阶段
        # ============================================================================
        logger.info(f"解析阶段完成: "
                   f"总计 {results['total_decisions']} 个决策, "
                   f"等价实体 {results['same_as_existing']} 个, "
                   f"新建实体 {results['new_entities']} 个, "
                   f"Identity Groups {results['identity_groups']} 个, "
                   f"Canonical Entities {results['canonical_entities']} 个, "
                   f"成功合并 {results['merged']} 个")
        
        results["success"] = len(results["merge_errors"]) == 0
        return results
    
    # ============================================================================
    # 辅助方法
    # ============================================================================
    
    def get_kg_stats(self) -> Dict[str, Any]:
        """获取知识图谱统计信息"""
        return self.kg_base.get_kg_stats()
    
    def get_entity_resolution_stats(self) -> Dict[str, Any]:
        """获取实体解析服务统计信息"""
        return self.entity_resolution_service.get_library_stats()
    
    def __str__(self) -> str:
        """字符串表示"""
        kg_stats = self.get_kg_stats()
        return (f"MemoryCore(workflow_id={self.workflow_id}, "
                f"entities={kg_stats['entity_count']}, "
                f"relations={kg_stats['relation_count']}, "
                f"services={len(self.services)})")


# 便捷函数
def create_memory_core(
    workflow_id: str,
    llm_func: Optional[Callable[[str], str]] = None,
    embed_func: Optional[Callable[[str], List[float]]] = None,
    **kwargs
) -> MemoryCore:
    """
    创建 MemoryCore 实例的便捷函数
    
    Args:
        workflow_id: 工作流ID
        llm_func: 可选的LLM函数
        embed_func: 可选的嵌入函数
        **kwargs: 其他参数传递给 MemoryCore.__init__
        
    Returns:
        MemoryCore 实例
    """
    return MemoryCore(
        workflow_id=workflow_id,
        llm_func=llm_func,
        embed_func=embed_func,
        **kwargs
    )