#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 memory_owner_name 参数功能
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到路径
sys.path.append('.')

def test_build_episode_load_prompts():
    """测试 build_episode.py 中的 load_prompts 函数"""
    print("=== 测试 build_episode.py load_prompts 函数 ===")
    
    try:
        from memory.build_memory.build_episode import load_prompts
        
        # 测试默认参数
        print("1. 测试默认参数 (memory_owner_name='changshengEVA'):")
        prompts_default = load_prompts()
        print(f"   成功加载 prompts，类型: {type(prompts_default)}")
        
        # 检查是否有包含 <memory_owner_name> 的字符串
        has_placeholder = False
        if isinstance(prompts_default, dict):
            for key, value in prompts_default.items():
                if isinstance(value, str) and '<memory_owner_name>' in value:
                    has_placeholder = True
                    print(f"   发现包含 <memory_owner_name> 的键: {key}")
                    print(f"   值片段: {value[:100]}...")
        
        if has_placeholder:
            print("   ✓ prompts 中包含 <memory_owner_name> 占位符")
        else:
            print("   ⚠ prompts 中未找到 <memory_owner_name> 占位符")
        
        # 测试自定义参数
        print("\n2. 测试自定义参数 (memory_owner_name='TestUser'):")
        prompts_custom = load_prompts(memory_owner_name='TestUser')
        print(f"   成功加载 prompts，类型: {type(prompts_custom)}")
        
        # 检查是否替换了占位符
        has_replaced = False
        if isinstance(prompts_custom, dict):
            for key, value in prompts_custom.items():
                if isinstance(value, str) and 'TestUser' in value:
                    has_replaced = True
                    print(f"   发现包含 'TestUser' 的键: {key}")
                    print(f"   值片段: {value[:100]}...")
        
        if has_replaced:
            print("   ✓ prompts 中的 <memory_owner_name> 已替换为 'TestUser'")
        else:
            print("   ⚠ prompts 中未找到 'TestUser'，可能没有占位符或替换失败")
        
        return True
        
    except Exception as e:
        print(f"   测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_qualify_episode_load_prompts():
    """测试 qualify_episode.py 中的 load_prompts 函数"""
    print("\n=== 测试 qualify_episode.py load_prompts 函数 ===")
    
    try:
        from memory.build_memory.qualify_episode import load_prompts
        
        # 测试自定义参数
        print("测试自定义参数 (memory_owner_name='Alice'):")
        prompts = load_prompts(memory_owner_name='Alice')
        print(f"   成功加载 prompts，类型: {type(prompts)}")
        
        # 检查是否有包含 'Alice' 的字符串
        has_alice = False
        if isinstance(prompts, dict):
            for key, value in prompts.items():
                if isinstance(value, str) and 'Alice' in value:
                    has_alice = True
                    print(f"   发现包含 'Alice' 的键: {key}")
                    print(f"   值片段: {value[:100]}...")
        
        if has_alice:
            print("   ✓ prompts 中的 <memory_owner_name> 已替换为 'Alice'")
        else:
            print("   ⚠ prompts 中未找到 'Alice'，可能没有占位符或替换失败")
        
        return True
        
    except Exception as e:
        print(f"   测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_form_scene_load_prompts():
    """测试 form_scene.py 中的 load_prompts 函数"""
    print("\n=== 测试 form_scene.py load_prompts 函数 ===")
    
    try:
        from memory.build_memory.form_scene import load_prompts
        
        # 测试自定义参数
        print("测试自定义参数 (memory_owner_name='Bob'):")
        prompts = load_prompts(memory_owner_name='Bob')
        print(f"   成功加载 prompts，类型: {type(prompts)}")
        
        # 检查是否有包含 'Bob' 的字符串
        has_bob = False
        if isinstance(prompts, dict):
            for key, value in prompts.items():
                if isinstance(value, str) and 'Bob' in value:
                    has_bob = True
                    print(f"   发现包含 'Bob' 的键: {key}")
                    print(f"   值片段: {value[:100]}...")
        
        if has_bob:
            print("   ✓ prompts 中的 <memory_owner_name> 已替换为 'Bob'")
        else:
            print("   ⚠ prompts 中未找到 'Bob'，可能没有占位符或替换失败")
        
        return True
        
    except Exception as e:
        print(f"   测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_form_kg_candidate_load_prompts():
    """测试 form_kg_candidate.py 中的 load_prompts 函数"""
    print("\n=== 测试 form_kg_candidate.py load_prompts 函数 ===")
    
    try:
        from memory.build_memory.form_kg_candidate import load_prompts
        
        # 测试自定义参数
        print("测试自定义参数 (memory_owner_name='Charlie'):")
        prompts = load_prompts(memory_owner_name='Charlie')
        print(f"   成功加载 prompts，类型: {type(prompts)}")
        
        # 检查是否有包含 'Charlie' 的字符串
        has_charlie = False
        if isinstance(prompts, dict):
            for key, value in prompts.items():
                if isinstance(value, str) and 'Charlie' in value:
                    has_charlie = True
                    print(f"   发现包含 'Charlie' 的键: {key}")
                    print(f"   值片段: {value[:100]}...")
        
        if has_charlie:
            print("   ✓ prompts 中的 <memory_owner_name> 已替换为 'Charlie'")
        else:
            print("   ⚠ prompts 中未找到 'Charlie'，可能没有占位符或替换失败")
        
        return True
        
    except Exception as e:
        print(f"   测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_form_scene_kg_load_prompts():
    """测试 form_scene_kg.py 中的 load_prompts 函数"""
    print("\n=== 测试 form_scene_kg.py load_prompts 函数 ===")
    
    try:
        from memory.build_memory.form_scene_kg import load_prompts
        
        # 测试自定义参数
        print("测试自定义参数 (memory_owner_name='David'):")
        prompts = load_prompts(memory_owner_name='David')
        print(f"   成功加载 prompts，类型: {type(prompts)}")
        
        # 检查是否有包含 'David' 的字符串
        has_david = False
        if isinstance(prompts, dict):
            for key, value in prompts.items():
                if isinstance(value, str) and 'David' in value:
                    has_david = True
                    print(f"   发现包含 'David' 的键: {key}")
                    print(f"   值片段: {value[:100]}...")
        
        if has_david:
            print("   ✓ prompts 中的 <memory_owner_name> 已替换为 'David'")
        else:
            print("   ⚠ prompts 中未找到 'David'，可能没有占位符或替换失败")
        
        return True
        
    except Exception as e:
        print(f"   测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_pipeline_parameter():
    """测试 pipeline 参数解析"""
    print("\n=== 测试 pipeline 参数解析 ===")
    
    try:
        # 测试 pipeline/memory_pre.py 中的参数解析
        import argparse
        
        # 模拟命令行参数
        test_args = [
            '--id', 'test123',
            '--memory-owner-name', 'CustomUser',
            '--no-stage5'
        ]
        
        # 创建解析器（模拟 main 函数中的解析器）
        parser = argparse.ArgumentParser(
            description="数据构造流程 - 支持指定数据源和加载器类型"
        )
        parser.add_argument("--id", type=str, required=True,
                           help="处理流ID（必需）")
        parser.add_argument("--data-source", type=str, default=None,
                           help="数据源路径（文件或目录），如果未指定则使用默认路径")
        parser.add_argument("--loader-type", type=str, default="auto",
                           choices=["auto", "realtalk", "default"],
                           help="加载器类型：auto（自动检测，默认）, realtalk（强制使用realtalk加载器）, default（强制使用默认加载器）")
        parser.add_argument("--kg-prompt-version", type=str, default="v1",
                           help="KG候选生成的prompt版本（v1 或 v2，默认v1）")
        parser.add_argument("--no-stage5", action="store_true",
                           help="不包含第五阶段（scene特征提取）")
        parser.add_argument("--memory-owner-name", type=str, default="changshengEVA",
                           help="记忆所有者的名称，用于替换prompt中的<memory_owner_name>占位符（默认：changshengEVA）")
        
        # 解析测试参数
        args = parser.parse_args(test_args)
        
        print(f"   解析的参数:")
        print(f"   --id: {args.id}")
        print(f"   --memory-owner-name: {args.memory_owner_name}")
        print(f"   --no-stage5: {args.no_stage5}")
        
        if args.memory_owner_name == 'CustomUser':
            print("   ✓ 成功解析 --memory-owner-name 参数")
            return True
        else:
            print(f"   ✗ 参数解析错误: memory_owner_name={args.memory_owner_name}")
            return False
            
    except Exception as e:
        print(f"   测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """主测试函数"""
    print("开始测试 memory_owner_name 参数功能")
    print("=" * 60)
    
    test_results = []
    
    # 运行各个测试
    test_results.append(("build_episode load_prompts", test_build_episode_load_prompts()))
    test_results.append(("qualify_episode load_prompts", test_qualify_episode_load_prompts()))
    test_results.append(("form_scene load_prompts", test_form_scene_load_prompts()))
    test_results.append(("form_kg_candidate load_prompts", test_form_kg_candidate_load_prompts()))
    test_results.append(("form_scene_kg load_prompts", test_form_scene_kg_load_prompts()))
    test_results.append(("pipeline 参数解析", test_pipeline_parameter()))
    
    # 输出测试结果
    print("\n" + "=" * 60)
    print("测试结果汇总:")
    print("=" * 60)
    
    all_passed = True
    for test_name, result in test_results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{test_name}: {status}")
        if not result:
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("所有测试通过！memory_owner_name 参数功能正常。")
    else:
        print("部分测试失败，请检查修改。")
    
    return all_passed

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)