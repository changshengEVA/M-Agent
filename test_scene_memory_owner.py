#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
from memory.build_memory.form_scene import build_scene_structure
from datetime import datetime

# 测试数据
scene_number = 42
episode_meta = {
    'episode_id': 'ep_001',
    'dialogue_id': 'dlg_001',
    'turn_span': [0, 5]
}
scene_result = {
    'theme': '测试主题',
    'diary': '测试日记内容'
}

# 测试默认 memory_owner_name (应为 "changshengEVA")
scene_default = build_scene_structure(scene_number, episode_meta, scene_result)
print("默认 memory_owner_name:")
print(f"  memory_owner: {scene_default['meta']['memory_owner']}")
assert scene_default['meta']['memory_owner'] == "changshengEVA", f"预期 'changshengEVA'，实际 {scene_default['meta']['memory_owner']}"

# 测试自定义 memory_owner_name
custom_name = "ZQR"
scene_custom = build_scene_structure(scene_number, episode_meta, scene_result, memory_owner_name=custom_name)
print(f"自定义 memory_owner_name = '{custom_name}':")
print(f"  memory_owner: {scene_custom['meta']['memory_owner']}")
assert scene_custom['meta']['memory_owner'] == custom_name, f"预期 '{custom_name}'，实际 {scene_custom['meta']['memory_owner']}"

# 检查其他字段是否正常
assert scene_custom['scene_id'] == 'scene_00042'
assert scene_custom['theme'] == '测试主题'
assert scene_custom['diary'] == '测试日记内容'
print("所有断言通过。")

# 测试 process_episode_file 的调用链（模拟）
# 由于需要加载文件，我们只检查函数签名是否存在
import inspect
sig = inspect.signature(build_scene_structure)
print(f"\nbuild_scene_structure 参数: {list(sig.parameters.keys())}")
assert 'memory_owner_name' in sig.parameters, "memory_owner_name 参数缺失"

print("\n测试成功完成。")