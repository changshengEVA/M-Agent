#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 dialog_history.json 加载对话数据，并转换为 dialogue 格式
"""

import json
import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

def load_dialog_history(file_path: str = None) -> List[Dict[str, Any]]:
    """
    从 dialog_history.json 加载原始对话数据
    
    Args:
        file_path: JSON 文件路径，如果为 None 则使用默认路径
        
    Returns:
        原始对话数据列表，每个元素是一个字典
    """
    if file_path is None:
        # 默认路径：项目根目录下的 data/memory/dialog_history.json
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        file_path = os.path.join(project_root, "data", "memory", "dialog_history.json")
    
    if not os.path.exists(file_path):
        logger.error(f"文件不存在: {file_path}")
        return []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        logger.info(f"成功从 {file_path} 加载 {len(data)} 条对话记录")
        return data
    except Exception as e:
        logger.error(f"加载文件失败: {e}")
        return []

def parse_history_string(history_str: str) -> List[Dict[str, str]]:
    """
    解析历史字符串格式为结构化的 turns
    """
    turns = []
    lines = history_str.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        if ':' in line:
            speaker, text = line.split(':', 1)
            speaker = speaker.strip()
            text = text.strip()
            
            if speaker.startswith('changshengEVA'):
                speaker = 'changshengEVA'
            elif speaker.startswith('ZQR'):
                speaker = 'ZQR'
            
            turns.append({'speaker': speaker, 'text': text})
        else:
            if turns:
                turns[-1]['text'] += '\n' + line
    
    return turns

def extract_timestamp_from_time_str(time_str: str) -> datetime:
    """
    将时间字符串转换为 datetime 对象
    """
    if ' ' in time_str:
        time_str = time_str.replace(' ', 'T')
    
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f"
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue
    
    logger.warning(f"无法解析时间字符串: {time_str}, 使用当前时间")
    return datetime.now()

def construct_dialogue_from_entry(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    将原始条目构造为 dialogue 格式
    """
    try:
        time_str = entry.get('kwargs', {}).get('time')
        if not time_str:
            logger.warning("条目缺少时间信息，跳过")
            return None
        
        start_dt = extract_timestamp_from_time_str(time_str)
        
        turns = []
        if 'kwargs' in entry and 'turns' in entry['kwargs']:
            raw_turns = entry['kwargs']['turns']
            for i, turn in enumerate(raw_turns):
                turn_dt = extract_timestamp_from_time_str(turn.get('timestamp', time_str))
                turns.append({
                    'turn_id': i,
                    'speaker': turn.get('speaker', 'unknown'),
                    'text': turn.get('text', ''),
                    'timestamp': turn_dt.isoformat()
                })
        else:
            history_str = entry.get('history', '')
            if not history_str:
                logger.warning("条目缺少 history 信息，跳过")
                return None
            
            parsed_turns = parse_history_string(history_str)
            if not parsed_turns:
                logger.warning("无法解析 history 字符串，跳过")
                return None
            
            for i, turn in enumerate(parsed_turns):
                turn_dt = start_dt + timedelta(seconds=i * 5)
                turns.append({
                    'turn_id': i,
                    'speaker': turn['speaker'],
                    'text': turn['text'],
                    'timestamp': turn_dt.isoformat()
                })
        
        if not turns:
            logger.warning("没有有效的 turns，跳过")
            return None
        
        participants_set = set()
        for turn in turns:
            participants_set.add(turn['speaker'])
        participants = list(participants_set)
        
        user_id = "ZQR" if "ZQR" in participants else participants[0] if participants else "unknown"
        
        dialogue_id = f"dlg_{start_dt.strftime('%Y-%m-%d_%H-%M-%S')}"
        
        dialogue = {
            "dialogue_id": dialogue_id,
            "user_id": user_id,
            "participants": participants,
            "meta": {
                "start_time": turns[0]['timestamp'],
                "end_time": turns[-1]['timestamp'],
                "language": "zh",
                "platform": "web",
                "version": 1
            },
            "turns": turns
        }
        
        return dialogue
        
    except Exception as e:
        logger.error(f"构造 dialogue 时出错: {e}")
        return None

def load_dialogues(file_path: str = None) -> List[Dict[str, Any]]:
    """
    加载并构造 dialogue 列表
    
    Args:
        file_path: JSON 文件路径，如果为 None 则使用默认路径
        
    Returns:
        dialogue 列表，每个元素是构造好的 dialogue 字典
    """
    raw_data = load_dialog_history(file_path)
    dialogues = []
    
    for entry in raw_data:
        dialogue = construct_dialogue_from_entry(entry)
        if dialogue:
            dialogues.append(dialogue)
    
    logger.info(f"成功构造 {len(dialogues)} 个 dialogue（原始数据 {len(raw_data)} 条）")
    return dialogues

if __name__ == "__main__":
    # 测试代码
    import sys
    logging.basicConfig(level=logging.INFO)
    
    dialogues = load_dialogues()
    print(f"构造了 {len(dialogues)} 个 dialogue")
    if dialogues:
        print("第一个 dialogue 示例:")
        print(json.dumps(dialogues[0], ensure_ascii=False, indent=2))