#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简化版配置文件加载模块
只保留必要的功能
"""

import os
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def get_output_path(process_id: str, stage_name: str) -> Path:
    """
    获取输出目录路径（简化版）
    
    Args:
        process_id: 处理流ID
        stage_name: 阶段名称（如 "dialogues", "episodes", "kg_candidates"）
        
    Returns:
        输出目录路径
    """
    return Path("data/memory") / process_id / stage_name


if __name__ == "__main__":
    # 测试输出路径生成
    output_path = get_output_path("test_id", "kg_candidates")
    print(f"测试输出路径: {output_path}")