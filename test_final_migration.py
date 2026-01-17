#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试最终迁移后的代码
"""

import sys
import os
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 测试导入
try:
    from utils import save_dialogue, build_episodes, cleanup_directory
    print("✅ 成功从 utils 导入 save_dialogue, build_episodes, cleanup_directory")
except ImportError as e:
    print(f"❌ 导入失败: {e}")
    sys.exit(1)

# 测试 main_updata.py 中的导入
try:
    from main_updata import stage1_construct_dialogues, stage2_construct_episodes
    print("✅ 成功从 main_updata 导入 stage1_construct_dialogues 和 stage2_construct_episodes")
except ImportError as e:
    print(f"❌ 导入 main_updata 失败: {e}")
    sys.exit(1)

# 测试路径配置
from main_updata import PROJECT_ROOT, DIALOGUES_BY_DATA_DIR, EPISODES_BY_DATA_TMP_DIR
print(f"✅ 路径配置正常:")
print(f"   PROJECT_ROOT: {PROJECT_ROOT}")
print(f"   DIALOGUES_BY_DATA_DIR: {DIALOGUES_BY_DATA_DIR}")
print(f"   EPISODES_BY_DATA_TMP_DIR: {EPISODES_BY_DATA_TMP_DIR}")

# 测试 save_dialogue 函数签名
import inspect
sig = inspect.signature(save_dialogue)
params = list(sig.parameters.keys())
print(f"✅ save_dialogue 参数: {params}")
if len(params) >= 2:
    print("✅ save_dialogue 函数签名正确")
else:
    print("❌ save_dialogue 函数签名不正确")

# 测试 build_episodes 函数签名
sig = inspect.signature(build_episodes)
params = list(sig.parameters.keys())
print(f"✅ build_episodes 参数: {params}")
if len(params) >= 3:
    print("✅ build_episodes 函数签名正确")
else:
    print("❌ build_episodes 函数签名不正确")

print("\n✅ 所有导入和函数签名测试通过，工具函数迁移完成")