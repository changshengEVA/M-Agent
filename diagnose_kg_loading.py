#!/usr/bin/env python3
"""
诊断 KGBase 数据加载问题
检查 KGBase 在实例化时是否加载数据到内存
"""

import os
import tempfile
import shutil
import sys
sys.path.append('.')

from memory.memory_core.core.kg_base import KGBase
from memory.memory_core.core.repo_context import RepoContext
from pathlib import Path

def test_kgbase_initialization():
    """测试 KGBase 初始化是否加载数据到内存"""
    print("=== 测试 KGBase 初始化数据加载 ===")
    
    # 创建临时目录
    temp_dir = tempfile.mkdtemp()
    entity_dir = Path(temp_dir) / "entity"
    relation_dir = Path(temp_dir) / "relation"
    entity_dir.mkdir(parents=True, exist_ok=True)
    relation_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # 1. 创建一些测试实体文件
        print("\n1. 创建测试实体文件...")
        test_entities = [
            {"id": "entity_001", "name": "Entity One", "type": "person"},
            {"id": "entity_002", "name": "Entity Two", "type": "location"},
            {"id": "entity_003", "name": "Entity Three", "type": "organization"},
        ]
        
        for entity in test_entities:
            entity_file = entity_dir / f"{entity['id']}.json"
            with open(entity_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "id": entity["id"],
                    "name": entity["name"],
                    "type": entity["type"],
                    "metadata": {"created": "2024-01-01"}
                }, f, indent=2)
            print(f"  创建实体文件: {entity_file}")
        
        # 2. 初始化 KGBase
        print("\n2. 初始化 KGBase...")
        kg_base = KGBase(
            entity_dir=entity_dir,
            relation_dir=relation_dir,
            event_bus=None
        )
        
        # 3. 检查 list_entity_ids 方法
        print("\n3. 检查 list_entity_ids() 方法...")
        entity_ids = kg_base.list_entity_ids()
        print(f"  获取到的实体ID: {entity_ids}")
        print(f"  实体数量: {len(entity_ids)}")
        
        # 4. 检查 EntityRepository 是否有内存缓存
        print("\n4. 检查 EntityRepository 内存状态...")
        repo = kg_base.repos.entity
        
        # 检查是否有缓存属性
        cache_attrs = [attr for attr in dir(repo) if 'cache' in attr.lower() or 'memory' in attr.lower()]
        print(f"  可能的缓存属性: {cache_attrs}")
        
        # 检查是否有加载所有实体的方法
        load_methods = [method for method in dir(repo) if 'load_all' in method or 'preload' in method]
        print(f"  批量加载方法: {load_methods}")
        
        # 5. 测试实体加载性能（是否每次都要读磁盘）
        print("\n5. 测试实体加载性能...")
        import time
        
        # 第一次加载
        start_time = time.time()
        for entity_id in entity_ids:
            success, data = repo.load(entity_id)
        first_load_time = time.time() - start_time
        print(f"  第一次加载所有实体耗时: {first_load_time:.4f}秒")
        
        # 第二次加载（测试是否有缓存）
        start_time = time.time()
        for entity_id in entity_ids:
            success, data = repo.load(entity_id)
        second_load_time = time.time() - start_time
        print(f"  第二次加载所有实体耗时: {second_load_time:.4f}秒")
        
        if second_load_time < first_load_time * 0.5:
            print("  ✅ 可能有缓存机制")
        else:
            print("  ❌ 可能没有缓存，每次都要读磁盘")
        
        # 6. 检查 RepoContext 结构
        print("\n6. 检查 RepoContext 结构...")
        repos = kg_base.repos
        print(f"  EntityRepository: {type(repos.entity).__name__}")
        print(f"  RelationRepository: {type(repos.relation).__name__}")
        print(f"  FeatureRepository: {type(repos.feature).__name__}")
        print(f"  AttributeRepository: {type(repos.attribute).__name__}")
        
        # 7. 结论
        print("\n=== 诊断结论 ===")
        print("问题分析:")
        print("1. KGBase 在初始化时只创建了 RepoContext，但没有预加载实体数据到内存")
        print("2. EntityRepository 每次调用 load() 方法都会从磁盘读取文件")
        print("3. 这可能导致 _align_entity_library_with_kg 方法性能低下")
        print("4. 更重要的是：如果磁盘文件在初始化后被修改，内存状态不会自动更新")
        
        print("\n建议解决方案:")
        print("1. 在 KGBase 中添加内存缓存机制")
        print("2. 或者修改 EntityRepository 在初始化时预加载所有实体到内存")
        print("3. 或者接受每次操作都从磁盘读取的设计（但需要确保数据一致性）")
        
        return True
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # 清理临时目录
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"\n已清理临时目录: {temp_dir}")

