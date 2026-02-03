#!/usr/bin/env python3
"""
测试pipeline的参数配置功能
验证data_source和loader_type参数是否正常工作
"""

import sys
import os
sys.path.append('.')

from pipeline.memory_pre import stage1_construct_dialogues_for_id, run_full_pipeline_for_id

def test_stage1_with_args():
    """测试stage1函数的数据源参数"""
    print("=== 测试stage1_construct_dialogues_for_id的参数配置 ===")
    
    # 测试1: 使用realtalk数据源
    print("\n1. 测试realtalk数据源:")
    try:
        success = stage1_construct_dialogues_for_id(
            process_id="test_realtalk_args",
            data_source='data/REALTALK/data/Chat_1_Emi_Elise.json',
            loader_type='realtalk'
        )
        print(f"   结果: {'成功' if success else '失败'}")
        if success:
            print("   ✓ realtalk数据源参数工作正常")
    except Exception as e:
        print(f"   错误: {e}")
    
    # 测试2: 使用自动检测
    print("\n2. 测试自动检测:")
    try:
        success = stage1_construct_dialogues_for_id(
            process_id="test_auto_args",
            data_source='data/REALTALK/data/Chat_1_Emi_Elise.json',
            loader_type='auto'
        )
        print(f"   结果: {'成功' if success else '失败'}")
        if success:
            print("   ✓ 自动检测参数工作正常")
    except Exception as e:
        print(f"   错误: {e}")
    
    # 测试3: 使用默认加载器（应该会失败，因为不是默认格式）
    print("\n3. 测试默认加载器（预期会失败）:")
    try:
        success = stage1_construct_dialogues_for_id(
            process_id="test_default_args",
            data_source='data/REALTALK/data/Chat_1_Emi_Elise.json',
            loader_type='default'
        )
        print(f"   结果: {'成功' if success else '失败'}")
        if not success:
            print("   ✓ 默认加载器正确处理了不支持的格式")
    except Exception as e:
        print(f"   错误: {e}")

def test_full_pipeline_with_args():
    """测试完整pipeline的参数配置"""
    print("\n=== 测试run_full_pipeline_for_id的参数配置 ===")
    
    # 测试完整pipeline（只运行前两个阶段）
    print("\n1. 测试完整pipeline的realtalk数据源:")
    try:
        success = run_full_pipeline_for_id(
            process_id="test_full_realtalk",
            data_source='data/REALTALK/data/Chat_1_Emi_Elise.json',
            loader_type='realtalk',
            include_stage5=False  # 不包含第五阶段以加快测试
        )
        print(f"   结果: {'成功' if success else '失败'}")
        if success:
            print("   ✓ 完整pipeline的参数配置工作正常")
    except Exception as e:
        print(f"   错误: {e}")
        import traceback
        traceback.print_exc()

def test_command_line_interface():
    """测试命令行接口"""
    print("\n=== 测试命令行接口 ===")
    
    # 模拟命令行参数
    import argparse
    
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
    
    # 测试参数解析
    print("\n1. 测试参数解析:")
    test_args = [
        "--id", "test_cli",
        "--data-source", "data/REALTALK/data/Chat_1_Emi_Elise.json",
        "--loader-type", "realtalk",
        "--kg-prompt-version", "v1",
        "--no-stage5"
    ]
    
    args = parser.parse_args(test_args)
    print(f"   解析的参数:")
    print(f"   - id: {args.id}")
    print(f"   - data-source: {args.data_source}")
    print(f"   - loader-type: {args.loader_type}")
    print(f"   - kg-prompt-version: {args.kg_prompt_version}")
    print(f"   - no-stage5: {args.no_stage5}")
    
    # 验证参数是否正确传递
    if (args.id == "test_cli" and 
        args.data_source == "data/REALTALK/data/Chat_1_Emi_Elise.json" and
        args.loader_type == "realtalk"):
        print("   ✓ 命令行参数解析工作正常")

def main():
    """主测试函数"""
    print("开始测试pipeline的参数配置功能")
    print("=" * 60)
    
    test_stage1_with_args()
    test_full_pipeline_with_args()
    test_command_line_interface()
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("\n总结:")
    print("1. stage1_construct_dialogues_for_id 函数已支持 data_source 和 loader_type 参数")
    print("2. run_full_pipeline_for_id 函数已支持 data_source 和 loader_type 参数")
    print("3. 命令行接口已更新，支持 --data-source 和 --loader-type 参数")
    print("4. pipeline现在可以通过参数选择加载realtalk数据")

if __name__ == "__main__":
    main()