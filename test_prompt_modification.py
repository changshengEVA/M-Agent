#!/usr/bin/env python3
import sys
import os
import yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory.build_memory.form_kg_candidate import load_prompts

def test_prompt_modified():
    prompts = load_prompts(memory_owner_name="test_owner")
    v3 = prompts.get('kg_strong_filter_v3')
    if not v3:
        print("v3 not found")
        return
    entity_prompt = v3.get('entity_extraction', '')
    if "Dialogue participants" in entity_prompt:
        print("✓ Prompt 已成功添加关于说话人的说明")
        # 打印相关部分
        lines = entity_prompt.split('\n')
        for i, line in enumerate(lines):
            if "Dialogue participants" in line:
                for j in range(max(0, i-2), min(len(lines), i+3)):
                    print(lines[j])
                break
    else:
        print("✗ Prompt 中未找到新增的说明")
        # 检查整个 prompt 长度
        print(f"实体提取 prompt 长度: {len(entity_prompt)} 字符")
        # 打印前500字符
        print(entity_prompt[:500])

if __name__ == '__main__':
    test_prompt_modified()