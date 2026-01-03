#!/usr/bin/env python3
"""
测试文件删除后的实时更新功能
"""

import sys
import os
import time
import json
from pathlib import Path

# 添加项目路径
sys.path.append('.')

from backend.data_loader import KGDataLoader
from backend.file_watcher import KGFileWatcher

def test_file_deletion_update():
    """测试文件删除后的更新机制"""
    
    print("=== 测试文件删除实时更新 ===\n")
    
    # 1. 初始化数据加载器
    loader = KGDataLoader()
    print(f"1. 数据目录: {loader.data_dir}")
    print(f"   目录存在: {loader.data_dir.exists()}")
    
    # 2. 加载初始数据
    stats = loader.load_all_data()
    print(f"2. 初始数据统计:")
    print(f"   实体数量: {stats['total_entities']}")
    print(f"   关系数量: {stats['total_relations']}")
    print(f"   场景数量: {stats['total_scenes']}")
    
    # 3. 检查特定文件
    test_file = loader.data_dir / "scene_000005.kg_candidate.json"
    print(f"3. 测试文件: {test_file}")
    print(f"   文件存在: {test_file.exists()}")
    
    if test_file.exists():
        # 读取文件内容
        with open(test_file, 'r', encoding='utf-8') as f:
            content = json.load(f)
        print(f"   文件包含实体: {len(content.get('entities', []))}")
        print(f"   文件包含关系: {len(content.get('relations', []))}")
    
    # 4. 测试文件监控回调
    print("\n4. 测试文件监控回调机制...")
    
    callback_called = []
    
    def mock_callback(change_type, file_path):
        callback_called.append((change_type, file_path))
        print(f"   ⚡ 回调被调用: {change_type} {file_path}")
    
    # 创建文件监控器
    watcher = KGFileWatcher(str(loader.data_dir), mock_callback)
    
    if watcher.start():
        print("   ✅ 文件监控器已启动")
        
        # 等待监控器初始化
        time.sleep(1)
        
        # 5. 模拟文件删除
        print("\n5. 模拟文件删除...")
        print(f"   删除前文件存在: {test_file.exists()}")
        
        # 注意：这里只是模拟，不实际删除文件
        print("   ℹ️  请手动删除文件进行测试")
        print("   ℹ️  删除命令: del /f \"F:\\AI\\M-Agent\\data\\memory\\kg_candidates\\strong\\scene_000005.kg_candidate.json\"")
        
        # 等待用户操作
        input("\n   按Enter键继续（请在另一个窗口删除文件）...")
        
        # 检查回调是否被调用
        time.sleep(2)  # 等待事件处理
        
        if callback_called:
            print(f"\n   ✅ 检测到 {len(callback_called)} 个文件变化事件")
            for change_type, file_path in callback_called:
                print(f"      - {change_type}: {Path(file_path).name}")
        else:
            print("\n   ❌ 未检测到文件变化事件")
            print("   可能的原因:")
            print("     1. 文件监控器未正确监控目录")
            print("     2. 删除事件未被捕获")
            print("     3. 防抖机制阻止了事件")
        
        # 停止监控器
        watcher.stop()
        print("\n   🛑 文件监控器已停止")
    else:
        print("   ❌ 文件监控器启动失败")
    
    # 6. 检查数据是否更新
    print("\n6. 检查数据更新...")
    new_stats = loader.load_all_data()
    print(f"   更新后实体数量: {new_stats['total_entities']}")
    print(f"   更新后关系数量: {new_stats['total_relations']}")
    print(f"   更新后场景数量: {new_stats['total_scenes']}")
    
    # 7. 验证WebSocket推送
    print("\n7. WebSocket推送验证:")
    print("   请检查前端界面:")
    print("     - 更新计数器是否增加")
    print("     - 日志面板是否显示'检测到文件删除'")
    print("     - 统计数字是否变化")
    
    print("\n=== 测试完成 ===")

if __name__ == "__main__":
    test_file_deletion_update()