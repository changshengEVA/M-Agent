#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试重复关系检测功能
"""

import sys
import logging
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from memory.memory_core.core.kg_base import KGBase
from memory.memory_core.core.repo_context import RepoContext

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_duplicate_relation():
    """测试重复关系检测"""
    print("=== 测试重复关系检测 ===")
    
    # 使用测试数据目录
    entity_dir = Path("data/memory/test4/kg_data/entity")
    relation_dir = Path("data/memory/test4/kg_data/relation")
    
    # 创建 RepoContext
    repos = RepoContext.from_directories(entity_dir, relation_dir)
    
    # 创建 KGBase 实例
    kg = KGBase(repos)
    
    # 检查现有关系
    print(f"现有关系数量: {len(repos.relation.list_all())}")
    
    # 选择两个存在的实体
    subject = "ZQR"
    object = "Peking_University"
    relation = "studies_at"
    
    # 第一次添加关系
    print(f"\n1. 第一次添加关系: {subject} -[{relation}]-> {object}")
    result1 = kg.add_relation(subject, relation, object, confidence=0.9)
    print(f"   结果: success={result1['success']}, changed={result1['changed']}")
    if result1['success']:
        print(f"   关系ID: {result1['details'].get('relation_id', 'unknown')}")
    
    # 第二次添加相同的关系（应该检测到重复）
    print(f"\n2. 第二次添加相同的关系（重复）")
    result2 = kg.add_relation(subject, relation, object, confidence=0.8)
    print(f"   结果: success={result2['success']}, changed={result2['changed']}")
    if result2['success'] and not result2['changed']:
        print(f"   检测到重复关系: {result2['details'].get('message', '')}")
        print(f"   现有关系ID: {result2['details'].get('existing_relation_id', 'unknown')}")
    
    # 第三次添加不同的关系（应该成功）
    print(f"\n3. 第三次添加不同的关系")
    relation2 = "works_at"
    result3 = kg.add_relation(subject, relation2, object, confidence=0.7)
    print(f"   结果: success={result3['success']}, changed={result3['changed']}")
    if result3['success']:
        print(f"   关系ID: {result3['details'].get('relation_id', 'unknown')}")
    
    # 最终统计
    print(f"\n最终关系数量: {len(repos.relation.list_all())}")
    
    # 验证
    if result1['success'] and result1['changed'] and result2['success'] and not result2['changed'] and result3['success'] and result3['changed']:
        print("\n✓ 测试通过：重复关系检测正常工作")
        return True
    else:
        print("\n✗ 测试失败")
        return False

if __name__ == "__main__":
    success = test_duplicate_relation()
    sys.exit(0 if success else 1)