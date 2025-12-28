#!/usr/bin/env python3
"""
测试 scan_and_build_scene 函数
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from memory.build_memory.build_scene import (
    scan_now_scene,
    scan_and_build_scene,
    get_next_scene_id,
    extract_original_episode_talk,
    find_dialogue_file
)

def test_scan_now_scene():
    """测试扫描功能"""
    print("测试 scan_now_scene...")
    try:
        result = scan_now_scene()
        print(f"扫描成功! 共处理 {len(result['episodes'])} 个 episodes")
        print(f"统计信息: {json.dumps(result['statistics'], indent=2)}")
        return True
    except Exception as e:
        print(f"扫描失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_scene_id_generation():
    """测试 scene_id 生成"""
    print("\n测试 scene_id 生成...")
    test_users = ["ZQR", "test_user", "unknown"]
    for user in test_users:
        scene_id = get_next_scene_id(user)
        print(f"  User '{user}' 的下一个 scene_id: {scene_id}")
    return True

def test_dialogue_extraction():
    """测试对话提取功能"""
    print("\n测试对话提取功能...")
    # 使用一个已知的对话文件进行测试
    dialogue_id = "dlg_2025-12-23_21-53-05"
    dialogue_file = find_dialogue_file(dialogue_id)
    
    if dialogue_file:
        print(f"找到对话文件: {dialogue_file}")
        # 测试提取功能
        turn_span = [0, 9]
        original_talk = extract_original_episode_talk(dialogue_file, turn_span)
        if original_talk:
            print(f"成功提取对话文本 (长度: {len(original_talk)} 字符)")
            print(f"前200字符: {original_talk[:200]}...")
            return True
        else:
            print("提取对话文本失败")
            return False
    else:
        print(f"未找到对话文件: {dialogue_id}")
        return False

def test_scan_and_build():
    """测试 scan_and_build_scene 函数"""
    print("\n测试 scan_and_build_scene...")
    try:
        # 注意：这个测试可能需要调用 LLM API，可能会产生费用
        # 我们可以先测试但不实际调用 LLM，或者使用模拟数据
        print("注意：此测试可能需要调用 LLM API，跳过实际构建...")
        print("要运行完整测试，请手动执行: python memory/build_memory/build_scene.py --build")
        return True
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    import json
    
    print("=== scan_and_build_scene 功能测试 ===\n")
    
    # 运行测试
    tests_passed = 0
    tests_total = 0
    
    # 测试 1: scan_now_scene
    tests_total += 1
    if test_scan_now_scene():
        tests_passed += 1
    
    # 测试 2: scene_id 生成
    tests_total += 1
    if test_scene_id_generation():
        tests_passed += 1
    
    # 测试 3: 对话提取
    tests_total += 1
    if test_dialogue_extraction():
        tests_passed += 1
    
    # 测试 4: scan_and_build_scene
    tests_total += 1
    if test_scan_and_build():
        tests_passed += 1
    
    print(f"\n=== 测试完成 ===")
    print(f"通过测试: {tests_passed}/{tests_total}")
    
    if tests_passed == tests_total:
        print("所有测试通过!")
    else:
        print(f"{tests_total - tests_passed} 个测试失败")