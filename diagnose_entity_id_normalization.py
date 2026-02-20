#!/usr/bin/env python3
"""
诊断实体ID规范化问题
"""

import sys
import os
sys.path.append('.')

def test_entity_id_normalization():
    """测试实体ID规范化问题"""
    print("🔍 诊断实体ID规范化问题")
    print("=" * 60)
    
    # 测试 EntityRepository._sanitize_entity_name 方法
    from memory.memory_core.persistence.entity_repository import EntityRepository
    from pathlib import Path
    
    # 创建临时 EntityRepository 实例来测试 _sanitize_entity_name 方法
    # 注意：_sanitize_entity_name 是私有方法，我们需要通过实例访问
    temp_dir = Path("temp_test_dir")
    repo = EntityRepository(temp_dir)
    
    # 测试用例
    test_cases = [
        "AIRE Ancient Baths",
        "Airbus A350",
        "Anya Taylor-Joy",
        "Appalachian Trail",
        "A'Pieu raspberry vinegar treatment",
        "Atlantic Ocean",
        "Balboa Park",
        "Banff National Park"
    ]
    
    print("📋 测试 EntityRepository._sanitize_entity_name() 方法:")
    print("-" * 60)
    
    for original_id in test_cases:
        try:
            # 使用反射调用私有方法
            sanitized = repo._sanitize_entity_name(original_id)
            print(f"  '{original_id}' → '{sanitized}'")
        except Exception as e:
            print(f"  ❌ 错误处理 '{original_id}': {e}")
    
    print("\n📋 分析错误日志中的实体ID差异:")
    print("-" * 60)
    
    # 从之前的错误日志中提取示例
    kg_entities = [
        "A'Pieu_raspberry_vinegar_treatment",
        "AIRE_Ancient_Baths", 
        "Airbus_A350",
        "Anya_Taylor-Joy",
        "Appalachian_Trail",
        "Aqua_Dome",
        "Art_Basel",
        "Atlantic_Ocean",
        "Balboa_Park",
        "Banff_National_Park"
    ]
    
    library_entities = [
        "A'Pieu raspberry vinegar treatment",
        "AIRE Ancient Baths",
        "Airbus A350", 
        "Anya Taylor-Joy",
        "Appalachian Trail",
        "Aqua Dome",
        "Art Basel",
        "Atlantic Ocean",
        "Balboa Park",
        "Banff National Park"
    ]
    
    print("KG 实体ID (带下划线) ↔ EntityLibrary 实体ID (带空格):")
    for kg_id, lib_id in zip(kg_entities, library_entities):
        # 尝试将 library 实体ID规范化，看是否匹配 KG 实体ID
        try:
            normalized_lib_id = repo._sanitize_entity_name(lib_id)
            match = normalized_lib_id == kg_id
            symbol = "✅" if match else "❌"
            print(f"  {symbol} KG: '{kg_id}' ←→ Library: '{lib_id}' (规范化后: '{normalized_lib_id}')")
        except Exception as e:
            print(f"  ❌ 错误: {e}")
    
    print("\n💡 结论:")
    print("-" * 60)
    print("1. EntityRepository._sanitize_entity_name() 将空格替换为下划线")
    print("2. KG 从文件名获取实体ID，得到带下划线的ID")
    print("3. EntityLibrary 可能从其他来源获取实体ID，得到带空格的原始ID")
    print("4. 这导致相同的实体被当作两个不同的实体")
    
    print("\n🔧 建议的解决方案:")
    print("1. 在 _align_entity_library_with_kg 方法中比较实体ID时，进行规范化处理")
    print("2. 或者统一实体ID的创建和存储规范")
    print("3. 或者在 EntityLibrary 加载实体时进行ID规范化")
    
    # 清理
    try:
        import shutil
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
    except:
        pass

def test_kg_base_entity_loading():
    """测试 KGBase 实体加载过程"""
    print("\n\n🔍 测试 KGBase 实体加载过程")
    print("=" * 60)
    
    try:
        from memory.memory_core.core.kg_base import KGBase
        from pathlib import Path
        import tempfile
        import json
        
        # 创建临时目录和实体文件
        temp_dir = tempfile.mkdtemp(prefix="test_kg_loading_")
        entity_dir = Path(temp_dir) / "entities"
        entity_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建一些实体文件（使用规范化后的ID）
        test_entities = [
            ("AIRE_Ancient_Baths", "AIRE Ancient Baths"),
            ("Airbus_A350", "Airbus A350"),
            ("Anya_Taylor-Joy", "Anya Taylor-Joy")
        ]
        
        for file_id, display_name in test_entities:
            entity_file = entity_dir / f"{file_id}.json"
            entity_data = {
                "id": file_id,
                "name": display_name,
                "type": "location",
                "metadata": {"source": "test"}
            }
            entity_file.write_text(json.dumps(entity_data, indent=2))
            print(f"✅ 创建实体文件: {entity_file.name}")
        
        # 创建 KGBase 实例
        kg_base = KGBase(
            entity_dir=str(entity_dir),
            relation_dir=str(Path(temp_dir) / "relations"),
            event_bus=None
        )
        
        # 测试 list_entity_ids
        print(f"\n📋 KGBase.list_entity_ids() 返回:")
        entity_ids = kg_base.list_entity_ids()
        for entity_id in entity_ids:
            print(f"  - '{entity_id}'")
        
        # 测试加载实体
        print(f"\n📋 加载实体数据:")
        for entity_id in entity_ids:
            success, entity_data = kg_base.repos.entity.load(entity_id)
            if success:
                print(f"  ✅ '{entity_id}': name='{entity_data.get('name')}'")
            else:
                print(f"  ❌ 加载失败: '{entity_id}'")
        
        print(f"\n💡 观察:")
        print("1. 文件系统中的实体ID是规范化的（带下划线）")
        print("2. 实体数据中的 'name' 字段可能是原始名称（带空格）")
        print("3. 这可能导致 EntityLibrary 使用 'name' 字段作为实体ID")
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 清理
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except:
            pass

def main():
    """主函数"""
    print("🚀 开始诊断实体ID规范化问题")
    print("=" * 60)
    
    try:
        test_entity_id_normalization()
        test_kg_base_entity_loading()
        
        print("\n" + "=" * 60)
        print("🎯 诊断完成！")
        
    except Exception as e:
        print(f"\n❌ 诊断失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())