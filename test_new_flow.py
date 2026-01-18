#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试新的流程
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到路径
sys.path.append(str(Path(__file__).parent))

def test_imports():
    """测试导入"""
    print("测试导入...")
    
    try:
        from memory.build_memory.build_episode import scan_and_build_episodes
        print("✓ 成功导入 scan_and_build_episodes")
        
        from memory.build_memory.qualify_episode import scan_and_qualify_episodes
        print("✓ 成功导入 scan_and_qualify_episodes")
        
        from memory.build_memory.filter_episode import scan_and_filter_episodes
        print("✓ 成功导入 scan_and_filter_episodes")
        
        from utils.memory_build_utils import build_episodes_with_id
        print("✓ 成功导入 build_episodes_with_id")
        
        return True
    except Exception as e:
        print(f"✗ 导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_directory_structure():
    """测试目录结构创建"""
    print("\n测试目录结构...")
    
    try:
        from utils.memory_build_utils import build_episodes_with_id
        
        # 创建一个测试ID
        test_id = "test_flow_001"
        
        # 构建预期的目录路径
        project_root = Path(__file__).parent
        dialogues_root = project_root / "data" / "memory" / test_id / "dialogues"
        episodes_root = project_root / "data" / "memory" / test_id / "episodes"
        
        print(f"项目根目录: {project_root}")
        print(f"预期对话目录: {dialogues_root}")
        print(f"预期Episodes目录: {episodes_root}")
        
        # 检查目录是否存在（应该不存在）
        if dialogues_root.exists():
            print(f"警告: 对话目录已存在: {dialogues_root}")
        else:
            print("✓ 对话目录不存在（正常）")
            
        if episodes_root.exists():
            print(f"警告: Episodes目录已存在: {episodes_root}")
        else:
            print("✓ Episodes目录不存在（正常）")
            
        return True
    except Exception as e:
        print(f"✗ 测试目录结构失败: {e}")
        return False

def test_main_update_help():
    """测试main_update.py的帮助信息"""
    print("\n测试main_update.py帮助信息...")
    
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "main_updata.py", "--help"],
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        
        if result.returncode == 0:
            print("✓ main_update.py帮助信息正常")
            print(f"输出:\n{result.stdout[:200]}...")
            return True
        else:
            print(f"✗ main_update.py帮助信息失败: {result.stderr}")
            return False
    except Exception as e:
        print(f"✗ 测试main_update.py帮助信息失败: {e}")
        return False

def main():
    """主测试函数"""
    print("=" * 50)
    print("开始测试新的流程")
    print("=" * 50)
    
    # 测试导入
    if not test_imports():
        print("\n导入测试失败，退出")
        return False
    
    # 测试目录结构
    if not test_directory_structure():
        print("\n目录结构测试失败")
        return False
    
    # 测试main_update.py帮助信息
    if not test_main_update_help():
        print("\nmain_update.py帮助信息测试失败")
        return False
    
    print("\n" + "=" * 50)
    print("所有测试通过！")
    print("=" * 50)
    print("\n下一步:")
    print("1. 运行完整流程: python main_updata.py --id test_flow_001 --full")
    print("2. 仅运行episodes构建: python main_updata.py --id test_flow_002 --episodes-only")
    print("3. 查看生成的目录结构: data/memory/{id}/")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)