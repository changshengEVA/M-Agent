#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 EntityLibrary 保存功能
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.memory_core.services_bank.entity_resolution.library import EntityLibrary

def test_save_and_load():
    """测试保存和加载功能"""
    print("=== 测试 EntityLibrary 保存功能 ===")
    
    # 创建测试目录
    test_dir = Path("data/test_entity_library")
    test_dir.mkdir(parents=True, exist_ok=True)
    
    # 创建一个简单的嵌入函数
    def dummy_embed_func(text):
        return [0.1, 0.2, 0.3] * 10  # 30维向量
    
    # 创建 EntityLibrary
    library = EntityLibrary(embed_func=dummy_embed_func, data_path=str(test_dir))
    
    # 添加一些测试实体
    print("1. 添加测试实体...")
    library.add_entity(
        entity_id="test_entity_1",
        canonical_name="测试实体1",
        aliases=["别名1", "别名2"],
        entity_type="PERSON"
    )
    
    library.add_entity(
        entity_id="test_entity_2",
        canonical_name="测试实体2",
        aliases=["别名3"],
        entity_type="LOCATION"
    )
    
    # 为实体生成embedding
    print("2. 生成实体embedding...")
    library.init_entity_embedding("test_entity_1")
    library.init_entity_embedding("test_entity_2")
    
    # 获取统计信息
    stats = library.get_stats()
    print(f"3. 添加实体后统计: {stats}")
    
    # 保存到文件
    print("4. 保存到文件...")
    save_success = library.save_to_path(str(test_dir))
    print(f"   保存结果: {save_success}")
    
    # 检查文件是否创建
    if save_success:
        json_files = list(test_dir.glob("*.json"))
        print(f"   创建的JSON文件数量: {len(json_files)}")
        for f in json_files:
            print(f"   - {f.name}")
    
    # 创建新的 EntityLibrary 实例并加载数据
    print("5. 创建新的 EntityLibrary 并加载数据...")
    new_library = EntityLibrary(embed_func=dummy_embed_func, data_path=str(test_dir))
    
    # 检查加载后的统计信息
    new_stats = new_library.get_stats()
    print(f"6. 加载后统计: {new_stats}")
    
    # 验证数据是否一致
    if stats["entity_count"] == new_stats["entity_count"]:
        print("✅ 测试通过: 实体数量一致")
    else:
        print(f"❌ 测试失败: 实体数量不一致 (原始: {stats['entity_count']}, 加载后: {new_stats['entity_count']})")
    
    # 清理测试目录
    import shutil
    if test_dir.exists():
        shutil.rmtree(test_dir)
        print(f"7. 清理测试目录: {test_dir}")
    
    print("=== 测试完成 ===")

if __name__ == "__main__":
    test_save_and_load()