#!/usr/bin/env python3
"""
测试数据格式匹配问题
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from memory_visualization.backend.data_loader import MemoryDataLoader

def test_data_formats():
    """测试数据格式"""
    print("=== 测试数据格式匹配 ===")
    
    # 创建数据加载器
    loader = MemoryDataLoader()
    
    # 1. 检查数据加载器返回的格式
    print("\n1. 数据加载器返回的episodes格式:")
    episodes_data = loader.get_all_episodes()
    print(f"  类型: {type(episodes_data)}")
    print(f"  长度: {len(episodes_data)}")
    
    if episodes_data:
        first_item = episodes_data[0]
        print(f"  第一个元素的类型: {type(first_item)}")
        print(f"  第一个元素的键: {list(first_item.keys())}")
        print(f"  是否有'episodes'键: {'episodes' in first_item}")
        if 'episodes' in first_item:
            print(f"  'episodes'值的类型: {type(first_item['episodes'])}")
            print(f"  'episodes'值的长度: {len(first_item['episodes'])}")
    
    # 2. 检查API端点应该返回的格式
    print("\n2. API端点应该返回的格式:")
    print("  根据main.py，/api/episodes返回扁平化的EpisodeResponse列表")
    print("  每个EpisodeResponse包含: episode_id, dialogue_id, turn_span, segmentation_reason")
    
    # 3. 模拟API返回的数据
    print("\n3. 模拟API返回的数据:")
    from memory_visualization.backend.main import get_episodes
    import asyncio
    
    # 由于get_episodes是异步函数，我们需要模拟运行
    async def test_api_format():
        # 这里我们直接查看函数逻辑
        episodes_data = loader.get_all_episodes()
        result = []
        for episode_data in episodes_data:
            dialogue_id = episode_data.get("dialogue_id", "")
            episodes = episode_data.get("episodes", [])
            for episode in episodes:
                result.append({
                    "episode_id": episode.get("episode_id", ""),
                    "dialogue_id": dialogue_id,
                    "turn_span": episode.get("turn_span", []),
                    "segmentation_reason": episode.get("segmentation_reason", [])
                })
        return result
    
    result = asyncio.run(test_api_format())
    print(f"  API返回的数据长度: {len(result)}")
    if result:
        print(f"  第一个API返回项: {result[0]}")
        print(f"  第一个API返回项的键: {list(result[0].keys())}")
    
    # 4. 前端期望的格式
    print("\n4. 前端期望的格式:")
    print("  根据app.js第240-248行，前端期望this.episodes是嵌套结构:")
    print("  this.episodes = [")
    print("    {dialogue_id: '...', episodes: [{episode_id: '...', ...}]},")
    print("    ...")
    print("  ]")
    
    # 5. 检查实际前端接收的数据
    print("\n5. 实际前端接收的数据:")
    print("  前端从/api/episodes接收扁平化列表")
    print("  但updateEpisodesList()期望嵌套结构")
    print("  这导致allEpisodes数组为空，因为this.episodes.forEach中")
    print("  episodeData.episodes是undefined")
    
    return True

if __name__ == "__main__":
    test_data_formats()