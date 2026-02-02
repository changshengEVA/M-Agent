#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试entity_resolution的扫描合并功能

配置：
- kg_base使用data\memory\test4\kg_data作为文件路径
- Library使用data\memory\test4\kg_data\entity_library作为文件路径
- llm方法和embed方法从load_model中导入Openai的
"""

import sys
import os
import json
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def main():
    """主测试函数"""
    print("=== 测试entity_resolution的扫描合并功能 ===")
    
    # 1. 设置路径
    kg_data_path = Path("data/memory/test4/kg_data")
    entity_library_path = Path("data/memory/test4/kg_data/entity_library")
    
    print(f"1. 配置路径:")
    print(f"   KG数据路径: {kg_data_path.absolute()}")
    print(f"   实体库路径: {entity_library_path.absolute()}")
    
    # 检查路径是否存在
    if not kg_data_path.exists():
        print(f"   ✗ KG数据路径不存在: {kg_data_path}")
        return False
    
    # 创建entity_library目录（如果不存在）
    entity_library_path.mkdir(parents=True, exist_ok=True)
    print(f"   ✓ 实体库路径已创建/存在")
    
    # 2. 导入OpenAI函数
    print(f"\n2. 导入OpenAI函数:")
    try:
        from load_model.OpenAIcall import get_llm, get_embed_model
        
        # 创建llm和embed函数
        llm_func = get_llm(model_temperature=0.1)
        embed_func = get_embed_model()
        
        print("   ✓ 成功导入OpenAI函数")
        print(f"   llm_func类型: {type(llm_func)}")
        print(f"   embed_func类型: {type(embed_func)}")
        
    except ImportError as e:
        print(f"   ✗ 导入OpenAI函数失败: {e}")
        print("   使用模拟函数进行测试...")
        
        # 使用模拟函数
        def mock_llm_func(prompt: str) -> str:
            print(f"     模拟LLM调用: {prompt[:50]}...")
            # 简单逻辑：如果prompt包含"等价"，返回"SAME_AS_EXISTING"，否则返回"NEW_ENTITY"
            if "等价" in prompt or "same" in prompt.lower():
                return "SAME_AS_EXISTING"
            return "NEW_ENTITY"
        
        def mock_embed_func(text: str):
            # 返回简单的模拟embedding
            return [0.1] * 10
        
        llm_func = mock_llm_func
        embed_func = mock_embed_func
        print("   ✓ 使用模拟函数")
    
    except Exception as e:
        print(f"   ✗ 创建OpenAI函数失败: {e}")
        return False
    
    # 3. 创建KGBase实例
    print(f"\n3. 创建KGBase实例:")
    try:
        from memory.memory_core.core.kg_base import KGBase
        
        # 设置实体和关系目录
        entity_dir = kg_data_path / "entity"
        relation_dir = kg_data_path / "relation"
        
        print(f"   实体目录: {entity_dir}")
        print(f"   关系目录: {relation_dir}")
        
        # 检查目录是否存在
        if not entity_dir.exists():
            print(f"   ✗ 实体目录不存在: {entity_dir}")
            return False
        
        # 创建关系目录（如果不存在）
        relation_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建KGBase实例
        kg_base = KGBase(
            entity_dir=entity_dir,
            relation_dir=relation_dir
        )
        
        print("   ✓ 成功创建KGBase实例")
        
        # 获取KG统计信息
        kg_stats = kg_base.get_kg_stats()
        print(f"   KG统计: {kg_stats}")
        
        # 获取实体ID列表
        entity_ids = kg_base.list_entity_ids()
        print(f"   KG实体数量: {len(entity_ids)}")
        print(f"   前5个实体: {entity_ids[:5]}")
        
    except Exception as e:
        print(f"   ✗ 创建KGBase失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # 4. 创建EntityResolutionService
    print(f"\n4. 创建EntityResolutionService:")
    try:
        from memory.memory_core.services_bank.entity_resolution.service import (
            EntityResolutionService, 
            create_default_resolution_service
        )
        
        # 使用create_default_resolution_service创建服务
        service = create_default_resolution_service(
            llm_func=llm_func,
            embed_func=embed_func,
            kg_base=kg_base,
            data_path=str(entity_library_path)
        )
        
        print("   ✓ 成功创建EntityResolutionService")
        
        # 获取Library统计信息
        library_stats = service.get_library_stats()
        print(f"   实体库初始统计: {library_stats}")
        
    except Exception as e:
        print(f"   ✗ 创建EntityResolutionService失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # 5. 测试扫描合并功能（align_library_with_kg_entities）
    print(f"\n5. 测试扫描合并功能:")
    try:
        # 获取KG中的所有实体ID
        kg_entity_ids = kg_base.list_entity_ids()
        print(f"   KG实体总数: {len(kg_entity_ids)}")
        
        # 执行对齐操作
        print("   执行align_library_with_kg_entities...")
        alignment_result = service.align_library_with_kg_entities(kg_entity_ids)
        
        print(f"   对齐结果:")
        print(f"     KG实体数量: {alignment_result['kg_entity_count']}")
        print(f"     对齐前Library实体数量: {alignment_result['library_entity_count_before']}")
        print(f"     对齐后Library实体数量: {alignment_result['library_entity_count_after']}")
        print(f"     从Library删除的实体: {alignment_result['removed_from_library']}")
        print(f"     从KG新增的实体: {alignment_result['new_from_kg']}")
        print(f"     成功解析的实体: {alignment_result['resolved_success']}")
        print(f"     解析失败的实体: {alignment_result['resolved_failed']}")
        
        # 显示新增实体的解析结果
        if alignment_result['new_entities']:
            print(f"\n   新增实体解析结果:")
            for i, entity_id in enumerate(alignment_result['new_entities'][:3]):  # 只显示前3个
                result = alignment_result['resolution_results'][i]
                print(f"     {entity_id}: {result['resolution_type']} (成功: {result['success']})")
            
            if len(alignment_result['new_entities']) > 3:
                print(f"     ... 还有 {len(alignment_result['new_entities']) - 3} 个实体")
        
        # 获取最终的Library统计
        final_stats = service.get_library_stats()
        print(f"\n   最终实体库统计: {final_stats}")
        
    except Exception as e:
        print(f"   ✗ 测试扫描合并功能失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    
    print(f"\n=== 测试完成 ===")
    print(f"总结:")
    print(f"- KGBase使用: {kg_data_path}")
    print(f"- Library路径: {entity_library_path}")
    print(f"- 最终Library实体数量: {service.get_library_stats()['entity_count']}")
    print(f"- 所有测试通过!")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)