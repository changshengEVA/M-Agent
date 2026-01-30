#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试实体库功能
"""

import json
import tempfile
import os
from pathlib import Path
from memory.memory_sys.storage.entity_library import EntityLibrary, EntityRecord

def test_entity_record():
    """测试实体记录类"""
    print("测试 EntityRecord 类...")
    
    # 创建实体记录
    record = EntityRecord(
        id="测试实体",
        alias_names=["别名1", "别名2"],
        embedding=[0.1, 0.2, 0.3]
    )
    
    # 测试to_dict
    record_dict = record.to_dict()
    assert record_dict["ID"] == "测试实体"
    assert record_dict["alias_names"] == ["别名1", "别名2"]
    assert record_dict["embedding"] == [0.1, 0.2, 0.3]
    
    # 测试from_dict
    record2 = EntityRecord.from_dict(record_dict)
    assert record2.id == "测试实体"
    assert record2.alias_names == ["别名1", "别名2"]
    assert record2.embedding == [0.1, 0.2, 0.3]
    
    # 测试get_all_names
    names = record.get_all_names()
    assert "测试实体" in names
    assert "别名1" in names
    assert "别名2" in names
    
    # 测试add_alias
    assert record.add_alias("别名3") == True
    assert "别名3" in record.alias_names
    assert record.add_alias("别名3") == False  # 已存在
    
    print("EntityRecord 测试通过!")

def test_entity_library():
    """测试实体库类"""
    print("\n测试 EntityLibrary 类...")
    
    # 创建临时目录
    with tempfile.TemporaryDirectory() as temp_dir:
        library_dir = Path(temp_dir) / "entity_library"
        
        # 初始化实体库
        library = EntityLibrary(library_dir)
        
        # 测试添加实体
        assert library.add_entity("实体1", [0.1, 0.2, 0.3]) == True
        assert library.add_entity("实体1", [0.4, 0.5, 0.6]) == False  # 已存在
        
        # 测试实体存在性
        assert library.entity_exists("实体1") == True
        assert library.entity_exists("不存在的实体") == False
        
        # 测试获取实体ID
        assert library.get_entity_id("实体1") == "实体1"
        
        # 测试添加别名
        assert library.add_alias("实体1", "别名1") == True
        assert library.entity_exists("别名1") == True
        assert library.get_entity_id("别名1") == "实体1"
        
        # 测试添加第二个实体
        library.add_entity("实体2", [0.9, 0.8, 0.7])
        
        # 测试相似度查找（由于嵌入向量不同，应该找不到相似实体）
        similar = library.find_similar_entities("实体1", [0.1, 0.2, 0.3], threshold=0.99)
        assert len(similar) == 0
        
        # 测试获取所有实体
        all_entities = library.get_all_entities()
        assert len(all_entities) == 2
        
        # 测试保存和重新加载
        assert library.save() == True
        
        # 检查文件是否创建
        entity1_file = library_dir / "实体1.json"
        entity2_file = library_dir / "实体2.json"
        assert entity1_file.exists()
        assert entity2_file.exists()
        
        # 创建新的库实例加载数据
        library2 = EntityLibrary(library_dir)
        assert library2.entity_exists("实体1") == True
        assert library2.entity_exists("别名1") == True
        assert library2.get_entity_id("别名1") == "实体1"
        
        print("EntityLibrary 测试通过!")

def test_kg_manager_integration():
    """测试KGManager集成"""
    print("\n测试 KGManager 集成...")
    
    try:
        from memory.memory_sys.kg_manager import KGManager
        
        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        kg_data_dir = Path(temp_dir) / "test_kg_data"
        
        # 初始化KGManager
        kg_manager = KGManager(kg_data_dir, "test_workflow")
        
        # 测试实体库初始化
        assert hasattr(kg_manager, 'entity_library')
        assert kg_manager.entity_library is not None
        
        # 测试模型加载方法存在
        assert hasattr(kg_manager, '_load_models')
        assert hasattr(kg_manager, '_get_entity_embedding')
        assert hasattr(kg_manager, '_post_process_entities')
        
        print("KGManager 集成测试通过!")
        
        # 清理临时目录
        import shutil
        shutil.rmtree(temp_dir)
        
    except ImportError as e:
        print(f"导入错误: {e}")
    except Exception as e:
        print(f"测试错误: {e}")

def main():
    """主测试函数"""
    print("开始测试实体库功能...")
    
    test_entity_record()
    test_entity_library()
    test_kg_manager_integration()
    
    print("\n所有测试完成!")

if __name__ == "__main__":
    main()