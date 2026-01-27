#!/usr/bin/env python3
"""
测试三维数据加载器
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kg_visualization.backend.enhanced_data_loader import EnhancedKGDataLoader

def test_data_loader():
    """测试数据加载器"""
    print("测试三维数据加载器...")
    
    try:
        # 创建数据加载器
        loader = EnhancedKGDataLoader(memory_id="test2")
        
        # 加载数据
        print("加载数据...")
        stats = loader.load_all_data()
        
        print(f"数据加载完成:")
        print(f"  实体数量: {stats['total_entities']}")
        print(f"  特征数量: {stats['total_features']}")
        print(f"  场景数量: {stats['total_scenes']}")
        print(f"  水平边数量: {stats['total_horizontal_edges']}")
        print(f"  垂直边数量: {stats['total_vertical_edges']}")
        
        # 获取三维图数据
        print("\n获取三维图数据...")
        graph_data = loader.get_3d_graph_data()
        
        print(f"三维图数据:")
        print(f"  实体节点: {len(graph_data['entities'])}")
        print(f"  特征节点: {len(graph_data['features'])}")
        print(f"  场景节点: {len(graph_data['scenes'])}")
        print(f"  水平边: {len(graph_data['horizontal_edges'])}")
        print(f"  垂直边: {len(graph_data['vertical_edges'])}")
        
        # 检查实体类型分布
        if stats['entity_types']:
            print(f"\n实体类型分布:")
            for entity_type, count in stats['entity_types'].items():
                print(f"  {entity_type}: {count}")
        
        # 测试获取实体详情
        if graph_data['entities']:
            sample_entity = graph_data['entities'][0]
            print(f"\n测试获取实体详情: {sample_entity['id']}")
            entity_details = loader.get_entity_details(sample_entity['id'])
            if entity_details:
                print(f"  实体特征数量: {len(entity_details.get('features', []))}")
        
        # 测试获取特征详情
        if graph_data['features']:
            sample_feature = graph_data['features'][0]
            print(f"\n测试获取特征详情: {sample_feature['id']}")
            feature_details = loader.get_feature_details(sample_feature['id'])
            if feature_details:
                print(f"  特征文本: {feature_details['feature']['feature'][:50]}...")
                print(f"  相关场景数量: {len(feature_details.get('scenes', []))}")
        
        # 测试获取场景详情
        if graph_data['scenes']:
            sample_scene = graph_data['scenes'][0]
            print(f"\n测试获取场景详情: {sample_scene['id']}")
            scene_details = loader.get_scene_details(sample_scene['id'])
            if scene_details:
                print(f"  相关特征数量: {len(scene_details.get('related_features', []))}")
        
        print("\n✅ 数据加载器测试通过!")
        return True
        
    except Exception as e:
        print(f"\n❌ 数据加载器测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_data_structure():
    """测试数据结构"""
    print("\n测试数据结构...")
    
    try:
        # 检查数据目录
        data_dir = "data/memory/test2/kg_data"
        if not os.path.exists(data_dir):
            print(f"警告: 数据目录不存在: {data_dir}")
            print("请确保已生成测试数据")
            return False
        
        # 检查实体文件
        entity_dir = os.path.join(data_dir, "entity")
        if os.path.exists(entity_dir):
            entity_files = [f for f in os.listdir(entity_dir) if f.endswith('.json')]
            print(f"找到 {len(entity_files)} 个实体文件")
            
            if entity_files:
                # 检查一个实体文件的结构
                sample_file = os.path.join(entity_dir, entity_files[0])
                import json
                with open(sample_file, 'r', encoding='utf-8') as f:
                    entity_data = json.load(f)
                
                print(f"实体文件结构检查:")
                print(f"  包含ID: {'id' in entity_data}")
                print(f"  包含类型: {'type' in entity_data}")
                print(f"  包含置信度: {'confidence' in entity_data}")
                print(f"  包含特征: {'features' in entity_data}")
                print(f"  特征数量: {len(entity_data.get('features', []))}")
                print(f"  包含来源: {'sources' in entity_data}")
        
        # 检查关系文件
        relation_dir = os.path.join(data_dir, "relation")
        if os.path.exists(relation_dir):
            relation_files = [f for f in os.listdir(relation_dir) if f.endswith('.json')]
            print(f"找到 {len(relation_files)} 个关系文件")
        
        print("✅ 数据结构测试通过!")
        return True
        
    except Exception as e:
        print(f"❌ 数据结构测试失败: {e}")
        return False

def main():
    """主测试函数"""
    print("=" * 60)
    print("三维知识图谱可视化系统测试")
    print("=" * 60)
    
    # 测试数据结构
    if not test_data_structure():
        print("\n⚠️ 数据结构测试失败，可能影响后续测试")
    
    # 测试数据加载器
    success = test_data_loader()
    
    print("\n" + "=" * 60)
    if success:
        print("✅ 所有测试通过!")
        print("\n下一步:")
        print("1. 运行 start_3d.bat 启动后端服务")
        print("2. 访问 http://localhost:8001/3d_frontend/index.html")
        print("3. 查看三维知识图谱可视化")
    else:
        print("❌ 测试失败，请检查错误信息")
    
    print("=" * 60)

if __name__ == "__main__":
    main()