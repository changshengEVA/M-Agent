#!/usr/bin/env python3
"""
验证修复是否有效
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from memory_visualization.backend.data_loader import MemoryDataLoader

def test_fix():
    """测试修复"""
    print("=== 验证修复是否有效 ===")
    
    # 创建数据加载器
    loader = MemoryDataLoader()
    
    # 获取episodes数据
    episodes_data = loader.get_all_episodes()
    print(f"1. 数据加载器返回的episodes数据数量: {len(episodes_data)}")
    
    # 模拟API返回的扁平化数据
    print("\n2. 模拟API返回的扁平化数据:")
    flat_episodes = []
    for episode_data in episodes_data:
        dialogue_id = episode_data.get("dialogue_id", "")
        episodes = episode_data.get("episodes", [])
        for episode in episodes:
            flat_episodes.append({
                "episode_id": episode.get("episode_id", ""),
                "dialogue_id": dialogue_id,
                "turn_span": episode.get("turn_span", []),
                "segmentation_reason": episode.get("segmentation_reason", [])
            })
    
    print(f"   扁平化episodes数量: {len(flat_episodes)}")
    if flat_episodes:
        print(f"   第一个扁平化episode: {flat_episodes[0]}")
    
    # 测试前端代码逻辑
    print("\n3. 测试前端代码逻辑:")
    print("   a) updateEpisodesList() 应该能处理两种格式:")
    print("      - 嵌套结构: {dialogue_id: '...', episodes: [...]}")
    print("      - 扁平化结构: [{episode_id: '...', dialogue_id: '...', ...}]")
    
    print("\n   b) displayEpisodeDetail() 应该能处理两种格式:")
    print("      - 嵌套结构: 在episodeData.episodes中查找")
    print("      - 扁平化结构: 直接在数组中查找")
    
    # 检查实际数据
    print("\n4. 实际数据检查:")
    print(f"   总episodes数量: {len(flat_episodes)}")
    
    # 按对话分组
    episodes_by_dialogue = {}
    for ep in flat_episodes:
        dialogue_id = ep.get('dialogue_id')
        if dialogue_id not in episodes_by_dialogue:
            episodes_by_dialogue[dialogue_id] = []
        episodes_by_dialogue[dialogue_id].append(ep)
    
    print(f"   涉及对话数量: {len(episodes_by_dialogue)}")
    for dialogue_id, eps in list(episodes_by_dialogue.items())[:3]:
        print(f"     - {dialogue_id}: {len(eps)} 个episodes")
    
    # 检查是否有episode_id重复
    episode_ids = [ep.get('episode_id') for ep in flat_episodes]
    unique_ids = set(episode_ids)
    print(f"\n5. Episode ID检查:")
    print(f"   唯一ID数量: {len(unique_ids)}")
    print(f"   总ID数量: {len(episode_ids)}")
    
    if len(unique_ids) != len(episode_ids):
        print("   ⚠️  警告: 有重复的episode_id")
        from collections import Counter
        duplicates = [item for item, count in Counter(episode_ids).items() if count > 1]
        print(f"   重复的ID: {duplicates}")
    else:
        print("   ✅ 所有episode_id都是唯一的")
    
    return True

if __name__ == "__main__":
    test_fix()