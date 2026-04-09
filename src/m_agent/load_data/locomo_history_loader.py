#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 LoCoMo 数据（locomo10.json 或 split Chat_*.json）导入为 Dialogue 数据
具体的 Dialogue 数据结构与其它 loader 保持一致：
- dialogue_id
- user_id
- participants
- meta
- turns
"""

import glob
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from m_agent.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)


def parse_locomo_datetime(dt_str: str) -> datetime:
    """
    解析 LoCoMo 日期时间字符串。
    常见格式：
    - "1:56 pm on 8 May, 2023"
    - "10:05 am on 11 July, 2023"
    """
    if not isinstance(dt_str, str) or not dt_str.strip():
        logger.warning("LoCoMo 日期时间为空，使用当前时间")
        return datetime.now()

    text = dt_str.strip()
    # 兼容小写 am/pm
    text = re.sub(r"\bam\b", "AM", text, flags=re.IGNORECASE)
    text = re.sub(r"\bpm\b", "PM", text, flags=re.IGNORECASE)

    formats = [
        "%I:%M %p on %d %B, %Y",
        "%I:%M %p on %d %b, %Y",
        "%H:%M on %d %B, %Y",
        "%H:%M on %d %b, %Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    logger.warning(f"无法解析 LoCoMo 日期时间 '{dt_str}'，使用当前时间")
    return datetime.now()


def load_locomo_history(file_path: str) -> Optional[Any]:
    """
    加载 LoCoMo JSON 文件。

    支持两种结构：
    - list[dict]：locomo10.json 顶层数组
    - dict：单个 split 文件（如 Chat_1_Caroline_Melanie.json）
    """
    if not os.path.exists(file_path):
        logger.error(f"文件不存在: {file_path}")
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"成功从 {file_path} 加载 LoCoMo 数据")
        return data
    except Exception as e:
        logger.error(f"加载文件失败: {e}")
        return None


def _sanitize_id_component(text: str) -> str:
    """将 ID 片段标准化，避免出现路径/特殊字符。"""
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", str(text).strip())
    return value or "unknown"


def _ordered_unique(items: List[str]) -> List[str]:
    """保持顺序去重。"""
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def extract_dialogues_from_locomo(raw_data: Any, source_file: str = "") -> List[Dict[str, Any]]:
    """
    从 LoCoMo 原始数据中提取 dialogue 列表。
    每个 session 被视为一个独立 dialogue。
    """
    if isinstance(raw_data, list):
        samples = raw_data
    elif isinstance(raw_data, dict):
        samples = [raw_data]
    else:
        logger.warning(f"LoCoMo 数据类型不支持: {type(raw_data)}")
        return []

    base_name = os.path.splitext(os.path.basename(source_file))[0] if source_file else "locomo"
    dialogues: List[Dict[str, Any]] = []

    for sample_index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            logger.warning(f"LoCoMo sample[{sample_index}] 不是字典，跳过")
            continue

        # 常规结构：sample["conversation"]；兼容直接传入 conversation 对象
        conversation = sample.get("conversation", sample)
        if not isinstance(conversation, dict):
            logger.warning(f"LoCoMo sample[{sample_index}] 缺少有效 conversation，跳过")
            continue

        sample_id = str(sample.get("sample_id", f"sample_{sample_index + 1}"))
        speaker_a = conversation.get("speaker_a")
        speaker_b = conversation.get("speaker_b")
        seed_participants = [p for p in [speaker_a, speaker_b] if isinstance(p, str) and p.strip()]

        session_numbers = []
        for key, value in conversation.items():
            m = re.match(r"^session_(\d+)$", key)
            if m and isinstance(value, list):
                session_numbers.append(int(m.group(1)))
        session_numbers.sort()

        for session_num in session_numbers:
            session_key = f"session_{session_num}"
            date_key = f"{session_key}_date_time"
            messages = conversation.get(session_key, [])

            if not isinstance(messages, list) or not messages:
                logger.debug(f"跳过空会话: sample={sample_id}, {session_key}")
                continue

            session_start = parse_locomo_datetime(conversation.get(date_key, ""))
            turns = []

            for turn_idx, msg in enumerate(messages):
                if not isinstance(msg, dict):
                    logger.warning(f"sample={sample_id} {session_key} 的 turn[{turn_idx}] 不是字典，跳过")
                    continue

                speaker = msg.get("speaker", "unknown")
                text = msg.get("text", "")

                if not isinstance(speaker, str):
                    speaker = str(speaker)
                if not isinstance(text, str):
                    text = str(text)

                turn_dt = session_start + timedelta(seconds=turn_idx * 5)
                turns.append(
                    {
                        "turn_id": turn_idx,
                        "speaker": speaker,
                        "text": text,
                        "timestamp": turn_dt.isoformat(),
                    }
                )

            if not turns:
                logger.warning(f"sample={sample_id} {session_key} 没有有效 turns，跳过")
                continue

            participants = _ordered_unique(
                seed_participants + [t.get("speaker", "unknown") for t in turns if t.get("speaker")]
            )
            user_id = turns[0].get("speaker") or (participants[0] if participants else "unknown")

            dialogue_id = "_".join(
                [
                    "dlg",
                    _sanitize_id_component(base_name),
                    _sanitize_id_component(sample_id),
                    str(session_num),
                ]
            )

            dialogue = {
                "dialogue_id": dialogue_id,
                "user_id": user_id,
                "participants": participants,
                "meta": {
                    "start_time": turns[0]["timestamp"],
                    "end_time": turns[-1]["timestamp"],
                    "language": "en",
                    "platform": "locomo",
                    "version": 1,
                },
                "turns": turns,
            }
            dialogues.append(dialogue)

    return dialogues


def load_locomo_dialogues(file_path: str = None) -> List[Dict[str, Any]]:
    """
    从 LoCoMo 文件加载并构造 dialogue 列表。
    默认路径：data/locomo/data/locomo10.json
    """
    if file_path is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        file_path = os.path.join(project_root, "data", "locomo", "data", "locomo10.json")

    raw_data = load_locomo_history(file_path)
    if raw_data is None:
        return []

    dialogues = extract_dialogues_from_locomo(raw_data, source_file=file_path)
    logger.info(f"从 {file_path} 成功构造 {len(dialogues)} 个 LoCoMo dialogue")
    return dialogues


def load_locomo_dialogues_from_directory(dir_path: str = None) -> List[Dict[str, Any]]:
    """
    从目录加载 LoCoMo JSON 文件并构造 dialogue 列表。
    默认目录：data/locomo
    """
    if dir_path is None:
        dir_path = os.fspath(PROJECT_ROOT / "data" / "locomo")

    if not os.path.isdir(dir_path):
        logger.error(f"目录不存在: {dir_path}")
        return []

    # 优先 split 文件命名；若不存在则回退为目录内全部 json 文件
    file_list = glob.glob(os.path.join(dir_path, "Chat_*.json"))
    if not file_list:
        file_list = glob.glob(os.path.join(dir_path, "*.json"))
    file_list.sort()

    all_dialogues: List[Dict[str, Any]] = []
    logger.info(f"在目录 {dir_path} 中找到 {len(file_list)} 个 LoCoMo 文件")

    for file_path in file_list:
        raw_data = load_locomo_history(file_path)
        if raw_data is None:
            continue
        dialogues = extract_dialogues_from_locomo(raw_data, source_file=file_path)
        all_dialogues.extend(dialogues)

    logger.info(f"从目录 {dir_path} 总共构造 {len(all_dialogues)} 个 LoCoMo dialogue")
    return all_dialogues


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    dialogues = load_locomo_dialogues()
    print(f"构造了 {len(dialogues)} 个 dialogue")
    if dialogues:
        print("第一个 dialogue 示例:")
        print(json.dumps(dialogues[0], ensure_ascii=False, indent=2))
