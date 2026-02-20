#!/usr/bin/env python3
"""
诊断 EntityLibrary 与 KG_data 实体数量不一致问题
"""

import sys
import os
import tempfile
import shutil
from pathlib import Path

# 添加项目根目录到路径
sys.path.append('.')

from memory.memory_core.services_bank.entity_resolution.library import EntityLibrary
from memory.memory_core.services_bank.entity_resolution.service import EntityResolutionService
from memory.memory_core.services_bank.entity_resolution.listener import EntityMergeListener
from memory.memory_core.system.event_types import EventType

def test_entity_resolution_service_on_entity_merged():
    """测试 EntityResolutionService.on_entity_merged 方法"""
    print("=== 测试 EntityResolutionService.on_entity_merged ===")
    
    # 创建临时目录
    temp_dir = tempfile.mkdtemp()
    print(f"临时目录: {temp_dir}")
    
    try:
        # 创建模拟的 LLM 和 Embed 函数
        def mock_llm(prompt):
            return "mock response"
        
        def mock_embed(text):
            return [0.1] * 1536  # 模拟嵌入向量
        
        # 创建 EntityResolutionService
        service = EntityResolutionService(
            llm_func=mock_llm,
            embed_func=mock_embed,
            similarity_threshold=0.7,
            top_k=3,
            use_threshold=True,
            data_path=temp_dir
        )
        
        # 添加一些测试实体
        print("\n1. 添加测试实体...")
        service.entity_library.add_entity("entity_A", "Entity A")
        service.entity_library.add_entity("entity_B", "Entity B")
        service.entity_library.add_entity("entity_C", "Entity C")
        
        print(f"   添加后实体数量: {len(service.entity_library.entities)}")
        print(f"   实体列表: {list(service.entity_library.entities.keys())}")
        
        # 测试 on_entity_merged 方法
        print("\n2. 测试实体合并 (entity_B -> entity_A)...")
        service.on_entity_merged("entity_B", "entity_A")
        
        print(f"   合并后实体数量: {len(service.entity_library.entities)}")
        print(f"   实体列表: {list(service.entity_library.entities.keys())}")
        
        # 检查 entity_B 是否被删除
        if "entity_B" in service.entity_library.entities:
            print("❌ 错误: entity_B 仍然在 EntityLibrary 中")
            return False
        else:
            print("✅ 正确: entity_B 已从 EntityLibrary 中删除")
        
        # 检查 entity_A 是否仍然存在
        if "entity_A" in service.entity_library.entities:
            print("✅ 正确: entity_A 仍然在 EntityLibrary 中")
        else:
            print("❌ 错误: entity_A 不在 EntityLibrary 中")
            return False
        
        # 检查 name_to_entity 映射
        print("\n3. 检查 name_to_entity 映射...")
        if "entity_B" in service.entity_library.name_to_entity:
            print(f"   entity_B 映射到: {service.entity_library.name_to_entity.get('entity_B')}")
        else:
            print("   entity_B 不在 name_to_entity 中 (正确)")
        
        # 检查 entity_B 是否作为 entity_A 的别名
        if service.entity_library.name_to_entity.get("entity_B") == "entity_A":
            print("✅ 正确: entity_B 作为 entity_A 的别名")
        else:
            print("⚠️  注意: entity_B 没有作为 entity_A 的别名")
        
        # 测试保存和加载
        print("\n4. 测试保存和加载...")
        save_success = service.save_library()
        if save_success:
            print("✅ 保存成功")
        else:
            print("❌ 保存失败")
            return False
        
        # 创建新的服务实例并加载
        service2 = EntityResolutionService(
            llm_func=mock_llm,
            embed_func=mock_embed,
            similarity_threshold=0.7,
            top_k=3,
            use_threshold=True,
            data_path=temp_dir
        )
        
        # 检查加载后的实体数量
        print(f"   加载后实体数量: {len(service2.entity_library.entities)}")
        print(f"   加载后实体列表: {list(service2.entity_library.entities.keys())}")
        
        if len(service2.entity_library.entities) == 2:  # entity_A 和 entity_C
            print("✅ 正确: 加载后实体数量正确")
        else:
            print(f"❌ 错误: 期望 2 个实体，实际 {len(service2.entity_library.entities)} 个")
            return False
        
        return True
        
    finally:
        # 清理临时目录
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"\n清理临时目录: {temp_dir}")

