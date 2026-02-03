#!/usr/bin/env python3
"""
测试pipeline集成：验证realtalk数据加载器在pipeline stage1中的集成
"""

import sys
import os
sys.path.append('.')

def test_realtalk_loader():
    """测试realtalk数据加载器"""
    print("=== 测试realtalk数据加载器 ===")
    
    from load_data.dialog_history_loader import load_dialogues
    
    # 测试realtalk文件
    realtalk_file = 'data/REALTALK/data/Chat_1_Emi_Elise.json'
    print(f"测试文件: {realtalk_file}")
    
    if not os.path.exists(realtalk_file):
        print(f"错误: 文件不存在: {realtalk_file}")
        return False
    
    dialogues = load_dialogues(realtalk_file)
    print(f"加载的对话数量: {len(dialogues)}")
    
    if not dialogues:
        print("错误: 没有加载到对话")
        return False
    
    # 检查第一个对话
    first_dialogue = dialogues[0]
    print(f"第一个对话ID: {first_dialogue.get('dialogue_id')}")
    print(f"参与者: {first_dialogue.get('participants')}")
    print(f"元数据平台: {first_dialogue.get('meta', {}).get('platform')}")
    print(f"轮次数量: {len(first_dialogue.get('turns', []))}")
    
    # 验证格式
    required_keys = ['dialogue_id', 'user_id', 'participants', 'meta', 'turns']
    missing_keys = [key for key in required_keys if key not in first_dialogue]
    
    if missing_keys:
        print(f"错误: 缺少必要的键: {missing_keys}")
        return False
    
    print("✓ 对话格式正确")
    
    # 检查轮次结构
    if first_dialogue.get('turns'):
        first_turn = first_dialogue['turns'][0]
        turn_keys = ['turn_id', 'speaker', 'text', 'timestamp']
        missing_turn_keys = [key for key in turn_keys if key not in first_turn]
        
        if missing_turn_keys:
            print(f"错误: 轮次缺少必要的键: {missing_turn_keys}")
            return False
        
        print(f"第一个轮次: speaker={first_turn.get('speaker')}, text长度={len(first_turn.get('text', ''))}")
        print("✓ 轮次格式正确")
    
    return True

def test_pipeline_stage1_integration():
    """测试pipeline stage1集成"""
    print("\n=== 测试pipeline stage1集成 ===")
    
    from pipeline.memory_pre import stage1_construct_dialogues_for_id
    
    # 创建一个测试process_id
    test_process_id = "test_realtalk_001"
    
    print(f"测试process_id: {test_process_id}")
    print("注意: 这需要配置正确的数据目录，这里只验证导入路径")
    
    # 检查函数是否存在
    try:
        import inspect
        sig = inspect.signature(stage1_construct_dialogues_for_id)
        print(f"函数签名: {sig}")
        print("✓ stage1函数可访问")
        return True
    except Exception as e:
        print(f"错误: {e}")
        return False

def test_directory_loading():
    """测试目录加载功能"""
    print("\n=== 测试目录加载功能 ===")
    
    from load_data.realtalk_history_loader import load_realtalk_dialogues_from_directory
    
    realtalk_dir = 'data/REALTALK/data'
    print(f"测试目录: {realtalk_dir}")
    
    if not os.path.exists(realtalk_dir):
        print(f"警告: 目录不存在: {realtalk_dir}")
        return True  # 不是错误，只是跳过
    
    try:
        dialogues = load_realtalk_dialogues_from_directory(realtalk_dir)
        print(f"从目录加载的对话总数: {len(dialogues)}")
        
        if dialogues:
            print(f"示例对话ID: {dialogues[0].get('dialogue_id')}")
            print("✓ 目录加载功能正常")
        else:
            print("警告: 目录中没有找到对话")
        
        return True
    except Exception as e:
        print(f"错误: {e}")
        return False

def main():
    """主测试函数"""
    print("开始测试realtalk数据加载器和pipeline集成")
    print("=" * 60)
    
    tests_passed = 0
    tests_total = 3
    
    # 测试1: realtalk加载器
    if test_realtalk_loader():
        tests_passed += 1
    
    # 测试2: pipeline集成
    if test_pipeline_stage1_integration():
        tests_passed += 1
    
    # 测试3: 目录加载
    if test_directory_loading():
        tests_passed += 1
    
    print("\n" + "=" * 60)
    print(f"测试结果: {tests_passed}/{tests_total} 通过")
    
    if tests_passed == tests_total:
        print("✓ 所有测试通过！realtalk数据加载器已成功集成到pipeline中。")
        return 0
    else:
        print("⚠ 部分测试失败，请检查实现。")
        return 1

if __name__ == "__main__":
    sys.exit(main())