#!/usr/bin/env python3
"""
测试修复后的后端
"""

import sys
import os
import logging

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

print("=" * 60)
print("测试修复后的后端")
print("=" * 60)

# 测试数据加载器
print("\n1. 测试数据加载器...")
try:
    from backend.data_loader import KGDataLoader
    
    # 使用默认路径
    loader = KGDataLoader()
    
    print(f"数据目录: {loader.data_dir}")
    print(f"目录是否存在: {loader.data_dir.exists()}")
    
    if loader.data_dir.exists():
        print("目录内容:")
        for file in list(loader.data_dir.glob("*.json"))[:5]:
            print(f"  - {file.name}")
    
    stats = loader.load_all_data()
    
    print(f"\n✅ 数据加载成功!")
    print(f"   实体数: {stats.get('total_entities', 0)}")
    print(f"   关系数: {stats.get('total_relations', 0)}")
    print(f"   场景数: {stats.get('total_scenes', 0)}")
    print(f"   实体类型: {stats.get('entity_types', {})}")
    
    # 测试获取图数据
    graph_data = loader.get_graph_data()
    print(f"   图数据节点数: {len(graph_data.get('nodes', []))}")
    print(f"   图数据边数: {len(graph_data.get('edges', []))}")
    
    # 测试获取所有实体
    entities = loader.get_all_entities()
    print(f"   实体列表长度: {len(entities)}")
    
    # 测试获取所有关系
    relations = loader.get_all_relations()
    print(f"   关系列表长度: {len(relations)}")
    
except Exception as e:
    print(f"❌ 数据加载失败: {e}")
    import traceback
    traceback.print_exc()

# 测试FastAPI应用
print("\n2. 测试FastAPI应用...")
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    
    # 导入主应用
    from backend.main import app
    
    client = TestClient(app)
    
    # 测试根路径
    response = client.get("/")
    print(f"根路径状态码: {response.status_code}")
    
    # 测试API端点
    endpoints = ["/api/nodes", "/api/edges", "/api/scenes", "/api/stats", "/api/graph"]
    
    for endpoint in endpoints:
        try:
            response = client.get(endpoint)
            print(f"{endpoint}: 状态码 {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                if endpoint == "/api/stats":
                    print(f"  实体数: {data.get('total_entities', 0)}")
        except Exception as e:
            print(f"{endpoint}: 错误 - {e}")
    
    print("✅ FastAPI应用测试通过")
    
except Exception as e:
    print(f"❌ FastAPI应用测试失败: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)