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

def load_default_dialogues(file_path: str = None) -> List[Dict[str, Any]]:
    """
    使用默认加载器加载并构造 dialogue 列表
    
    Args:
        file_path: JSON 文件路径，如果为 None 则使用默认路径。
        
    Returns:
        dialogue 列表，每个元素是构造好的 dialogue 字典
    """
    logger.info(f"使用默认加载器: {file_path}")
    raw_data = load_dialog_history(file_path)
    dialogues = []
    
    for entry in raw_data:
        dialogue = construct_dialogue_from_entry(entry)
        if dialogue:
            dialogues.append(dialogue)
    
    logger.info(f"成功构造 {len(dialogues)} 个 dialogue（原始数据 {len(raw_data)} 条）")
    return dialogues


def load_dialogues(file_path: str = None, loader_type: str = "auto") -> List[Dict[str, Any]]:
    """
    统一的 dialogue 加载接口，根据 loader_type 调用不同的加载器
    
    Args:
        file_path: JSON 文件路径，如果为 None 则使用默认路径。
        loader_type: 加载器类型，可选值：
                    - "auto": 自动检测（默认）
                    - "realtalk": 强制使用 realtalk 加载器
                    - "locomo": 强制使用 locomo 加载器
                    - "default": 强制使用默认加载器
        
    Returns:
        dialogue 列表，每个元素是构造好的 dialogue 字典
    """
    # 检测是否为 realtalk 数据
    def is_realtalk_path(path: str) -> bool:
        if path is None:
            return False
        path_lower = path.lower()
        # locomo 路径优先交给 locomo loader
        if "locomo" in path_lower:
            return False
        if "realtalk" in path_lower:
            return True
        import os
        filename = os.path.basename(path)
        if filename.startswith("Chat_") and filename.endswith(".json"):
            return True
        return False

    def is_locomo_path(path: str) -> bool:
        if path is None:
            return False
        path_lower = path.lower()
        if "locomo" in path_lower:
            return True
        import os
        filename = os.path.basename(path_lower)
        if filename == "locomo10.json":
            return True
        return False
    
    # 根据 loader_type 决定使用哪个加载器
    if loader_type == "realtalk":
        # 强制使用 realtalk 加载器
        if file_path and os.path.isdir(file_path):
            from .realtalk_history_loader import load_realtalk_dialogues_from_directory
            logger.info(f"使用 realtalk 目录加载器: {file_path}")
            return load_realtalk_dialogues_from_directory(file_path)
        else:
            from .realtalk_history_loader import load_realtalk_dialogues
            logger.info(f"使用 realtalk 文件加载器: {file_path}")
            return load_realtalk_dialogues(file_path)

    elif loader_type == "locomo":
        # 强制使用 locomo 加载器
        if file_path and os.path.isdir(file_path):
            from .locomo_history_loader import load_locomo_dialogues_from_directory
            logger.info(f"使用 locomo 目录加载器: {file_path}")
            return load_locomo_dialogues_from_directory(file_path)
        else:
            from .locomo_history_loader import load_locomo_dialogues
            logger.info(f"使用 locomo 文件加载器: {file_path}")
            return load_locomo_dialogues(file_path)
    
    elif loader_type == "default":
        # 强制使用默认加载器
        return load_default_dialogues(file_path)
    
    else:  # loader_type == "auto" 或未知类型
        # 自动检测
        # 如果 file_path 是目录，检查目录名
        if file_path and os.path.isdir(file_path):
            # locomo 目录优先
            if "LOCOMO" in file_path.upper():
                from .locomo_history_loader import load_locomo_dialogues_from_directory
                logger.info(f"检测到 locomo 目录，使用 locomo 加载器: {file_path}")
                return load_locomo_dialogues_from_directory(file_path)
            # 检查目录是否包含 "REALTALK"
            if "REALTALK" in file_path.upper():
                from .realtalk_history_loader import load_realtalk_dialogues_from_directory
                logger.info(f"检测到 realtalk 目录，使用 realtalk 加载器: {file_path}")
                return load_realtalk_dialogues_from_directory(file_path)
        
        if is_locomo_path(file_path):
            from .locomo_history_loader import load_locomo_dialogues
            logger.info(f"检测到 locomo 文件，使用 locomo 加载器: {file_path}")
            return load_locomo_dialogues(file_path)

        if is_realtalk_path(file_path):
            from .realtalk_history_loader import load_realtalk_dialogues
            logger.info(f"检测到 realtalk 文件，使用 realtalk 加载器: {file_path}")
            return load_realtalk_dialogues(file_path)
        
        # 否则使用默认加载器
        return load_default_dialogues(file_path)

if __name__ == "__main__":
    # 测试代码
    import sys
    logging.basicConfig(level=logging.INFO)
    
    dialogues = load_dialogues()
    print(f"构造了 {len(dialogues)} 个 dialogue")
    if dialogues:
        print("第一个 dialogue 示例:")
        print(json.dumps(dialogues[0], ensure_ascii=False, indent=2))
