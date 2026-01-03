#!/usr/bin/env python3
"""
简单测试 - 检查基本功能
"""

import sys
import os

print("简单测试开始...")

# 测试1: 检查Python版本
print(f"Python版本: {sys.version}")

# 测试2: 检查当前目录
print(f"当前目录: {os.getcwd()}")

# 测试3: 检查数据目录
data_path = os.path.join(os.path.dirname(os.getcwd()), "data", "memory", "kg_candidates", "strong")
print(f"数据路径: {data_path}")
print(f"路径是否存在: {os.path.exists(data_path)}")

if os.path.exists(data_path):
    files = [f for f in os.listdir(data_path) if f.endswith('.json')]
    print(f"找到 {len(files)} 个JSON文件")
    for f in files[:3]:
        print(f"  - {f}")

# 测试4: 尝试导入模块
print("\n尝试导入模块...")
try:
    sys.path.insert(0, os.path.join(os.getcwd(), "backend"))
    from data_loader import KGDataLoader
    print("✅ data_loader 导入成功")
    
    loader = KGDataLoader()
    print(f"数据目录: {loader.data_dir}")
    
    stats = loader.load_all_data()
    print(f"实体数: {stats.get('total_entities', 0)}")
    print(f"关系数: {stats.get('total_relations', 0)}")
    
except Exception as e:
    print(f"❌ 导入失败: {e}")
    import traceback
    traceback.print_exc()

print("\n测试完成")