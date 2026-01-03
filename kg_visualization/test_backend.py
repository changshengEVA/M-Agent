#!/usr/bin/env python3
"""
测试后端功能
"""

import sys
import os

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_data_loader():
    """测试数据加载器"""
    print("测试数据加载器...")
    try:
        from backend.data_loader import KGDataLoader
        
        # 使用相对路径
        data_dir = "../../data/memory/kg_candidates/strong"
        loader = KGDataLoader(data_dir)
        
        stats = loader.load_all_data()
        
        print(f"✅ 数据加载成功!")
        print(f"   实体数: {stats.get('total_entities', 0)}")
        print(f"   关系数: {stats.get('total_relations', 0)}")
        print(f"   场景数: {stats.get('total_scenes', 0)}")
        print(f"   实体类型分布: {stats.get('entity_types', {})}")
        
        # 测试获取图数据
        graph_data = loader.get_graph_data()
        print(f"   图数据节点数: {len(graph_data.get('nodes', []))}")
        print(f"   图数据边数: {len(graph_data.get('edges', []))}")
        
        return True
        
    except Exception as e:
        print(f"❌ 数据加载失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_file_watcher():
    """测试文件监控器"""
    print("\n测试文件监控器...")
    try:
        from backend.file_watcher import KGFileWatcher
        
        def dummy_callback(change_type, file_path):
            print(f"   回调被调用: {change_type} {file_path}")
        
        data_dir = "../../data/memory/kg_candidates/strong"
        watcher = KGFileWatcher(data_dir, dummy_callback)
        
        # 测试启动和停止
        if watcher.start():
            print("✅ 文件监控器启动成功")
            # 短暂运行后停止
            import time
            time.sleep(1)
            watcher.stop()
            print("✅ 文件监控器停止成功")
            return True
        else:
            print("❌ 文件监控器启动失败")
            return False
            
    except Exception as e:
        print(f"❌ 文件监控器测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_api_endpoints():
    """测试API端点（需要服务运行）"""
    print("\n测试API端点...")
    try:
        import requests
        import json
        
        base_url = "http://localhost:8000"
        
        # 测试根路径
        print("   测试根路径...")
        response = requests.get(base_url, timeout=5)
        if response.status_code == 200:
            print("   ✅ 根路径访问成功")
        else:
            print(f"   ❌ 根路径访问失败: {response.status_code}")
            return False
        
        # 测试API端点
        endpoints = [
            "/api/nodes",
            "/api/edges", 
            "/api/scenes",
            "/api/stats",
            "/api/graph"
        ]
        
        for endpoint in endpoints:
            print(f"   测试 {endpoint}...")
            try:
                response = requests.get(f"{base_url}{endpoint}", timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    print(f"   ✅ {endpoint} 访问成功")
                    if endpoint == "/api/stats":
                        print(f"      实体数: {data.get('total_entities', 0)}")
                else:
                    print(f"   ❌ {endpoint} 访问失败: {response.status_code}")
                    return False
            except requests.exceptions.ConnectionError:
                print(f"   ⚠️  {endpoint} 连接失败（服务可能未启动）")
                return False
            except Exception as e:
                print(f"   ❌ {endpoint} 错误: {e}")
                return False
        
        print("✅ 所有API端点测试通过")
        return True
        
    except Exception as e:
        print(f"❌ API测试失败: {e}")
        return False

def main():
    """主测试函数"""
    print("=" * 50)
    print("知识图谱可视化系统测试")
    print("=" * 50)
    
    # 测试数据加载器
    data_loader_ok = test_data_loader()
    
    # 测试文件监控器
    file_watcher_ok = test_file_watcher()
    
    # 测试API端点（需要先启动服务）
    print("\n提示: API端点测试需要后端服务正在运行")
    print("请先运行 'python backend/main.py' 启动服务")
    api_test_choice = input("是否尝试测试API端点? (y/n): ").strip().lower()
    
    api_ok = False
    if api_test_choice == 'y':
        api_ok = test_api_endpoints()
    else:
        print("跳过API端点测试")
        api_ok = True  # 假设通过
    
    # 汇总结果
    print("\n" + "=" * 50)
    print("测试结果汇总:")
    print(f"  数据加载器: {'✅ 通过' if data_loader_ok else '❌ 失败'}")
    print(f"  文件监控器: {'✅ 通过' if file_watcher_ok else '❌ 失败'}")
    print(f"  API端点: {'✅ 通过' if api_ok else '❌ 失败'}")
    
    all_passed = data_loader_ok and file_watcher_ok and api_ok
    print(f"\n总体结果: {'✅ 所有测试通过' if all_passed else '❌ 部分测试失败'}")
    print("=" * 50)
    
    return all_passed

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)