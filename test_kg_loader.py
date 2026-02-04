#!/usr/bin/env python3
"""
测试三维知识图谱数据加载器
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'kg_visualization/backend'))

from enhanced_data_loader import EnhancedKGDataLoader

def test_loader():
    print("测试EnhancedKGDataLoader...")
    
    # 使用testrt memory_id
    loader = EnhancedKGDataLoader(memory_id="testrt")
    print(f"数据目录: {loader.data_dir}")
    print(f"目录是否存在: {loader.data_dir.exists()}")
    
    # 加载数据
    stats = loader.load_all_data()
    print(f"加载结果: {stats}")
    
    # 检查场景数量
    print(f"\n场景数量: {len(loader.scenes)}")
    if loader.scenes:
        print("前5个场景:")
        for i, (scene_id, scene) in enumerate(list(loader.scenes.items())[:5]):
            print(f"  {scene_id}: dialogue={scene.dialogue_id}, episode={scene.episode_id}")
    
    # 检查实体数量
    print(f"\n实体数量: {len(loader.entities)}")
    if loader.entities:
        print("前5个实体:")
        for i, (entity_id, entity) in enumerate(list(loader.entities.items())[:5]):
            print(f"  {entity_id}: type={entity.type}, features={len(entity.features)}")
    
    # 检查特征数量
    print(f"\n特征数量: {len(loader.features)}")
    
    # 检查垂直边数量
    print(f"\n垂直边数量: {len(loader.vertical_edges)}")
    
    # 获取3D图数据
    graph_data = loader.get_3d_graph_data()
    print(f"\n3D图数据统计:")
    print(f"  实体节点: {len(graph_data.get('entities', []))}")
    print(f"  特征节点: {len(graph_data.get('features', []))}")
    print(f"  场景节点: {len(graph_data.get('scenes', []))}")
    
    return stats

if __name__ == "__main__":
    test_loader()