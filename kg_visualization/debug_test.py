#!/usr/bin/env python3
"""
调试脚本 - 检查后端问题
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
print("调试知识图谱可视化系统")
print("=" * 60)

# 1. 测试数据加载器
print("\n1. 测试数据加载器...")
try:
    from backend.data_loader import KGDataLoader
    
    data_dir = "../../data/memory/kg_candidates/strong"
    print(f"数据目录: {data_dir}")
    print(f"目录是否存在: {os.path.exists(data_dir)}")
    
    if os.path.exists(data_dir):
        print("目录内容:")
        for file in os.listdir(data_dir):
            if file.endswith('.json'):
                print(f"  - {file}")
    
    loader = KGDataLoader(data_dir)
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
    
except Exception as e:
    print(f"❌ 数据加载失败: {e}")
    import traceback
    traceback.print_exc()

# 2. 测试FastAPI导入
print("\n2. 测试FastAPI导入...")
try:
    from fastapi import FastAPI
    print("✅ FastAPI导入成功")
except Exception as e:
    print(f"❌ FastAPI导入失败: {e}")

# 3. 测试WebSocket导入
print("\n3. 测试WebSocket导入...")
try:
    from fastapi import WebSocket
    print("✅ WebSocket导入成功")
except Exception as e:
    print(f"❌ WebSocket导入失败: {e}")

# 4. 测试静态文件导入
print("\n4. 测试静态文件导入...")
try:
    from fastapi.staticfiles import StaticFiles
    print("✅ StaticFiles导入成功")
except Exception as e:
    print(f"❌ StaticFiles导入失败: {e}")

# 5. 测试完整后端启动
print("\n5. 测试完整后端启动...")
try:
    # 模拟启动
    import asyncio
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    
    app = FastAPI()
    
    @app.get("/")
    async def root():
        return {"message": "测试成功"}
    
    print("✅ 基本FastAPI应用创建成功")
    
    # 测试uvicorn导入
    import uvicorn
    print("✅ uvicorn导入成功")
    
except Exception as e:
    print(f"❌ 后端启动测试失败: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("调试完成")
print("=" * 60)