#!/usr/bin/env python3
"""
实时测试文件删除更新功能
"""

import time
import requests
import json
import os
from pathlib import Path

def test_real_time_delete():
    """测试实时删除文件并观察更新"""
    
    print("=== 实时文件删除更新测试 ===\n")
    
    # 1. 获取初始状态
    print("1. 获取初始状态...")
    try:
        response = requests.get("http://localhost:8000/api/stats")
        initial_stats = response.json()
        print(f"   初始实体数量: {initial_stats['total_entities']}")
        print(f"   初始关系数量: {initial_stats['total_relations']}")
        print(f"   初始场景数量: {initial_stats['total_scenes']}")
    except Exception as e:
        print(f"   ❌ 无法连接到服务器: {e}")
        return
    
    # 2. 确定要删除的文件
    # 尝试多种可能的路径
    possible_paths = [
        Path("../../data/memory/kg_candidates/strong"),  # 从kg_visualization目录运行
        Path("../data/memory/kg_candidates/strong"),     # 从项目根目录运行
        Path("data/memory/kg_candidates/strong"),        # 从当前目录运行
        Path(__file__).parent.parent.parent / "data" / "memory" / "kg_candidates" / "strong"  # 绝对路径
    ]
    
    data_dir = None
    for path in possible_paths:
        if path.exists():
            data_dir = path
            break
    
    if data_dir is None:
        print("   ❌ 数据目录不存在，尝试的路径:")
        for path in possible_paths:
            print(f"      - {path}")
        print("   请确保数据目录存在或手动指定路径")
        return
    
    print(f"   使用数据目录: {data_dir}")
    
    files = list(data_dir.glob("*.kg_candidate.json"))
    if not files:
        print("   ❌ 没有找到KG候选文件")
        return
    
    test_file = files[0]  # 使用第一个文件
    print(f"2. 测试文件: {test_file.name}")
    print(f"   文件路径: {test_file}")
    print(f"   文件大小: {test_file.stat().st_size} 字节")
    
    # 3. 读取文件内容以了解影响
    with open(test_file, 'r', encoding='utf-8') as f:
        file_content = json.load(f)
    entities_in_file = len(file_content.get('entities', []))
    relations_in_file = len(file_content.get('relations', []))
    print(f"   文件包含实体: {entities_in_file}")
    print(f"   文件包含关系: {relations_in_file}")
    
    # 4. 检查WebSocket连接状态
    print("\n3. 检查WebSocket连接...")
    try:
        # 尝试建立WebSocket连接（简单测试）
        import websocket
        ws = websocket.WebSocket()
        ws.connect("ws://localhost:8000/ws", timeout=2)
        print("   ✅ WebSocket连接成功")
        ws.close()
    except ImportError:
        print("   ℹ️  websocket-client库未安装，跳过WebSocket测试")
    except Exception as e:
        print(f"   ⚠️  WebSocket连接失败: {e}")
    
    # 5. 用户操作提示
    print("\n4. 准备删除文件...")
    print(f"   请手动删除文件: {test_file.name}")
    print(f"   删除命令: del /f \"{test_file}\"")
    print("\n   或者按以下步骤操作:")
    print("   1. 打开文件资源管理器")
    print(f"   2. 导航到: {data_dir}")
    print(f"   3. 删除文件: {test_file.name}")
    print("\n   删除后请观察:")
    print("   - 后端控制台日志")
    print("   - 前端界面更新")
    print("   - 统计数字变化")
    
    input("\n   按Enter键继续（请在另一个窗口删除文件）...")
    
    # 6. 监控更新
    print("\n5. 监控更新...")
    print("   等待10秒观察更新...")
    
    for i in range(10):
        time.sleep(1)
        try:
            response = requests.get("http://localhost:8000/api/stats")
            current_stats = response.json()
            print(f"   {i+1}s: 实体={current_stats['total_entities']}, 关系={current_stats['total_relations']}, 场景={current_stats['total_scenes']}")
            
            # 检查是否有变化
            if (current_stats['total_entities'] != initial_stats['total_entities'] or
                current_stats['total_relations'] != initial_stats['total_relations']):
                print(f"   ⚡ 检测到数据变化!")
                break
        except Exception as e:
            print(f"   ❌ 获取状态失败: {e}")
    
    # 7. 最终状态
    print("\n6. 最终状态检查...")
    try:
        response = requests.get("http://localhost:8000/api/stats")
        final_stats = response.json()
        
        print(f"   最终实体数量: {final_stats['total_entities']} (变化: {final_stats['total_entities'] - initial_stats['total_entities']})")
        print(f"   最终关系数量: {final_stats['total_relations']} (变化: {final_stats['total_relations'] - initial_stats['total_relations']})")
        print(f"   最终场景数量: {final_stats['total_scenes']} (变化: {final_stats['total_scenes'] - initial_stats['total_scenes']})")
        
        if final_stats['total_scenes'] < initial_stats['total_scenes']:
            print("   ✅ 文件删除成功检测到!")
        else:
            print("   ❌ 文件删除未检测到")
            print("   可能的原因:")
            print("     1. 文件监控器未捕获删除事件")
            print("     2. 防抖机制阻止了事件")
            print("     3. 数据加载器缓存了旧数据")
            print("     4. Windows文件系统事件延迟")
    except Exception as e:
        print(f"   ❌ 获取最终状态失败: {e}")
    
    # 8. 建议
    print("\n7. 故障排除建议:")
    print("   - 检查后端控制台是否有'文件变化'日志")
    print("   - 检查前端WebSocket连接状态")
    print("   - 尝试修改文件而不是删除（测试修改事件）")
    print("   - 检查文件权限和防病毒软件")
    
    print("\n=== 测试完成 ===")

if __name__ == "__main__":
    test_real_time_delete()