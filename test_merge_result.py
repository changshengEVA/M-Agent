#!/usr/bin/env python3
"""
测试实体合并结果的脚本
验证重构后的KGManager合并功能是否正确工作
"""

import os
import sys
import json
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory.memory_sys import KGManager
from memory.memory_sys.storage.entity_storage import EntityStorage
from memory.memory_sys.storage.relation_storage import RelationStorage

def test_merge_result():
    """测试合并结果"""
    print("=== 测试实体合并结果 ===")
    
    # 初始化KGManager
    kg_data_dir = "data/memory/test3/kg_data"
    kg_manager = KGManager(kg_data_dir=kg_data_dir, workflow_id="test3")
    
    # 直接使用存储模块获取实体和关系
    entity_storage = EntityStorage(Path(kg_data_dir) / "entity")
    relation_storage = RelationStorage(Path(kg_data_dir) / "relation")
    
    # 1. 检查实体数量
    entity_files = entity_storage.get_all_entity_files()
    entity_names = [f.stem for f in entity_files]
    print(f"1. 当前实体数量: {len(entity_names)}")
    print(f"   实体列表: {entity_names}")
    
    # 2. 检查Peking_University实体
    print("\n2. 检查Peking_University实体:")
    peking_entity = entity_storage.load_entity("Peking_University")
    if peking_entity:
        print(f"   - 实体ID: {peking_entity['id']}")
        print(f"   - 类型: {peking_entity['type']}")
        print(f"   - 来源数量: {len(peking_entity['sources'])}")
        print(f"   - 特征数量: {len(peking_entity['features'])}")
        print(f"   - 属性数量: {len(peking_entity['attributes'])}")
        
        # 显示来源详情
        print(f"   - 来源详情:")
        for i, source in enumerate(peking_entity['sources']):
            print(f"     来源{i+1}: dialogue_id={source['dialogue_id']}, episode_id={source['episode_id']}")
    else:
        print("   - Peking_University实体不存在!")
    
    # 3. 检查北大实体是否已删除
    print("\n3. 检查北大实体是否已删除:")
    beida_entity = entity_storage.load_entity("北大")
    if beida_entity:
        print("   - 北大实体仍然存在!")
    else:
        print("   - 北大实体已成功删除")
    
    # 4. 检查关系
    print("\n4. 检查关系:")
    relation_files = relation_storage.get_all_relation_files()
    relation_ids = [f.stem for f in relation_files]
    print(f"   关系数量: {len(relation_ids)}")
    
    # 检查是否有关系引用北大实体
    beida_in_relations = False
    for rel_file in relation_files:
        relation = relation_storage.load_relation(rel_file)
        if relation:
            if relation['subject'] == '北大' or relation['object'] == '北大':
                beida_in_relations = True
                print(f"   - 发现关系 {rel_file.stem} 引用北大实体: {relation['subject']} -> {relation['relation']} -> {relation['object']}")
    
    if not beida_in_relations:
        print("   - 没有关系引用北大实体（正确）")
    
    # 5. 检查启元实验室与Peking_University的关系
    print("\n5. 检查启元实验室与Peking_University的关系:")
    for rel_file in relation_files:
        relation = relation_storage.load_relation(rel_file)
        if relation and relation['object'] == 'Peking_University':
            print(f"   - 发现关系: {relation['subject']} -> {relation['relation']} -> {relation['object']}")
            print(f"     置信度: {relation['confidence']}")
            print(f"     来源数量: {len(relation['sources'])}")
    
    # 6. 使用KGManager的get_stats方法获取统计信息
    print("\n6. 使用KGManager获取统计信息:")
    stats = kg_manager.get_stats()
    if stats.get('success', False):
        print(f"   - 实体总数: {stats.get('entity_count', 0)}")
        print(f"   - 关系总数: {stats.get('relation_count', 0)}")
        print(f"   - 特征总数: {stats.get('feature_count', 0)}")
        print(f"   - 属性总数: {stats.get('attribute_count', 0)}")
    else:
        print(f"   - 获取统计信息失败: {stats.get('error', '未知错误')}")
    
    print("\n=== 测试完成 ===")
    return True

if __name__ == "__main__":
    try:
        test_merge_result()
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)