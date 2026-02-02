#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 MemoryCore 类
"""

import sys
import logging
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.memory_core.memory_system import MemoryCore

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def mock_llm(prompt: str) -> str:
    """模拟 LLM 函数，返回固定响应"""
    logger.debug(f"Mock LLM 收到提示: {prompt[:100]}...")
    return "这是一个模拟的 LLM 响应"


def mock_embed(text: str) -> list[float]:
    """模拟嵌入函数，返回随机向量"""
    import random
    logger.debug(f"Mock Embed 收到文本: {text[:50]}...")
    return [random.random() for _ in range(1536)]  # 模拟 ada-002 维度


def test_initialization():
    """测试 MemoryCore 初始化"""
    print("=== 测试 MemoryCore 初始化 ===")
    
    try:
        # 使用模拟函数避免依赖 OpenAI API
        memory_core = MemoryCore(
            workflow_id="test4",
            llm_func=mock_llm,
            embed_func=mock_embed,
            llm_temperature=0.0,
            similarity_threshold=0.7,
            top_k=3,
            use_threshold=True
        )
        
        print(f"✓ MemoryCore 初始化成功: {memory_core}")
        print(f"  工作流ID: {memory_core.workflow_id}")
        print(f"  KG数据路径: {memory_core.kg_data_path}")
        
        # 获取统计信息
        kg_stats = memory_core.get_kg_stats()
        print(f"  KG统计: {kg_stats}")
        
        # 获取实体解析统计
        er_stats = memory_core.get_entity_resolution_stats()
        print(f"  实体解析统计: {er_stats}")
        
        return memory_core
        
    except Exception as e:
        print(f"✗ MemoryCore 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_load_single_json(memory_core: MemoryCore):
    """测试加载单个 JSON 数据"""
    print("\n=== 测试加载单个 JSON 数据 ===")
    
    # 从现有文件读取示例 JSON
    sample_file = Path("data/memory/test4/kg_candidates/00001.json")
    if not sample_file.exists():
        print(f"✗ 示例文件不存在: {sample_file}")
        return
    
    try:
        import json
        with open(sample_file, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        
        print(f"从文件加载 JSON: {sample_file.name}")
        result = memory_core.load_from_dialogue_json(json_data)
        
        print(f"✓ 加载完成:")
        print(f"  处理实体数: {result.get('entities_processed')}")
        print(f"  处理特征数: {result.get('features_processed')}")
        print(f"  实体解析应用: {result.get('resolution_applied', False)}")
        
        if result.get('entity_errors'):
            print(f"  实体错误: {len(result['entity_errors'])} 个")
        
        if result.get('feature_errors'):
            print(f"  特征错误: {len(result['feature_errors'])} 个")
            
    except Exception as e:
        print(f"✗ 加载 JSON 失败: {e}")
        import traceback
        traceback.print_exc()


def test_load_directory(memory_core: MemoryCore):
    """测试加载整个目录"""
    print("\n=== 测试加载目录 ===")
    
    directory = Path("data/memory/test4/kg_candidates")
    if not directory.exists():
        print(f"✗ 目录不存在: {directory}")
        return
    
    try:
        result = memory_core.load_from_dialogue_path(directory)
        
        print(f"✓ 目录加载完成:")
        print(f"  总文件数: {result.get('total_files')}")
        print(f"  成功处理: {result.get('files_processed')}")
        print(f"  失败文件: {result.get('files_failed')}")
        print(f"  总实体数: {result.get('total_entities_processed')}")
        print(f"  总特征数: {result.get('total_features_processed')}")
        print(f"  实体解析应用: {result.get('resolution_applied', False)}")
        
        if result.get('success'):
            print("  所有文件处理成功")
        else:
            print("  部分文件处理失败")
            
    except Exception as e:
        print(f"✗ 加载目录失败: {e}")
        import traceback
        traceback.print_exc()


def main():
    """主测试函数"""
    print("开始测试 MemoryCore 系统")
    
    # 测试初始化
    memory_core = test_initialization()
    if not memory_core:
        print("初始化失败，终止测试")
        return
    
    # 测试单个 JSON 加载
    test_load_single_json(memory_core)
    
    # 测试目录加载（可选，可能会处理多个文件）
    # test_load_directory(memory_core)
    
    print("\n=== 测试完成 ===")


if __name__ == "__main__":
    main()