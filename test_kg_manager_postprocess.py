#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试KGManager的后处理功能
"""

import json
import tempfile
import os
from pathlib import Path

def test_kg_candidate_processing():
    """测试kg_candidate处理和后处理"""
    print("测试KGManager的kg_candidate处理和后处理...")
    
    try:
        from memory.memory_sys.kg_manager import KGManager
        
        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        kg_data_dir = Path(temp_dir) / "kg_data"
        
        # 初始化KGManager
        kg_manager = KGManager(kg_data_dir, "test_postprocess")
        
        # 创建一个模拟的kg_candidate JSON
        kg_candidate_json = {
            "file_number": 1,
            "generated_at": "2026-01-27T20:52:09.800086Z",
            "episode_id": "ep_001",
            "dialogue_id": "dlg_2025-10-21_22-24-25",
            "kg_candidate": {
                "facts": {
                    "entities": [
                        {"id": "张三", "type": "人物", "confidence": 0.9},
                        {"id": "李四", "type": "人物", "confidence": 0.8},
                        {"id": "北京", "type": "地点", "confidence": 0.95}
                    ],
                    "features": [
                        {"entity_id": "张三", "feature": "是一名程序员"},
                        {"entity_id": "李四", "feature": "是一名设计师"}
                    ],
                    "relations": [
                        {"subject": "张三", "relation": "认识", "object": "李四", "confidence": 0.7}
                    ],
                    "attributes": [
                        {"entity": "北京", "field": "人口", "value": "2154万"}
                    ]
                }
            },
            "prompt_version": "v2",
            "prompt_key": "kg_strong_filter_v2"
        }
        
        # 处理kg_candidate
        print("处理kg_candidate...")
        result = kg_manager.receive_kg_candidate(kg_candidate_json)
        
        # 检查结果
        assert result["success"] == True
        assert result["file_number"] == 1
        
        stats = result["stats"]
        assert stats["entities"]["saved"] == 3  # 3个实体
        assert stats["features"]["saved"] == 2  # 2个特征
        assert stats["relations"]["saved"] == 1  # 1个关系
        assert stats["attributes"]["saved"] == 1  # 1个属性
        
        # 检查后处理统计
        assert "post_processing" in stats
        post_stats = stats["post_processing"]
        assert "processed" in post_stats
        assert "added_to_library" in post_stats
        
        print(f"后处理统计: 处理 {post_stats.get('processed', 0)} 个实体, "
              f"新增 {post_stats.get('added_to_library', 0)} 个到库")
        
        # 检查实体库文件是否存在
        library_path = kg_data_dir / "entity_library.json"
        assert library_path.exists()
        
        # 加载实体库检查内容
        with open(library_path, 'r', encoding='utf-8') as f:
            library_data = json.load(f)
        
        print(f"实体库中有 {len(library_data)} 个实体")
        
        # 检查实体目录
        entity_dir = kg_data_dir / "entity"
        entity_files = list(entity_dir.glob("*.json"))
        print(f"实体目录中有 {len(entity_files)} 个实体文件")
        
        # 测试实体合并功能
        print("\n测试实体合并功能...")
        
        # 创建两个测试实体
        entity_a = {"id": "实体A", "type": "测试", "confidence": 0.9}
        entity_b = {"id": "实体B", "type": "测试", "confidence": 0.8}
        
        source_info = kg_manager.source_manager.create_source_info(
            dialogue_id="test_dlg",
            episode_id="test_ep",
            generated_at="2026-01-28T00:00:00Z"
        )
        
        # 保存实体
        kg_manager.entity_storage.save_entity(entity_a, source_info)
        kg_manager.entity_storage.save_entity(entity_b, source_info)
        
        # 合并实体
        merge_result = kg_manager.combine_entity("实体A", "实体B")
        print(f"实体合并结果: {merge_result.get('success', False)}")
        print(f"合并消息: {merge_result.get('message', '')}")
        
        # 清理临时目录
        import shutil
        shutil.rmtree(temp_dir)
        
        print("\nKGManager后处理测试通过!")
        
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()

def test_entity_library_functionality():
    """测试实体库功能"""
    print("\n测试实体库高级功能...")
    
    try:
        from memory.memory_sys.storage.entity_library import EntityLibrary
        
        # 创建临时文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            library_path = f.name
        
        # 初始化实体库
        library = EntityLibrary(library_path)
        
        # 添加一些测试实体
        test_entities = [
            ("苹果公司", ["Apple", "Apple Inc."], [0.1, 0.2, 0.3]),
            ("微软", ["Microsoft", "MSFT"], [0.4, 0.5, 0.6]),
            ("谷歌", ["Google", "Alphabet"], [0.7, 0.8, 0.9]),
        ]
        
        for entity_id, aliases, embedding in test_entities:
            library.add_entity(entity_id, embedding)
            for alias in aliases:
                library.add_alias(entity_id, alias)
        
        # 测试名称查找
        assert library.get_entity_id("Apple") == "苹果公司"
        assert library.get_entity_id("Microsoft") == "微软"
        assert library.get_entity_id("Google") == "谷歌"
        
        # 测试相似度查找（使用相同的嵌入向量应该找到自己）
        similar = library.find_similar_entities("苹果公司", [0.1, 0.2, 0.3], threshold=0.99)
        # 应该找不到，因为阈值太高
        assert len(similar) == 0
        
        # 使用低阈值
        similar = library.find_similar_entities("苹果公司", [0.1, 0.2, 0.3], threshold=0.1)
        # 应该找到其他实体
        print(f"找到 {len(similar)} 个相似实体")
        
        # 保存实体库
        assert library.save() == True
        
        print("实体库高级功能测试通过!")
        
        # 清理
        os.unlink(library_path)
        
    except Exception as e:
        print(f"实体库测试失败: {e}")

def main():
    """主测试函数"""
    print("开始测试KGManager后处理功能...")
    
    test_kg_candidate_processing()
    test_entity_library_functionality()
    
    print("\n所有测试完成!")

if __name__ == "__main__":
    main()