#!/usr/bin/env python3
"""
测试在根目录下调用KG_data/nlp_to_kg.py中的text_to_kg函数
"""

import sys
import os

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    # 尝试导入text_to_kg函数
    from KG_data.nlp_to_kg import text_to_kg
    print("成功导入text_to_kg函数")
    
    # 测试文本
    test_text = """
    张三是一位中国男性软件工程师，出生于1990年5月15日。他在北京工作，主要擅长Python和Java开发。
    李四是张三的同事，也是一位软件工程师，出生于1988年3月20日，女性，来自上海。
    他们一起合作开发了一个智能聊天机器人项目。
    """
    
    print("\n开始测试text_to_kg函数...")
    print("=" * 50)
    
    # 调用函数
    result = text_to_kg(test_text)
    
    print("\n测试结果:")
    print("=" * 50)
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("=" * 50)
    
    if result.get("success"):
        print("✓ 测试成功！")
    else:
        print("✗ 测试失败！")
        
except ImportError as e:
    print(f"导入失败: {e}")
    print("当前Python路径:")
    for p in sys.path:
        print(f"  {p}")
except Exception as e:
    print(f"测试过程中发生错误: {e}")
    import traceback
    traceback.print_exc()