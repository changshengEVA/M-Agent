#!/usr/bin/env python3
"""
测试特征数据结构
"""

import json
from pathlib import Path

# 读取一个实体文件
entity_file = Path("data/memory/testrt/kg_data/entity/New_York.json")
with open(entity_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

print("实体文件结构:")
print(json.dumps(data, indent=2, ensure_ascii=False))

print("\n\n特征数据结构分析:")
features = data.get("features", [])
for i, feature in enumerate(features):
    print(f"\n特征 {i}:")
    print(f"  文本: {feature.get('feature')}")
    print(f"  scene_id: {feature.get('scene_id')}")
    print(f"  episode_id: {feature.get('episode_id')}")
    print(f"  dialogue_id: {feature.get('dialogue_id')}")
    print(f"  sources字段: {feature.get('sources', '不存在')}")
    
print("\n\n实体sources字段:")
print(f"  sources: {data.get('sources', [])}")