def test_entity_merge_listener():
    """测试 EntityMergeListener.on_entity_merged 方法"""
    print("\n=== 测试 EntityMergeListener.on_entity_merged ===")
    
    # 创建临时目录
    temp_dir = tempfile.mkdtemp()
    print(f"临时目录: {temp_dir}")
    
    try:
        # 创建 EntityLibrary
        library = EntityLibrary(data_path=temp_dir)
        
        # 添加一些测试实体
        print("\n1. 添加测试实体...")
        library.add_entity("entity_X", "Entity X")
        library.add_entity("entity_Y", "Entity Y")
        library.add_entity("entity_Z", "Entity Z")
        
        print(f"   添加后实体数量: {len(library.entities)}")
        
        # 创建 EntityMergeListener
        listener = EntityMergeListener(library)
        
        # 测试 on_entity_merged 方法
        print("\n2. 测试实体合并 (entity_Y -> entity_X)...")
        listener.on_entity_merged("entity_Y", "entity_X")
        
        print(f"   合并后实体数量: {len(library.entities)}")
        print(f"   实体列表: {list(library.entities.keys())}")
        
        # 检查 entity_Y 是否被删除
        if "entity_Y" in library.entities:
            print("❌ 错误: entity_Y 仍然在 EntityLibrary 中")
            return False
        else:
            print("✅ 正确: entity_Y 已从 EntityLibrary 中删除")
        
        # 检查 entity_X 是否仍然存在
        if "entity_X" in library.entities:
            print("✅ 正确: entity_X 仍然在 EntityLibrary 中")
        else:
            print("❌ 错误: entity_X 不在 EntityLibrary 中")
            return False
        
        # 测试保存和加载
        print("\n3. 测试保存和加载...")
        save_success = library.save_to_path(temp_dir)
        if save_success:
            print("✅ 保存成功")
        else:
            print("❌ 保存失败")
            return False
        
        # 创建新的 EntityLibrary 并加载
        library2 = EntityLibrary(data_path=temp_dir)
        load_success = library2.load_from_path(temp_dir)
        
        if load_success:
            print(f"   加载后实体数量: {len(library2.entities)}")
            print(f"   加载后实体列表: {list(library2.entities.keys())}")
            
            if len(library2.entities) == 2:  # entity_X 和 entity_Z
                print("✅ 正确: 加载后实体数量正确")
            else:
                print(f"❌ 错误: 期望 2 个实体，实际 {len(library2.entities)} 个")
                return False
        else:
            print("❌ 加载失败")
            return False
        
        return True
        
    finally:
        # 清理临时目录
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"\n清理临时目录: {temp_dir}")

def compare_entity_resolution_and_listener():
    """比较 EntityResolutionService 和 EntityMergeListener 的实现差异"""
    print("\n=== 比较 EntityResolutionService 和 EntityMergeListener ===")
    
    # 检查 EntityResolutionService.on_entity_merged 的实现
    print("\n1. EntityResolutionService.on_entity_merged 逻辑:")
    print("   - 检查源实体是否在 EntityLibrary 中")
    print("   - 删除 name_to_entity 中映射到 source_id 的所有条目")
    print("   - 从 embeddings 中删除 source_id")
    print("   - 从 entities 中删除 source_id")
    print("   - 将 source_id 添加为 target_id 的别名")
    
    # 检查 EntityMergeListener.on_entity_merged 的实现
    print("\n2. EntityMergeListener.on_entity_merged 逻辑:")
    print("   - 检查目标实体是否在 EntityLibrary 中，如果不在则添加")
    print("   - 将源实体ID添加为目标实体的别名")
    print("   - 调用 _delete_source_entity_from_memory 删除源实体")
    print("   - _delete_source_entity_from_memory 会删除 entities、name_to_entity 和 embeddings")
    
    print("\n3. 关键差异:")
    print("   - EntityResolutionService 先删除源实体，然后添加别名")
    print("   - EntityMergeListener 先添加别名，然后删除源实体")
    print("   - EntityMergeListener 会检查目标实体是否存在，如果不存在则创建")
    print("   - EntityResolutionService 假设目标实体已经存在")
    
    return True

def main():
    """主函数"""
    print("开始诊断 EntityLibrary 与 KG_data 实体数量不一致问题")
    print("=" * 60)
    
    # 运行测试
    test1_success = test_entity_resolution_service_on_entity_merged()
    test2_success = test_entity_merge_listener()
    compare_entity_resolution_and_listener()
    
    print("\n" + "=" * 60)
    print("诊断结果:")
    
    if test1_success and test2_success:
        print("✅ 两个 on_entity_merged 方法都能正确删除源实体")
        print("\n可能的问题原因:")
        print("1. EntityMergeListener 没有被注册到 EventBus")
        print("2. 只有 EntityResolutionService 被注册，但它的 on_entity_merged 可能有问题")
        print("3. 实体合并事件可能没有正确发布")
        print("4. 可能存在时序问题或竞态条件")
    else:
        print("❌ 发现 on_entity_merged 方法实现问题")
        if not test1_success:
            print("   - EntityResolutionService.on_entity_merged 有问题")
        if not test2_success:
            print("   - EntityMergeListener.on_entity_merged 有问题")
    
    print("\n建议的解决方案:")
    print("1. 确保 EntityResolutionService 被正确注册到 EventBus")
    print("2. 修改 EntityMergeListener 实现 get_subscribed_events 和 handle_event 方法")
    print("3. 或者，修改 EntityResolutionService.on_entity_merged 确保正确删除源实体")
    print("4. 添加日志记录以跟踪实体合并事件的处理过程")

if __name__ == "__main__":
    main()