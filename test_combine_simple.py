#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简单测试实体合并功能
"""

import os
import sys
import json
import shutil
from pathlib import Path

# 直接导入KGManager
sys.path.insert(0, str(Path(__file__).parent))

from memory.memory_sys.kg_manager import KGManager

def main():
    print("=" * 60)
    print("简单测试实体合并功能")
    print("=" * 60)
    
    # 初始化KG管理器
    test_dir = Path("data/memory/test_combine_simple")
    kg_manager = KGManager(test_dir, workflow_id="test_combine_simple")
    
    # 清理测试目录（如果存在）
    if test_dir.exists():
        shutil.rmtree(test_dir)
    
    # 创建测试实体A
    entity_a_data = {
        "id": "entity_a",
        "type": "person",
        "confidence": 0.8,
        "sources": [{"dialogue_id": "dlg_1", "episode_id": "ep_1", "generated_at": "2026-01-27T00:00:00Z"}],
        "features": [
            {
                "entity_id": "entity_a",
                "feature": "特征A",
                "scene_id": "scene_1",
                "sources": [{"dialogue_id": "dlg_1", "episode_id": "ep_1", "generated_at": "2026-01-27T00:00:00Z", "scene_id": "scene_1"}]
            }
        ],
        "attributes": [
            {
                "entity": "entity_a",
                "field": "age",
                "value": "25",
                "confidence": 0.9,
                "sources": [{"dialogue_id": "dlg_1", "episode_id": "ep_1", "generated_at": "2026-01-27T00:00:00Z"}]
            }
        ]
    }
    
    # 创建测试实体B
    entity_b_data = {
        "id": "entity_b",
        "type": "developer",
        "confidence": 0.9,
        "sources": [{"dialogue_id": "dlg_2", "episode_id": "ep_1", "generated_at": "2026-01-27T00:01:00Z"}],
        "features": [
            {
                "entity_id": "entity_b",
                "feature": "特征B",
                "scene_id": "scene_2",
                "sources": [{"dialogue_id": "dlg_2", "episode_id": "ep_1", "generated_at": "2026-01-27T00:01:00Z", "scene_id": "scene_2"}]
            }
        ],
        "attributes": [
            {
                "entity": "entity_b",
                "field": "location",
                "value": "北京",
                "confidence": 0.8,
                "sources": [{"dialogue_id": "dlg_2", "episode_id": "ep_1", "generated_at": "2026-01-27T00:01:00Z"}]
            }
        ]
    }
    
    # 确保目录存在
    kg_manager.entity_dir.mkdir(parents=True, exist_ok=True)
    kg_manager.relation_dir.mkdir(parents=True, exist_ok=True)
    
    # 保存实体文件
    entity_a_file = kg_manager.entity_dir / "entity_a.json"
    entity_b_file = kg_manager.entity_dir / "entity_b.json"
    
    with open(entity_a_file, 'w', encoding='utf-8') as f:
        json.dump(entity_a_data, f, ensure_ascii=False, indent=2)
    
    with open(entity_b_file, 'w', encoding='utf-8') as f:
        json.dump(entity_b_data, f, ensure_ascii=False, indent=2)
    
    print(f"创建测试实体A: {entity_a_file}")
    print(f"创建测试实体B: {entity_b_file}")
    
    # 创建测试关系
    relation_data = {
        "subject": "entity_b",
        "relation": "knows",
        "object": "entity_a",
        "confidence": 0.85,
        "sources": [{"dialogue_id": "dlg_3", "episode_id": "ep_1", "generated_at": "2026-01-27T00:02:00Z"}]
    }
    
    # 保存关系
    kg_manager._save_relation(relation_data, {
        "dialogue_id": "dlg_3",
        "episode_id": "ep_1",
        "generated_at": "2026-01-27T00:02:00Z"
    })
    
    print("创建测试关系: entity_b -> knows -> entity_a")
    
    # 合并实体
    print("\n合并实体: entity_b -> entity_a")
    result = kg_manager.combine_entity("entity_a", "entity_b")
    
    print(f"成功: {result.get('success', False)}")
    print(f"消息: {result.get('message', '无消息')}")
    
    if result.get('success', False):
        stats = result.get('stats', {})
        print(f"\n合并统计:")
        for key, value in stats.items():
            print(f"  {key}: {value}")
        
        # 检查结果
        print(f"\n检查结果:")
        print(f"  实体B文件是否存在: {entity_b_file.exists()} (应为False)")
        
        if entity_a_file.exists():
            with open(entity_a_file, 'r', encoding='utf-8') as f:
                merged = json.load(f)
            
            print(f"  合并后实体A的特征数量: {len(merged.get('features', []))} (应为2)")
            print(f"  合并后实体A的属性数量: {len(merged.get('attributes', []))} (应为2)")
            print(f"  合并后实体A的来源数量: {len(merged.get('sources', []))} (应为2)")
            print(f"  合并后实体A的类型: {merged.get('type')} (应为'developer')")
            print(f"  合并后实体A的置信度: {merged.get('confidence')} (应为0.9)")
        
        # 检查关系
        relation_files = list(kg_manager.relation_dir.glob("*.json"))
        print(f"  关系文件数量: {len(relation_files)} (应为1)")
        
        for rel_file in relation_files:
            with open(rel_file, 'r', encoding='utf-8') as f:
                rel = json.load(f)
            print(f"  关系: {rel.get('subject')} -> {rel.get('relation')} -> {rel.get('object')} (应为entity_a -> knows -> entity_a)")
    
    print("\n" + "=" * 60)
    print("测试完成!")
    print("=" * 60)
    
    # 清理测试目录
    if test_dir.exists():
        shutil.rmtree(test_dir)
        print(f"已清理测试目录: {test_dir}")

if __name__ == "__main__":
    main()