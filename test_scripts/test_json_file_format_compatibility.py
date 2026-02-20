#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 JSON 文件格式兼容性

验证 _load_from_json_file 方法不再支持实体列表格式，
只支持单个实体对象格式。
"""

import os
import sys
import tempfile
import json
import shutil
from typing import List

# 添加项目根目录到路径
sys.path.append('.')

def mock_embed_func(text: str) -> List[float]:
    """模拟嵌入向量生成函数"""
    return [0.1, 0.2, 0.3, 0.4, 0.5]

def test_single_entity_format():
    """测试单个实体对象格式（应支持）"""
    print("=== 测试 1: 单个实体对象格式 ===")
    
    from memory.memory_core.services_bank.entity_resolution.library import EntityLibrary
    
    # 创建临时目录用于测试
    temp_dir = tempfile.mkdtemp()
    
    try:
        single_entity_file = os.path.join(temp_dir, "single_entity.json")
        single_entity_data = {
            "ID": "test_entity_1",
            "name": "Test Entity One",
            "alias_names": ["alias1", "alias2"],
            "embedding": [0.1, 0.2, 0.3],
            "entity_type": "person",
            "metadata": {"key": "value"},
            "resolved": True,
            "last_decision": {"type": "NEW_ENTITY", "confidence": 0.9}
        }
        
        with open(single_entity_file, 'w', encoding='utf-8') as f:
            json.dump(single_entity_data, f, ensure_ascii=False, indent=2)
        
        library = EntityLibrary(embed_func=mock_embed_func)
        success = library._load_from_json_file(single_entity_file)
        assert success, "应成功加载单个实体对象格式"
        
        entity = library.get_entity_by_name("Test Entity One")
        assert entity is not None, "应加载实体"
        assert entity.entity_id == "test_entity_1", "实体ID应匹配"
        assert entity.resolved, "解析状态应正确"
        assert entity.last_decision is not None, "解析决策应正确加载"
        assert "alias1" in entity.aliases, "别名应正确加载"
        
        print("✅ 单个实体对象格式支持正常")
        
    finally:
        # 清理临时文件
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

def test_entity_list_format():
    """测试实体列表格式（应不再支持）"""
    print("=== 测试 2: 实体列表格式 ===")
    
    from memory.memory_core.services_bank.entity_resolution.library import EntityLibrary
    
    # 创建临时目录用于测试
    temp_dir = tempfile.mkdtemp()
    
    try:
        entity_list_file = os.path.join(temp_dir, "entity_list.json")
        entity_list_data = [
            {
                "ID": "entity_1",
                "name": "Entity One",
                "alias_names": ["alias1"],
                "resolved": False
            },
            {
                "ID": "entity_2", 
                "name": "Entity Two",
                "alias_names": ["alias2"],
                "resolved": True
            }
        ]
        
        with open(entity_list_file, 'w', encoding='utf-8') as f:
            json.dump(entity_list_data, f, ensure_ascii=False, indent=2)
        
        library = EntityLibrary(embed_func=mock_embed_func)
        success = library._load_from_json_file(entity_list_file)
        assert not success, "应不再支持实体列表格式"
        
        # 验证没有实体被加载
        assert library.get_entity_count() == 0, "不应加载任何实体"
        
        print("✅ 实体列表格式不再支持")
        
    finally:
        # 清理临时文件
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

def test_invalid_format():
    """测试无效格式"""
    print("=== 测试 3: 无效格式 ===")
    
    from memory.memory_core.services_bank.entity_resolution.library import EntityLibrary
    
    # 创建临时目录用于测试
    temp_dir = tempfile.mkdtemp()
    
    try:
        # 测试无效 JSON
        invalid_file = os.path.join(temp_dir, "invalid.json")
        with open(invalid_file, 'w', encoding='utf-8') as f:
            f.write("invalid json content")
        
        library = EntityLibrary(embed_func=mock_embed_func)
        success = library._load_from_json_file(invalid_file)
        assert not success, "无效 JSON 应失败"
        
        # 测试非字典非列表格式
        non_dict_file = os.path.join(temp_dir, "non_dict.json")
        with open(non_dict_file, 'w', encoding='utf-8') as f:
            json.dump("just a string", f)
        
        library2 = EntityLibrary(embed_func=mock_embed_func)
        success = library2._load_from_json_file(non_dict_file)
        assert not success, "非字典格式应失败"
        
        # 测试空字典
        empty_dict_file = os.path.join(temp_dir, "empty_dict.json")
        with open(empty_dict_file, 'w', encoding='utf-8') as f:
            json.dump({}, f)
        
        library3 = EntityLibrary(embed_func=mock_embed_func)
        success = library3._load_from_json_file(empty_dict_file)
        assert not success, "空字典（无实体ID）应失败"
        
        print("✅ 无效格式正确处理")
        
    finally:
        # 清理临时文件
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

def test_directory_loading():
    """测试目录加载功能"""
    print("=== 测试 4: 目录加载 ===")
    
    from memory.memory_core.services_bank.entity_resolution.library import EntityLibrary
    
    # 创建临时目录用于测试
    temp_dir = tempfile.mkdtemp()
    
    try:
        test_dir = os.path.join(temp_dir, "test_dir")
        os.makedirs(test_dir, exist_ok=True)
        
        # 创建多个实体文件
        for i in range(3):
            entity_file = os.path.join(test_dir, f"entity_{i}.json")
            entity_data = {
                "ID": f"entity_{i}",
                "name": f"Entity {i}",
                "alias_names": [f"alias_{i}"],
                "resolved": i % 2 == 0,  # 交替设置解析状态
                "metadata": {"index": i}
            }
            
            with open(entity_file, 'w', encoding='utf-8') as f:
                json.dump(entity_data, f, ensure_ascii=False, indent=2)
        
        library = EntityLibrary(embed_func=mock_embed_func)
        success = library.load_from_path(test_dir)
        assert success, "应成功从目录加载"
        assert library.get_entity_count() == 3, "应加载3个实体"
        
        # 验证解析状态
        entity0 = library.get_entity_by_name("Entity 0")
        entity1 = library.get_entity_by_name("Entity 1")
        assert entity0.resolved, "entity_0 应已解析"
        assert not entity1.resolved, "entity_1 应未解析"
        
        # 验证别名
        assert "alias_0" in entity0.aliases, "别名应正确加载"
        
        print("✅ 目录加载功能正常")
        
    finally:
        # 清理临时文件
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

def test_backward_compatibility():
    """测试向后兼容性（旧字段名）"""
    print("=== 测试 5: 向后兼容性 ===")
    
    from memory.memory_core.services_bank.entity_resolution.library import EntityLibrary
    
    # 创建临时目录用于测试
    temp_dir = tempfile.mkdtemp()
    
    try:
        # 测试使用旧字段名 'id' 而不是 'ID'
        old_format_file = os.path.join(temp_dir, "old_format.json")
        old_format_data = {
            "id": "old_entity",  # 使用 'id' 而不是 'ID'
            "name": "Old Entity",
            "alias_names": ["old_alias"],
            "resolved": False
        }
        
        with open(old_format_file, 'w', encoding='utf-8') as f:
            json.dump(old_format_data, f, ensure_ascii=False, indent=2)
        
        library = EntityLibrary(embed_func=mock_embed_func)
        success = library._load_from_json_file(old_format_file)
        assert success, "应支持旧字段名 'id'"
        
        entity = library.get_entity_by_name("Old Entity")
        assert entity is not None, "应加载实体"
        assert entity.entity_id == "old_entity", "实体ID应匹配"
        
        print("✅ 向后兼容性正常")
        
    finally:
        # 清理临时文件
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

def main():
    """主测试函数"""
    print("开始测试 JSON 文件格式兼容性")
    print("=" * 60)
    
    try:
        test_single_entity_format()
        test_entity_list_format()
        test_invalid_format()
        test_directory_loading()
        test_backward_compatibility()
        
        print("=" * 60)
        print("✅ 所有 JSON 文件格式兼容性测试通过！")
        print("\n测试总结：")
        print("1. ✅ 单个实体对象格式支持正常")
        print("2. ✅ 实体列表格式不再支持（与删除的保存功能保持一致）")
        print("3. ✅ 无效格式正确处理")
        print("4. ✅ 目录加载功能正常")
        print("5. ✅ 向后兼容性正常")
        
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