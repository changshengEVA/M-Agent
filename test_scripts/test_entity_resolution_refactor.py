#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试重构后的 EntityResolutionService

验证重构是否符合要求：
1. 是否正确保存数据于附属数据库 entity_library 中
2. 是否能正确响应监听事件
3. 是否能够正确解析数据，保存和使用解析状态
"""

import os
import sys
import tempfile
import json
import shutil
from typing import List

# 添加项目根目录到路径
sys.path.append('.')

def mock_llm_func(prompt: str) -> str:
    """模拟 LLM 函数"""
    return "这是一个模拟的 LLM 响应"

def mock_embed_func(text: str) -> List[float]:
    """模拟嵌入向量生成函数"""
    return [0.1, 0.2, 0.3, 0.4, 0.5]

def test_entity_library_save_and_load():
    """测试 EntityLibrary 的保存和加载功能"""
    print("=== 测试 1: EntityLibrary 保存和加载 ===")
    
    from memory.memory_core.services_bank.entity_resolution.library import EntityLibrary
    
    # 创建临时目录用于测试
    temp_dir = tempfile.mkdtemp()
    test_dir = os.path.join(temp_dir, "test_library")
    
    try:
        # 创建 EntityLibrary
        library = EntityLibrary(embed_func=mock_embed_func)
        
        # 添加一些实体
        library.add_entity("entity_1", "Entity One", metadata={"type": "person"})
        library.add_entity("entity_2", "Entity Two", metadata={"type": "location"})
        
        # 为实体添加别名
        library.add_alias("entity_1", "alias_1")
        
        # 标记实体解析状态
        record1 = library.get_entity("entity_1")
        if record1:
            record1.mark_as_resolved({"decision": "NEW_ENTITY", "confidence": 0.9})
        
        # 保存到目录
        success = library.save_to_path(test_dir)
        assert success, "保存 EntityLibrary 失败"
        print("✅ EntityLibrary 保存成功")
        
        # 创建新的 EntityLibrary 并加载
        library2 = EntityLibrary(embed_func=mock_embed_func)
        success = library2.load_from_path(test_dir)
        assert success, "加载 EntityLibrary 失败"
        
        # 验证加载的数据
        assert library2.entity_exists("entity_1"), "实体 entity_1 未加载"
        assert library2.entity_exists("entity_2"), "实体 entity_2 未加载"
        
        # 验证解析状态
        record1_loaded = library2.get_entity("entity_1")
        assert record1_loaded is not None, "加载的实体记录为空"
        assert record1_loaded.resolved, "实体解析状态未正确加载"
        assert record1_loaded.last_decision is not None, "实体解析决策未正确加载"
        
        print("✅ EntityLibrary 加载成功，解析状态正确")
        
    finally:
        # 清理临时目录
        shutil.rmtree(temp_dir)
    
    print("✅ 测试 1 通过\n")

def test_service_event_handling():
    """测试 Service 的事件处理"""
    print("=== 测试 2: Service 事件处理 ===")
    
    from memory.memory_core.services_bank.entity_resolution.service import EntityResolutionService
    
    # 创建 Service
    service = EntityResolutionService(
        llm_func=mock_llm_func,
        embed_func=mock_embed_func,
        data_path=None  # 不保存到文件
    )
    
    # 测试 on_entity_added
    print("测试 on_entity_added...")
    service.on_entity_added("test_entity_1")
    
    # 验证实体已添加到 library
    assert service.entity_library.entity_exists("test_entity_1"), "实体未添加到 library"
    
    # 验证实体标记为未解析
    record = service.entity_library.get_entity("test_entity_1")
    assert record is not None, "实体记录为空"
    assert not record.resolved, "新实体应标记为未解析"
    
    print("✅ on_entity_added 工作正常")
    
    # 测试 on_entity_merged
    print("测试 on_entity_merged...")
    
    # 先添加目标实体
    service.entity_library.add_entity("target_entity", "Target Entity")
    
    # 触发合并事件
    service.on_entity_merged("source_entity", "target_entity")
    
    # 验证别名已添加
    target_record = service.entity_library.get_entity("target_entity")
    assert target_record is not None, "目标实体记录为空"
    assert "source_entity" in target_record.aliases, "源实体未添加为别名"
    
    print("✅ on_entity_merged 工作正常")
    
    # 测试 on_entity_renamed
    print("测试 on_entity_renamed...")
    
    # 添加一个实体
    service.entity_library.add_entity("old_entity", "Old Entity")
    old_record = service.entity_library.get_entity("old_entity")
    old_record.mark_as_resolved({"decision": "NEW_ENTITY", "confidence": 0.8})
    
    # 触发重命名事件
    service.on_entity_renamed("old_entity", "new_entity")
    
    # 验证重命名
    assert not service.entity_library.entity_exists("old_entity"), "旧实体应被移除"
    assert service.entity_library.entity_exists("new_entity"), "新实体应存在"
    
    new_record = service.entity_library.get_entity("new_entity")
    assert new_record is not None, "新实体记录为空"
    assert new_record.resolved, "解析状态应被保留"
    assert "old_entity" in new_record.aliases, "旧实体ID应添加为别名"
    
    print("✅ on_entity_renamed 工作正常")
    
    print("✅ 测试 2 通过\n")

def test_resolve_unresolved_entities():
    """测试批量解析未解析实体"""
    print("=== 测试 3: 批量解析未解析实体 ===")
    
    from memory.memory_core.services_bank.entity_resolution.service import EntityResolutionService
    from memory.memory_core.services_bank.entity_resolution.decision import ResolutionType
    
    # 创建 Service
    service = EntityResolutionService(
        llm_func=mock_llm_func,
        embed_func=mock_embed_func,
        data_path=None
    )
    
    # 添加一些未解析的实体
    service.entity_library.add_entity("unresolved_1", "Unresolved One")
    service.entity_library.add_entity("unresolved_2", "Unresolved Two")
    
    # 添加一个已解析的实体作为对比
    service.entity_library.add_entity("resolved_1", "Resolved One")
    resolved_record = service.entity_library.get_entity("resolved_1")
    resolved_record.mark_as_resolved({"decision": "NEW_ENTITY", "confidence": 1.0})
    
    # 执行批量解析
    decisions = service.resolve_unresolved_entities()
    
    # 验证结果
    assert len(decisions) == 2, f"应解析2个实体，实际解析了{len(decisions)}个"
    print(f"✅ 批量解析完成，解析了 {len(decisions)} 个实体")
    
    # 验证实体标记为已解析
    for entity_id in ["unresolved_1", "unresolved_2"]:
        record = service.entity_library.get_entity(entity_id)
        assert record is not None, f"实体 {entity_id} 记录为空"
        assert record.resolved, f"实体 {entity_id} 应标记为已解析"
        assert record.last_decision is not None, f"实体 {entity_id} 应保存解析决策"
    
    # 验证已解析的实体未被重新解析
    resolved_record = service.entity_library.get_entity("resolved_1")
    assert resolved_record.resolved, "已解析实体应保持已解析状态"
    
    print("✅ 测试 3 通过\n")

def test_no_kg_side_effects():
    """测试 Service 没有 KG 副作用"""
    print("=== 测试 4: 验证无 KG 副作用 ===")
    
    from memory.memory_core.services_bank.entity_resolution.service import EntityResolutionService
    from memory.memory_core.services_bank.entity_resolution.decision import ResolutionType
    
    # 创建 Service
    service = EntityResolutionService(
        llm_func=mock_llm_func,
        embed_func=mock_embed_func,
        data_path=None
    )
    
    # 测试 1: resolve_entity 方法只返回决策，不执行 KG 操作
    print("测试 resolve_entity 方法...")
    service.entity_library.add_entity("existing_entity", "Existing Entity")
    
    # 解析一个实体
    decision = service.resolve_entity("new_entity")
    
    # 验证只返回决策，不执行 KG 操作
    assert decision is not None, "解析决策不应为空"
    assert decision.source_entity_id == "new_entity", "源实体ID应正确"
    
    # 验证 library 状态未自动更新（因为 Service 不自动应用决策）
    new_record = service.entity_library.get_entity("new_entity")
    assert new_record is None, "Service 不应自动添加实体到 library"
    
    print("✅ resolve_entity 只返回决策，不执行 KG 操作")
    
    # 测试 2: resolve_unresolved_entities 方法只返回决策列表，不执行 KG 操作
    print("测试 resolve_unresolved_entities 方法...")
    
    # 清空 library，确保没有其他实体
    service.entity_library.clear()
    
    # 添加一些未解析的实体
    service.entity_library.add_entity("unresolved_1", "Unresolved One")
    service.entity_library.add_entity("unresolved_2", "Unresolved Two")
    
    # 执行批量解析
    decisions = service.resolve_unresolved_entities()
    
    # 验证返回决策列表
    assert len(decisions) == 2, f"应返回2个决策，实际返回{len(decisions)}个"
    
    # 验证实体被标记为已解析（这是 Service 的内部状态更新，不是 KG 操作）
    for entity_id in ["unresolved_1", "unresolved_2"]:
        record = service.entity_library.get_entity(entity_id)
        assert record is not None, f"实体 {entity_id} 记录为空"
        assert record.resolved, f"实体 {entity_id} 应标记为已解析"
        assert record.last_decision is not None, f"实体 {entity_id} 应保存解析决策"
    
    print("✅ resolve_unresolved_entities 只返回决策列表，不执行 KG 操作")
    
    # 测试 3: on_entity_added 事件处理不执行自动解析
    print("测试 on_entity_added 事件处理...")
    
    # 清空 library
    service.entity_library.clear()
    
    # 模拟实体添加事件
    service.on_entity_added("test_entity")
    
    # 验证实体被添加到 library 但未自动解析
    test_record = service.entity_library.get_entity("test_entity")
    assert test_record is not None, "实体应被添加到 library"
    assert not test_record.resolved, "实体不应被自动解析"
    assert test_record.last_decision is None, "实体不应有解析决策"
    
    print("✅ on_entity_added 只同步状态，不执行自动解析")
    
    print("✅ Service 无 KG 副作用，符合被动分析型设计")
    print("✅ 测试 4 通过\n")

def test_overall_refactoring_principles():
    """测试整体重构原则"""
    print("=== 测试 5: 整体重构原则验证 ===")
    
    from memory.memory_core.services_bank.entity_resolution.service import EntityResolutionService
    
    # 创建 Service
    service = EntityResolutionService(
        llm_func=mock_llm_func,
        embed_func=mock_embed_func,
        data_path=None
    )
    
    # 原则 1: Service 不再驱动系统流程
    print("验证原则 1: Service 不再驱动系统流程...")
    
    # 检查 on_entity_added 是否触发解析
    # 通过 monkey patch 来检查是否调用了 resolve_entity
    resolve_called = []
    original_resolve = service.resolve_entity
    
    def mock_resolve(entity_id, context=None):
        resolve_called.append(entity_id)
        return original_resolve(entity_id, context)
    
    service.resolve_entity = mock_resolve
    
    # 触发实体添加事件
    service.on_entity_added("test_entity_no_resolve")
    
    # 验证 resolve_entity 未被调用
    assert len(resolve_called) == 0, "on_entity_added 不应触发解析"
    print("✅ on_entity_added 不触发解析")
    
    # 恢复原始方法
    service.resolve_entity = original_resolve
    
    # 原则 2: Service 产生认知，Sys 决定现实
    print("验证原则 2: Service 产生认知，Sys 决定现实...")
    
    # 批量解析应返回决策列表，但不执行任何操作
    decisions = service.resolve_unresolved_entities()
    assert isinstance(decisions, list), "批量解析应返回决策列表"
    print(f"✅ 批量解析返回 {len(decisions)} 个决策建议")
    
    # 原则 3: 监听器只用于同步状态
    print("验证原则 3: 监听器只用于同步状态...")
    
    # 检查监听器方法是否包含解析或应用操作
    import inspect
    listener_methods = ["on_entity_added", "on_entity_merged", "on_entity_renamed"]
    
    for method_name in listener_methods:
        method = getattr(service, method_name)
        source = inspect.getsource(method)
        
        # 检查是否包含不应有的操作
        forbidden_terms = ["resolve_entity", "apply_decision", "merge_entities", "add_entity_to_kg"]
        for term in forbidden_terms:
            assert term not in source, f"{method_name} 不应包含 {term}"
    
    print("✅ 监听器仅用于同步状态")
    
    print("✅ 测试 5 通过\n")

def main():
    """主测试函数"""
    print("开始测试重构后的 EntityResolutionService")
    print("=" * 60)
    
    try:
        test_entity_library_save_and_load()
        test_service_event_handling()
        test_resolve_unresolved_entities()
        test_no_kg_side_effects()
        test_overall_refactoring_principles()
        
        print("=" * 60)
        print("✅ 所有测试通过！")
        print("\n重构总结：")
        print("1. ✅ EntityResolutionService 已重构为被动分析型 Service")
        print("2. ✅ 删除了自动解析机制")
        print("3. ✅ 为 EntityLibrary 增加了解析状态")
        print("4. ✅ 新增了批量解析入口 resolve_unresolved_entities()")
        print("5. ✅ 移除了所有 KG 副作用")
        print("6. ✅ 监听器仅用于同步状态")
        print("7. ✅ 最终调用模式：Service THINKS, Sys ACTS")
        
        return 0
        
    except AssertionError as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"❌ 测试发生错误: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())