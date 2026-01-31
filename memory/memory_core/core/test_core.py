#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Core 模块测试脚本

测试知识图谱核心算子的基本功能。
"""

import os
import sys
import tempfile
import shutil
import logging
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from memory.memory_core.core import KGBase, RepoContext

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def test_basic_operations():
    """测试基本操作"""
    print("=" * 60)
    print("测试知识图谱核心算子")
    print("=" * 60)
    
    # 创建临时目录
    temp_dir = tempfile.mkdtemp(prefix="kg_test_")
    entity_dir = Path(temp_dir) / "entities"
    relation_dir = Path(temp_dir) / "relations"
    
    entity_dir.mkdir(parents=True, exist_ok=True)
    relation_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"测试目录: {temp_dir}")
    print(f"实体目录: {entity_dir}")
    print(f"关系目录: {relation_dir}")
    
    try:
        # 1. 创建 KGBase 实例
        print("\n1. 创建 KGBase 实例...")
        kg = KGBase(entity_dir=entity_dir, relation_dir=relation_dir)
        print(f"   KGBase 创建成功")
        
        # 2. 测试添加实体
        print("\n2. 测试添加实体...")
        result = kg.add_entity("person_001", entity_type="person")
        print(f"   添加实体 person_001: {result['success']}")
        assert result['success'] == True, "添加实体失败"
        
        result = kg.add_entity("person_002", entity_type="person")
        print(f"   添加实体 person_002: {result['success']}")
        assert result['success'] == True, "添加实体失败"
        
        result = kg.add_entity("org_001", entity_type="organization")
        print(f"   添加实体 org_001: {result['success']}")
        assert result['success'] == True, "添加实体失败"
        
        # 3. 测试检查实体存在性
        print("\n3. 测试检查实体存在性...")
        result = kg.assert_entity_exists("person_001")
        print(f"   检查 person_001 存在: {result['success']}")
        assert result['success'] == True, "实体应该存在"
        
        result = kg.assert_entity_exists("nonexistent")
        print(f"   检查 nonexistent 存在: {result['success']}")
        assert result['success'] == False, "实体不应该存在"
        
        # 4. 测试添加特征
        print("\n4. 测试添加特征...")
        feature_record = {
            "feature": "works as a software engineer",
            "confidence": 0.9,
            "sources": [{"dialogue_id": "dlg_001", "episode_id": "ep_001"}]
        }
        result = kg.append_feature("person_001", feature_record)
        print(f"   向 person_001 添加特征: {result['success']}")
        assert result['success'] == True, "添加特征失败"
        
        # 5. 测试添加属性
        print("\n5. 测试添加属性...")
        attribute_record = {
            "field": "age",
            "value": 30,
            "confidence": 0.8,
            "sources": [{"dialogue_id": "dlg_001", "episode_id": "ep_001"}]
        }
        result = kg.append_attribute("person_001", attribute_record)
        print(f"   向 person_001 添加属性: {result['success']}")
        assert result['success'] == True, "添加属性失败"
        
        # 6. 测试获取统计信息
        print("\n6. 测试获取统计信息...")
        stats = kg.get_kg_stats()
        print(f"   实体数量: {stats['entity_count']}")
        print(f"   关系数量: {stats['relation_count']}")
        print(f"   特征数量: {stats['feature_count']}")
        print(f"   属性数量: {stats['attribute_count']}")
        assert stats['entity_count'] == 3, f"实体数量应为3，实际为{stats['entity_count']}"
        
        # 7. 测试重命名实体
        print("\n7. 测试重命名实体...")
        result = kg.rename_entity("person_002", "person_002_renamed")
        print(f"   重命名 person_002 -> person_002_renamed: {result['success']}")
        assert result['success'] == True, "重命名实体失败"
        
        # 8. 测试合并实体
        print("\n8. 测试合并实体...")
        # 先给 person_001 添加一些内容
        feature_record2 = {
            "feature": "has experience in machine learning",
            "confidence": 0.7,
            "sources": [{"dialogue_id": "dlg_002", "episode_id": "ep_002"}]
        }
        kg.append_feature("person_001", feature_record2)
        
        attribute_record2 = {
            "field": "role",
            "value": "engineer",
            "confidence": 0.9,
            "sources": [{"dialogue_id": "dlg_002", "episode_id": "ep_002"}]
        }
        kg.append_attribute("person_001", attribute_record2)
        
        # 给 person_002_renamed 添加一些内容
        feature_record3 = {
            "feature": "works as a data scientist",
            "confidence": 0.8,
            "sources": [{"dialogue_id": "dlg_003", "episode_id": "ep_003"}]
        }
        kg.append_feature("person_002_renamed", feature_record3)
        
        # 合并实体
        result = kg.merge_entities("person_001", "person_002_renamed")
        print(f"   合并 person_002_renamed -> person_001: {result['success']}")
        print(f"   合并详情: {result['details']}")
        assert result['success'] == True, "合并实体失败"
        
        # 9. 测试删除实体
        print("\n9. 测试删除实体...")
        result = kg.delete_entity("org_001")
        print(f"   删除实体 org_001: {result['success']}")
        assert result['success'] == True, "删除实体失败"
        
        # 10. 测试完整性验证
        print("\n10. 测试完整性验证...")
        result = kg.validate_kg_integrity()
        print(f"   完整性验证: {result['success']}")
        print(f"   验证详情: {result['details']['summary']}")
        assert result['success'] == True, "完整性验证失败"
        
        # 11. 最终统计
        print("\n11. 最终统计信息...")
        final_stats = kg.get_kg_stats()
        print(f"   最终实体数量: {final_stats['entity_count']}")
        print(f"   最终关系数量: {final_stats['relation_count']}")
        print(f"   最终特征数量: {final_stats['feature_count']}")
        print(f"   最终属性数量: {final_stats['attribute_count']}")
        
        print("\n" + "=" * 60)
        print("所有测试通过！")
        print("=" * 60)
        
        return True
        
    except Exception as e:
        print(f"\n测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # 清理临时目录
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            print(f"\n清理临时目录: {temp_dir}")


def test_repo_context():
    """测试 RepoContext"""
    print("\n" + "=" * 60)
    print("测试 RepoContext")
    print("=" * 60)
    
    temp_dir = tempfile.mkdtemp(prefix="repo_test_")
    entity_dir = Path(temp_dir) / "entities"
    relation_dir = Path(temp_dir) / "relations"
    
    try:
        # 测试 RepoContext.from_directories
        repos = RepoContext.from_directories(
            entity_dir=entity_dir,
            relation_dir=relation_dir
        )
        
        print(f"RepoContext 创建成功")
        print(f"   entity 仓库: {type(repos.entity).__name__}")
        print(f"   relation 仓库: {type(repos.relation).__name__}")
        print(f"   feature 仓库: {type(repos.feature).__name__}")
        print(f"   attribute 仓库: {type(repos.attribute).__name__}")
        
        # 测试基本功能
        print("\n测试基本仓库功能...")
        
        # 测试实体仓库
        test_entity_id = "test_entity"
        test_entity_data = {
            "id": test_entity_id,
            "type": "test",
            "sources": [],
            "features": [],
            "attributes": []
        }
        
        # 保存实体
        success = repos.entity.save(test_entity_data)
        print(f"   保存实体: {success}")
        assert success == True, "保存实体失败"
        
        # 检查实体是否存在
        exists = repos.entity.exists(test_entity_id)
        print(f"   实体存在: {exists}")
        assert exists == True, "实体应该存在"
        
        # 加载实体
        success, loaded_data = repos.entity.load(test_entity_id)
        print(f"   加载实体: {success}")
        assert success == True, "加载实体失败"
        assert loaded_data['id'] == test_entity_id, "加载的实体ID不匹配"
        
        # 列出实体ID
        entity_ids = repos.entity.list_ids()
        print(f"   实体ID列表: {entity_ids}")
        assert test_entity_id in entity_ids, "实体ID应该在列表中"
        
        print("\nRepoContext 测试通过！")
        return True
        
    except Exception as e:
        print(f"\nRepoContext 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # 清理临时目录
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)


def main():
    """主测试函数"""
    print("开始测试知识图谱核心模块...")
    
    # 测试 RepoContext
    repo_test_passed = test_repo_context()
    
    # 测试核心操作
    core_test_passed = test_basic_operations()
    
    # 汇总结果
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    print(f"RepoContext 测试: {'通过' if repo_test_passed else '失败'}")
    print(f"核心操作测试: {'通过' if core_test_passed else '失败'}")
    
    all_passed = repo_test_passed and core_test_passed
    
    if all_passed:
        print("\n✅ 所有测试通过！")
        return 0
    else:
        print("\n❌ 部分测试失败！")
        return 1


if __name__ == "__main__":
    sys.exit(main())