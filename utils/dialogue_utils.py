#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对话相关工具函数
"""

import json
import os
import logging
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger(__name__)

def save_dialogue(dialogue: Dict[str, Any], output_dir: str, default_output_dir: str = None) -> bool:
    """
    保存 dialogue 到文件
    
    文件路径: {output_dir}/{year}-{month}/{dialogue_id}.json
    
    Args:
        dialogue: dialogue 字典
        output_dir: 输出目录，如果为 None 则使用 default_output_dir
        default_output_dir: 默认的输出目录配置
        
    Returns:
        成功返回 True，失败返回 False
    """
    if output_dir is None:
        if default_output_dir is None:
            logger.error("未提供输出目录")
            return False
        output_dir = default_output_dir
    
    try:
        user_id = dialogue.get('user_id', 'unknown')
        dialogue_id = dialogue.get('dialogue_id', 'unknown')
        
        # 从 start_time 提取年月
        start_time = dialogue.get('meta', {}).get('start_time', '')
        if start_time:
            try:
                dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                year_month = dt.strftime('%Y-%m')
            except:
                year_month = 'unknown'
        else:
            year_month = 'unknown'
        
        # 创建目录
        year_month_dir = os.path.join(output_dir, year_month)
        os.makedirs(year_month_dir, exist_ok=True)
        
        # 保存文件
        filename = f"{dialogue_id}.json"
        filepath = os.path.join(year_month_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(dialogue, f, ensure_ascii=False, indent=2)
        
        logger.info(f"已保存 dialogue: {filepath}")
        return True
        
    except Exception as e:
        logger.error(f"保存 dialogue 失败: {e}")
        return False