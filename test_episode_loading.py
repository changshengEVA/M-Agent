#!/usr/bin/env python3
"""
测试episode数据加载
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from memory_visualization.backend.data_loader import MemoryDataLoader

def test_episode_loading():
    """测试episode数据加载"""
    print("=== 测试episode数据加载 ===")
    
    try:
        # 创建数据加载器
        loader = MemoryDataLoader()
        
        print(f"数据目录: {loader.data_dir}")
        print(f"目录是否存在: {loader.data_dir.exists()}")
        
        # 检查episodes数据
        episodes = loader.get_all_episodes()
        print(f"\n加载的episodes数据数量: {len(episodes)}")
        
        if episodes:
            print("\n前3个episodes数据:")
            for i, episode_data in enumerate(episodes[:3]):
                print(f"\n{i+1}. dialogue_id: {episode_data.get('dialogue_id')}")
                print(f"   episodes列表长度: {len(episode_data.get('episodes', []))}")
                if episode_data.get('episodes'):
                    for j, ep in enumerate(episode_data['episodes'][:2]):
                        print(f"   - episode {j+1}: {ep.get('episode_id')}, turn_span: {ep.get('turn_span')}")
        
        # 检查统计数据
        stats = loader.get_stats()
        print(f"\n统计数据:")
        print(f"  total_episodes: {stats.get('total_episodes')}")
        print(f"  episodes_by_dialogue: {stats.get('episodes_by_dialogue')}")
        
        # 测试获取单个dialogue的episodes
        if episodes:
            dialogue_id = episodes[0].get('dialogue_id')
            dialogue_episodes = loader.get_episodes_by_dialogue_id(dialogue_id)
            print(f"\n对话 {dialogue_id} 的episodes数量: {len(dialogue_episodes)}")
            if dialogue_episodes:
                print(f"第一个episode: {dialogue_episodes[0]}")
        
        # 检查数据目录结构
        episodes_path = loader.data_dir / "episodes" / "by_dialogue"
        print(f"\nepisodes目录: {episodes_path}")
        print(f"目录是否存在: {episodes_path.exists()}")
        
        if episodes_path.exists():
            dialogue_dirs = list(episodes_path.iterdir())
            print(f"对话目录数量: {len(dialogue_dirs)}")
            
            for dialogue_dir in dialogue_dirs[:3]:
                if dialogue_dir.is_dir():
                    print(f"\n对话目录: {dialogue_dir.name}")
                    episodes_file = dialogue_dir / "episodes_v1.json"
                    print(f"  episodes文件存在: {episodes_file.exists()}")
                    
                    if episodes_file.exists():
                        import json
                        try:
                            with open(episodes_file, 'r', encoding='utf-8') as f:
                                data = json.load(f)
                                print(f"  文件内容: {data.keys()}")
                                print(f"  episodes数量: {len(data.get('episodes', []))}")
                        except Exception as e:
                            print(f"  读取文件失败: {e}")
        
        return True
        
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    test_episode_loading()