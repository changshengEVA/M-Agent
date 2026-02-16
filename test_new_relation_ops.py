#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试新添加的关系操作功能
"""

import tempfile
import shutil
import logging
from pathlib import Path

# 设置日志
logging.basicConfig(level=logging.INFO)

# 导入核心模块
from memory.memory_core.core.kg_base import KGBase
from memory.memory_core.core.repo_context import RepoContext

def test_new_relation_operations():
    """测试三个新功能"""
    # 创建临时目录
    temp_dir = tempfile.mkdtemp()
    entity_dir = Path(temp_dir) / "entities"
    relation_dir = Path(temp_dir) / "relations"
    
    print(f"临时目录: {temp_dir}")
    
    try:
        # 初始化 KGBase
        kg = KGBase(entity_dir=entity_dir, relation_dir=relation_dir)
        
        # 1. 创建两个实体
        print("\n1. 创建两个实体...")
        result1 = kg.add_entity("entity_a", entity_type="person")
        print(f"   创建 entity_a: {result1['success']}")
        
        result2 = kg.add_entity("entity_b", entity_type="person")
        print(f"   创建 entity_b: {result2['success']}")
        
        # 2. 添加一些关系
        print("\n2. 添加关系...")
        result3 = kg.add_relation("entity_a", "knows", "entity_b", confidence=0.8)
        print(f"   添加关系 entity_a knows entity_b: {result3['success']}")
        
        result4 = kg.add_relation("entity_a", "likes", "entity_b", confidence=0.9)
        print(f"   添加关系 entity_a likes entity_b: {result4['success']}")
        
        result5 = kg.add_relation("entity_b", "knows", "entity_a", confidence=0.7)
        print(f"   添加关系 entity_b knows entity_a: {result5['success']}")
        
        # 3. 测试 find_relations_by_entities
        print("\n3. 测试 find_relations_by_entities...")
        find_result = kg.find_relations_by_entities("entity_a", "entity_b")
        print(f"   成功: {find_result['success']}")
        print(f"   关系数量: {find_result['details']['relation_count']}")
        print(f"   关系列表: {[r.get('relation') for r in find_result['details']['relations']]}")
        
        # 4. 测试 delete_relation (删除一条关系)
        print("\n4. 测试 delete_relation...")
        # 首先获取一条关系ID
        if find_result['details']['relations']:
            relation_id = find_result['details']['relations'][0].get('id')
            if relation_id:
                delete_result = kg.delete_relation(relation_id)
                print(f"   删除关系 {relation_id}: {delete_result['success']}")
                print(f"   是否改变: {delete_result['changed']}")
        
        # 再次查找关系，应该少一条
        find_result2 = kg.find_relations_by_entities("entity_a", "entity_b")
        print(f"   删除后关系数量: {find_result2['details']['relation_count']}")
        
        # 5. 测试 delete_all_relations_by_entities
        print("\n5. 测试 delete_all_relations_by_entities...")
        delete_all_result = kg.delete_all_relations_by_entities("entity_a", "entity_b")
        print(f"   成功: {delete_all_result['success']}")
        print(f"   删除数量: {delete_all_result['details']['deleted_count']}")
        
        # 再次查找关系，应该为零
        find_result3 = kg.find_relations_by_entities("entity_a", "entity_b")
        print(f"   删除所有后关系数量: {find_result3['details']['relation_count']}")
        
        # 6. 测试不存在的实体
        print("\n6. 测试不存在的实体...")
        invalid_result = kg.find_relations_by_entities("nonexistent", "entity_a")
        print(f"   查找不存在的实体: {invalid_result['success']} (应为 False)")
        
        # 7. 测试删除不存在的实体间关系
        delete_invalid = kg.delete_all_relations_by_entities("nonexistent", "entity_a")
        print(f"   删除不存在的实体间关系: {delete_invalid['success']} (应为 False)")
        
        # 8. 测试删除不存在的单个关系
        delete_single = kg.delete_relation("nonexistent-relation-id")
        print(f"   删除不存在的单个关系: {delete_single['success']} (应为 False)")
        
        print("\n所有测试完成!")
        
    finally:
        # 清理临时目录
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"\n清理临时目录: {temp_dir}")

if __name__ == "__main__":
    test_new_relation_operations()