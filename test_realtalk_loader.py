#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, '.')

from load_data.realtalk_history_loader import load_realtalk_dialogues

dialogues = load_realtalk_dialogues('data/REALTALK/data/Chat_1_Emi_Elise.json')
print(f'Loaded {len(dialogues)} dialogues')
if dialogues:
    import json
    print(json.dumps(dialogues[0], indent=2, ensure_ascii=False))
    # 检查结构
    required_keys = {'dialogue_id', 'user_id', 'participants', 'meta', 'turns'}
    if all(key in dialogues[0] for key in required_keys):
        print("✓ 对话结构正确")
    else:
        print("✗ 对话结构缺失")
    # 检查轮次
    turns = dialogues[0]['turns']
    if turns:
        print(f"  第一个轮次: speaker={turns[0]['speaker']}, text={turns[0]['text'][:50]}...")
else:
    print("没有加载到对话")