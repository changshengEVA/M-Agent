#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 realtalk 数据（data/REALTALK/data/Chat_x_A_B.json 文件）导入为 Dialogue 数据
具体的 Dialogue 数据的保存内容和形式参考 data\memory\test1\dialogues
"""

import json
import os
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def parse_realtalk_datetime(dt_str: str) -> datetime:
    """
    解析 realtalk 日期时间字符串，格式为 "DD.MM.YYYY, HH:MM:SS"
    返回 datetime 对象。
    """
    try:
        # 去除可能的多余空格
        dt_str = dt_str.strip()
        # 格式: "29.12.2023, 11:23:21"
        return datetime.strptime(dt_str, "%d.%m.%Y, %H:%M:%S")
    except ValueError as e:
        logger.warning(f"无法解析日期时间 '{dt_str}'，使用当前时间: {e}")
        return datetime.now()


def load_realtalk_history(file_path: str) -> Optional[Dict[str, Any]]:
    """
    加载单个 realtalk JSON 文件，返回原始数据字典。
    
    Args:
        file_path: JSON 文件路径
        
    Returns:
        原始数据字典，如果加载失败则返回 None
    """
    if not os.path.exists(file_path):
        logger.error(f"文件不存在: {file_path}")
        return None
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        logger.info(f"成功从 {file_path} 加载 realtalk 数据")
        return data
    except Exception as e:
        logger.error(f"加载文件失败: {e}")
        return None


def extract_dialogues_from_realtalk(raw_data: Dict[str, Any], source_file: str = "") -> List[Dict[str, Any]]:
    """
    从原始 realtalk 数据中提取 dialogue 列表。
    每个 session 被视为一个独立的 dialogue。
    
    Args:
        raw_data: 原始 realtalk 数据字典
        source_file: 源文件名，用于生成 dialogue_id
        
    Returns:
        dialogue 列表，每个元素是构造好的 dialogue 字典
    """
    dialogues = []
    
    # 提取参与者
    name_info = raw_data.get("name", {})
    speaker_1 = name_info.get("speaker_1", "unknown")
    speaker_2 = name_info.get("speaker_2", "unknown")
    participants = [speaker_1, speaker_2]
    
    # 找出所有 session 键
    session_keys = [key for key in raw_data.keys() if key.startswith("session_")]
    session_keys.sort(key=lambda x: int(x.split('_')[1]) if x.split('_')[1].isdigit() else 0)
    
    for session_key in session_keys:
        messages = raw_data.get(session_key, [])
        if not messages:
            logger.debug(f"跳过空会话: {session_key}")
            continue
        
        # 确保 messages 是列表
        if not isinstance(messages, list):
            logger.warning(f"会话 {session_key} 的数据类型不是列表，跳过")
            continue
        
        # 构建 turns
        turns = []
        for i, msg in enumerate(messages):
            # 确保 msg 是字典
            if not isinstance(msg, dict):
                logger.warning(f"会话 {session_key} 中的消息 {i} 不是字典，跳过")
                continue
            clean_text = msg.get("clean_text", "")
            speaker = msg.get("speaker", "")
            date_time_str = msg.get("date_time", "")
            # 解析时间戳
            timestamp_dt = parse_realtalk_datetime(date_time_str)
            timestamp_iso = timestamp_dt.isoformat()
            
            turn = {
                "turn_id": i,
                "speaker": speaker,
                "text": clean_text,
                "timestamp": timestamp_iso
            }
            turns.append(turn)
        
        if not turns:
            logger.warning(f"会话 {session_key} 没有有效的 turns，跳过")
            continue
        
        # 确定 start_time 和 end_time
        start_time = turns[0]["timestamp"]
        end_time = turns[-1]["timestamp"]
        
        # 生成 dialogue_id
        # 使用源文件名（不含扩展名）和会话编号
        base_name = os.path.splitext(os.path.basename(source_file))[0] if source_file else "unknown"
        session_num = session_key.split('_')[1]
        dialogue_id = f"dlg_{base_name}_{session_num}"
        
        # 确定 user_id（选择第一个说话者）
        user_id = turns[0]["speaker"] if turns else "unknown"
        
        # 构建 dialogue 字典
        dialogue = {
            "dialogue_id": dialogue_id,
            "user_id": user_id,
            "participants": participants,
            "meta": {
                "start_time": start_time,
                "end_time": end_time,
                "language": "en",  # realtalk 数据是英文
                "platform": "realtalk",
                "version": 1
            },
            "turns": turns
        }
        
        dialogues.append(dialogue)
        logger.debug(f"从会话 {session_key} 构造 dialogue: {dialogue_id}")
    
    return dialogues


def load_realtalk_dialogues(file_path: str = None) -> List[Dict[str, Any]]:
    """
    从 realtalk JSON 文件加载并构造 dialogue 列表。
    
    Args:
        file_path: JSON 文件路径，如果为 None 则使用默认路径（data/REALTALK/data/Chat_1_Emi_Elise.json）
        
    Returns:
        dialogue 列表，每个元素是构造好的 dialogue 字典
    """
    if file_path is None:
        # 默认使用第一个文件作为示例
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        file_path = os.path.join(project_root, "data", "REALTALK", "data", "Chat_1_Emi_Elise.json")
    
    raw_data = load_realtalk_history(file_path)
    if raw_data is None:
        return []
    
    dialogues = extract_dialogues_from_realtalk(raw_data, source_file=file_path)
    logger.info(f"从 {file_path} 成功构造 {len(dialogues)} 个 dialogue")
    return dialogues


def load_realtalk_dialogues_from_directory(dir_path: str = None) -> List[Dict[str, Any]]:
    """
    从目录加载所有 realtalk JSON 文件并构造 dialogue 列表。
    
    Args:
        dir_path: 目录路径，如果为 None 则使用默认目录（data/REALTALK/data/）
        
    Returns:
        所有文件的 dialogue 列表
    """
    if dir_path is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dir_path = os.path.join(project_root, "data", "REALTALK", "data")
    
    if not os.path.isdir(dir_path):
        logger.error(f"目录不存在: {dir_path}")
        return []
    
    all_dialogues = []
    # 查找所有 Chat_*.json 文件
    import glob
    pattern = os.path.join(dir_path, "Chat_*.json")
    file_list = glob.glob(pattern)
    logger.info(f"在目录 {dir_path} 中找到 {len(file_list)} 个 realtalk 文件")
    
    for file_path in file_list:
        raw_data = load_realtalk_history(file_path)
        if raw_data is None:
            continue
        dialogues = extract_dialogues_from_realtalk(raw_data, source_file=file_path)
        all_dialogues.extend(dialogues)
    
    logger.info(f"从目录 {dir_path} 总共构造 {len(all_dialogues)} 个 dialogue")
    return all_dialogues


if __name__ == "__main__":
    # 测试代码
    import sys
    logging.basicConfig(level=logging.INFO)
    
    # 测试单个文件
    dialogues = load_realtalk_dialogues()
    print(f"构造了 {len(dialogues)} 个 dialogue")
    if dialogues:
        print("第一个 dialogue 示例:")
        print(json.dumps(dialogues[0], ensure_ascii=False, indent=2))
    
    # 测试目录加载
    # all_dialogues = load_realtalk_dialogues_from_directory()
    # print(f"从目录构造了 {len(all_dialogues)} 个 dialogue")