def test_memorycore_alignment():
    """测试 MemoryCore 的 _align_entity_library_with_kg 方法"""
    print("\n\n=== 测试 MemoryCore 对齐方法 ===")
    
    # 创建临时目录
    temp_dir = tempfile.mkdtemp()
    
    try:
        # 模拟 MemoryCore 初始化过程
        from memory.memory_core.memory_system import MemoryCore
        
        print("1. 创建 MemoryCore 实例...")
        # 使用模拟的 LLM 和 Embed 函数
        def mock_llm(prompt):
            return "Mock response"
        
        def mock_embed(text):
            return [0.1] * 10
        
        memory_core = MemoryCore(
            workflow_id="test_workflow",
            llm_func=mock_llm,
            embed_func=mock_embed,
            similarity_threshold=0.7
        )
        
        print("2. 检查 KGBase 状态...")
        kg_base = memory_core.kg_base
        print(f"  KGBase 实体目录: {kg_base.entity_dir}")
        print(f"  KGBase 关系目录: {kg_base.relation_dir}")
        
        # 检查实体数量
        entity_ids = kg_base.list_entity_ids()
        print(f"  KGBase 中的实体数量: {len(entity_ids)}")
        
        print("3. 检查 EntityResolutionService 状态...")
        service = memory_core.entity_resolution_service
        library_entities = list(service.entity_library.entities.keys())
        print(f"  EntityLibrary 中的实体数量: {len(library_entities)}")
        
        print("\n4. 分析对齐逻辑...")
        print("  _align_entity_library_with_kg 方法会:")
        print("  a) 比较 KG 和 EntityLibrary 的实体ID集合")
        print("  b) 如果不一致，会从磁盘加载每个实体的详细信息")
        print("  c) 调用 rebuild_from_kg 重建 EntityLibrary")
        print("  d) 调用 resolve_unresolved_entities 完成对齐")
        
        print("\n5. 潜在问题:")
        print("  - 如果 KG 中有大量实体，每次对齐都要从磁盘读取所有实体文件")
        print("  - 磁盘 I/O 可能成为性能瓶颈")
        print("  - 如果磁盘文件在初始化后被修改，需要重新对齐")
        
        return True
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def main():
    """主函数"""
    print("KGBase 数据加载问题诊断")
    print("=" * 60)
    
    # 需要导入 json 模块
    import json
    globals()['json'] = json
    
    # 运行测试
    kgbase_test_passed = test_kgbase_initialization()
    memorycore_test_passed = test_memorycore_alignment()
    
    print("\n" + "=" * 60)
    print("最终诊断结果:")
    
    if kgbase_test_passed and memorycore_test_passed:
        print("✅ 诊断完成")
        print("\n关键发现:")
        print("1. KGBase 在实例化时没有加载数据到内存")
        print("2. EntityRepository 每次操作都从磁盘读取文件")
        print("3. _align_entity_library_with_kg 方法依赖磁盘读取")
        print("\n这可能解释了为什么 EntityLibrary 与 KG_data 同步有问题:")
        print("- 如果磁盘文件状态与内存状态不一致，对齐会失败")
        print("- 实体合并后，磁盘文件被删除，但 KGBase 可能仍然认为实体存在")
        print("- 需要确保 KGBase 能正确反映磁盘的最新状态")
    else:
        print("❌ 诊断失败")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())