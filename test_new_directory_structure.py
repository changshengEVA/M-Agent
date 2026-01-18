#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试新的目录结构（不使用by_data中间目录）
"""

import os
import json
import shutil
from pathlib import Path
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).parent

def test_save_dialogue():
    """测试保存对话到新目录结构"""
    logger.info("测试保存对话到新目录结构...")
    
    # 导入save_dialogue函数
    from utils.dialogue_utils import save_dialogue
    
    # 创建测试对话
    test_dialogue = {
        "dialogue_id": "test_dlg_001",
        "user_id": "test_user_001",
        "meta": {
            "start_time": "2025-12-01T10:30:00Z"
        },
        "turns": [
            {"turn_id": 1, "speaker": "user", "text": "Hello"},
            {"turn_id": 2, "speaker": "assistant", "text": "Hi there"}
        ]
    }
    
    # 目标目录
    target_dir = PROJECT_ROOT / "data" / "memory" / "test_dir" / "dialogues"
    
    # 保存对话
    success = save_dialogue(test_dialogue, str(target_dir))
    
    if success:
        logger.info("✓ 对话保存成功")
        
        # 验证文件路径
        expected_path = target_dir / "test_user_001" / "2025-12" / "test_dlg_001.json"
        if expected_path.exists():
            logger.info(f"✓ 文件保存在正确位置: {expected_path}")
            
            # 读取并验证内容
            with open(expected_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                if loaded["dialogue_id"] == "test_dlg_001":
                    logger.info("✓ 文件内容正确")
                else:
                    logger.error("✗ 文件内容不正确")
        else:
            logger.error(f"✗ 文件不存在: {expected_path}")
    else:
        logger.error("✗ 对话保存失败")
    
    return success

def test_build_episode_scan():
    """测试build_episode的扫描功能"""
    logger.info("测试build_episode的扫描功能...")
    
    # 导入scan_dialogue_files函数
    from memory.build_memory.build_episode import scan_dialogue_files
    
    # 创建测试目录结构
    test_dialogues_root = PROJECT_ROOT / "data" / "memory" / "test_scan" / "dialogues"
    
    # 创建新目录结构：直接是用户ID目录
    user_dir = test_dialogues_root / "test_user_002"
    year_month_dir = user_dir / "2025-11"
    year_month_dir.mkdir(parents=True, exist_ok=True)
    
    # 创建测试文件
    test_file = year_month_dir / "test_dlg_002.json"
    with open(test_file, 'w', encoding='utf-8') as f:
        json.dump({"dialogue_id": "test_dlg_002", "user_id": "test_user_002"}, f)
    
    # 扫描文件
    dialogue_files = scan_dialogue_files(test_dialogues_root)
    
    if dialogue_files:
        logger.info(f"✓ 扫描到 {len(dialogue_files)} 个对话文件")
        for file in dialogue_files:
            logger.info(f"  - {file}")
        
        # 验证是否扫描到正确的文件
        found = any(str(file).endswith("test_dlg_002.json") for file in dialogue_files)
        if found:
            logger.info("✓ 正确扫描到测试文件")
        else:
            logger.error("✗ 未扫描到测试文件")
    else:
        logger.error("✗ 未扫描到任何对话文件")
    
    return bool(dialogue_files)

def test_qualify_episode_find():
    """测试qualify_episode的查找功能"""
    logger.info("测试qualify_episode的查找功能...")
    
    # 导入find_dialogue_file函数
    from memory.build_memory.qualify_episode import find_dialogue_file
    
    # 创建测试目录结构
    test_dialogues_root = PROJECT_ROOT / "data" / "memory" / "test_find" / "dialogues"
    
    # 创建新目录结构：直接是用户ID目录
    user_dir = test_dialogues_root / "test_user_003"
    year_month_dir = user_dir / "2025-10"
    year_month_dir.mkdir(parents=True, exist_ok=True)
    
    # 创建测试文件
    test_file = year_month_dir / "test_dlg_003.json"
    with open(test_file, 'w', encoding='utf-8') as f:
        json.dump({"dialogue_id": "test_dlg_003", "user_id": "test_user_003"}, f)
    
    # 查找文件
    found_file = find_dialogue_file("test_dlg_003", test_dialogues_root)
    
    if found_file:
        logger.info(f"✓ 找到对话文件: {found_file}")
        
        # 验证路径
        if str(found_file).endswith("test_dlg_003.json"):
            logger.info("✓ 找到正确的文件")
        else:
            logger.error(f"✗ 找到的文件不正确: {found_file}")
    else:
        logger.error("✗ 未找到对话文件")
    
    return found_file is not None

def test_main_updata_structure():
    """测试main_updata.py的目录结构创建"""
    logger.info("测试main_updata.py的目录结构创建...")
    
    # 清理测试目录
    test_dir = PROJECT_ROOT / "data" / "memory" / "test_main"
    if test_dir.exists():
        shutil.rmtree(test_dir)
    
    try:
        # 直接调用main_updata.py中的函数来测试目录结构
        from main_updata import stage1_construct_dialogues_for_id
        
        # 模拟加载对话数据（不实际调用API）
        # 创建一个简单的对话列表
        test_dialogues = [
            {
                "dialogue_id": "test_main_dlg_001",
                "user_id": "test_main_user_001",
                "meta": {
                    "start_time": "2025-12-01T10:30:00Z"
                },
                "turns": []
            }
        ]
        
        # 临时替换load_dialogues函数
        import main_updata as mu
        original_load_dialogues = mu.load_dialogues
        
        def mock_load_dialogues():
            return test_dialogues
        
        mu.load_dialogues = mock_load_dialogues
        
        # 运行第一阶段
        success = stage1_construct_dialogues_for_id("test_main")
        
        # 恢复原始函数
        mu.load_dialogues = original_load_dialogues
        
        if success:
            logger.info("✓ 目录结构创建成功")
            
            # 验证目录结构
            dialogues_dir = test_dir / "dialogues"
            if dialogues_dir.exists():
                logger.info(f"✓ 对话目录存在: {dialogues_dir}")
                
                # 检查是否没有by_data子目录
                by_data_dir = dialogues_dir / "by_data"
                if not by_data_dir.exists():
                    logger.info("✓ 没有by_data中间目录（符合预期）")
                else:
                    logger.warning("⚠ 存在by_data目录（不符合预期）")
                
                # 检查是否有用户目录
                user_dirs = list(dialogues_dir.iterdir())
                if user_dirs:
                    logger.info(f"✓ 找到用户目录: {[d.name for d in user_dirs]}")
                    
                    # 检查文件是否保存
                    expected_file = dialogues_dir / "test_main_user_001" / "2025-12" / "test_main_dlg_001.json"
                    if expected_file.exists():
                        logger.info(f"✓ 对话文件保存成功: {expected_file}")
                    else:
                        logger.error(f"✗ 对话文件不存在: {expected_file}")
                else:
                    logger.warning("⚠ 没有用户目录")
            else:
                logger.error("✗ 对话目录不存在")
        else:
            logger.error("✗ 目录结构创建失败")
        
        return success
    except Exception as e:
        logger.error(f"✗ 测试main_updata.py目录结构时出错: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def cleanup():
    """清理测试目录"""
    logger.info("清理测试目录...")
    
    test_dirs = [
        PROJECT_ROOT / "data" / "memory" / "test_dir",
        PROJECT_ROOT / "data" / "memory" / "test_scan",
        PROJECT_ROOT / "data" / "memory" / "test_find",
        PROJECT_ROOT / "data" / "memory" / "test_main",
    ]
    
    for test_dir in test_dirs:
        if test_dir.exists():
            shutil.rmtree(test_dir)
            logger.info(f"已清理: {test_dir}")

def main():
    """主测试函数"""
    logger.info("开始测试新的目录结构...")
    
    try:
        # 运行测试
        test1 = test_save_dialogue()
        test2 = test_build_episode_scan()
        test3 = test_qualify_episode_find()
        test4 = test_main_updata_structure()
        
        # 输出结果
        logger.info("=" * 50)
        logger.info("测试结果:")
        logger.info(f"1. 保存对话测试: {'✓ 通过' if test1 else '✗ 失败'}")
        logger.info(f"2. 构建扫描测试: {'✓ 通过' if test2 else '✗ 失败'}")
        logger.info(f"3. 资格查找测试: {'✓ 通过' if test3 else '✗ 失败'}")
        logger.info(f"4. 主流程目录结构测试: {'✓ 通过' if test4 else '✗ 失败'}")
        logger.info("=" * 50)
        
        # 清理
        cleanup()
        
        if all([test1, test2, test3, test4]):
            logger.info("所有测试通过！新的目录结构工作正常。")
            return True
        else:
            logger.error("部分测试失败，请检查代码。")
            return False
            
    except Exception as e:
        logger.error(f"测试过程中出错: {e}")
        import traceback
        logger.error(traceback.format_exc())
        cleanup()
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)