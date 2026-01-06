#!/usr/bin/env python3
"""
测试API端点
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from memory_visualization.backend.main import app
from fastapi.testclient import TestClient

def test_api_endpoints():
    """测试API端点"""
    print("=== 测试API端点 ===")
    
    client = TestClient(app)
    
    # 测试统计数据端点
    print("\n1. 测试 /api/stats 端点:")
    response = client.get("/api/stats")
    print(f"  状态码: {response.status_code}")
    if response.status_code == 200:
        stats = response.json()
        print(f"  total_episodes: {stats.get('total_episodes')}")
        print(f"  episodes_by_dialogue: {stats.get('episodes_by_dialogue')}")
    else:
        print(f"  错误: {response.text}")
    
    # 测试episodes端点
    print("\n2. 测试 /api/episodes 端点:")
    response = client.get("/api/episodes")
    print(f"  状态码: {response.status_code}")
    if response.status_code == 200:
        episodes = response.json()
        print(f"  返回的episodes数量: {len(episodes)}")
        if episodes:
            print(f"  前3个episodes:")
            for i, ep in enumerate(episodes[:3]):
                print(f"    {i+1}. episode_id: {ep.get('episode_id')}, dialogue_id: {ep.get('dialogue_id')}")
    else:
        print(f"  错误: {response.text}")
    
    # 测试dialogues端点
    print("\n3. 测试 /api/dialogues 端点:")
    response = client.get("/api/dialogues")
    print(f"  状态码: {response.status_code}")
    if response.status_code == 200:
        dialogues = response.json()
        print(f"  返回的dialogues数量: {len(dialogues)}")
    else:
        print(f"  错误: {response.text}")
    
    # 测试scenes端点
    print("\n4. 测试 /api/scenes 端点:")
    response = client.get("/api/scenes")
    print(f"  状态码: {response.status_code}")
    if response.status_code == 200:
        scenes = response.json()
        print(f"  返回的scenes数量: {len(scenes)}")
    else:
        print(f"  错误: {response.text}")
    
    # 测试特定dialogue详情
    print("\n5. 测试 /api/dialogue/{id} 端点:")
    if episodes:
        dialogue_id = episodes[0].get('dialogue_id')
        response = client.get(f"/api/dialogue/{dialogue_id}")
        print(f"  状态码: {response.status_code}")
        if response.status_code == 200:
            detail = response.json()
            print(f"  对话详情包含episodes: {len(detail.get('episodes', []))}")
            print(f"  对话详情包含qualifications: {len(detail.get('qualifications', []))}")
        else:
            print(f"  错误: {response.text}")
    
    return True

if __name__ == "__main__":
    # 需要设置环境变量或模拟数据加载器
    # 由于数据加载器在启动时初始化，我们需要模拟它
    import memory_visualization.backend.main as main_module
    
    # 创建数据加载器
    from memory_visualization.backend.data_loader import MemoryDataLoader
    main_module.data_loader = MemoryDataLoader()
    
    test_api_endpoints()