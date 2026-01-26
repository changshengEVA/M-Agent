#!/usr/bin/env python3
"""
测试 form_scene.py 中的 scene_available 检查逻辑。
"""

import sys
import json
from pathlib import Path
from unittest.mock import Mock, patch

# 添加项目根目录到 Python 路径
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from memory.build_memory.form_scene import process_episode_file
from memory.build_memory.episode_status_manager import EpisodeStatusManager

def test_scene_available_filter():
    """测试 scene_available 过滤逻辑"""
    
    # 创建模拟数据
    mock_episode_file = Path("/tmp/test_episodes.json")
    mock_dialogue_file = Path("/tmp/test_dialogue.json")
    
    # 创建模拟的 episode 数据
    episode_data = {
        "dialogue_id": "dlg_test",
        "episodes": [
            {
                "episode_id": "ep_001",
                "turn_span": [1, 3]
            },
            {
                "episode_id": "ep_002", 
                "turn_span": [4, 6]
            }
        ]
    }
    
    # 创建模拟的对话数据
    dialogue_data = {
        "dialogue_id": "dlg_test",
        "turns": [
            {"turn_id": 1, "text": "Hello"},
            {"turn_id": 2, "text": "Hi"},
            {"turn_id": 3, "text": "How are you?"},
            {"turn_id": 4, "text": "Fine"},
            {"turn_id": 5, "text": "Good"},
            {"turn_id": 6, "text": "Bye"}
        ]
    }
    
    # 创建模拟的 prompt 数据
    prompts = {
        "scene_former_v1": "Generate scene for: <txt_string>"
    }
    
    # 创建模拟的状态管理器
    mock_status_manager = Mock(spec=EpisodeStatusManager)
    
    # 设置 mock 返回值
    # ep_001: scene_available = True, 未生成 scene
    # ep_002: scene_available = False, 未生成 scene
    mock_status_manager.get_episode.side_effect = lambda key: {
        "dlg_test:ep_001": {
            "scene_available": True,
            "scene_generated": False
        },
        "dlg_test:ep_002": {
            "scene_available": False,
            "scene_generated": False
        }
    }.get(key)
    
    mock_status_manager.is_scene_generated.side_effect = lambda key: False
    
    # Mock 文件读取
    with patch('memory.build_memory.form_scene.load_episodes') as mock_load_episodes, \
         patch('memory.build_memory.form_scene.find_dialogue_file') as mock_find_dialogue, \
         patch('memory.build_memory.form_scene.load_dialogue') as mock_load_dialogue, \
         patch('memory.build_memory.form_scene.get_status_manager') as mock_get_manager, \
         patch('memory.build_memory.form_scene.call_openai_for_scene') as mock_call_openai, \
         patch('memory.build_memory.form_scene.get_scene_root') as mock_get_scene_root, \
         patch('memory.build_memory.form_scene.get_next_scene_number') as mock_get_next_number, \
         patch('memory.build_memory.form_scene.save_scenes_as_individual_files') as mock_save_scenes:
        
        # 设置 mock 返回值
        mock_load_episodes.return_value = episode_data
        mock_find_dialogue.return_value = mock_dialogue_file
        mock_load_dialogue.return_value = dialogue_data
        mock_get_manager.return_value = mock_status_manager
        mock_call_openai.return_value = {"theme": "Test theme", "diary": "Test diary"}
        mock_get_scene_root.return_value = Path("/tmp/scenes")
        mock_get_next_number.return_value = 1
        mock_save_scenes.return_value = [Path("/tmp/scenes/00001.json")]
        
        # 运行 process_episode_file
        result = process_episode_file(
            mock_episode_file,
            prompts,
            force_update=False
        )
        
        # 验证结果
        print(f"处理结果: {result}")
        
        # 验证 get_episode 被调用了正确的次数
        call_args_list = mock_status_manager.get_episode.call_args_list
        print(f"get_episode 调用次数: {len(call_args_list)}")
        for i, call in enumerate(call_args_list):
            print(f"  调用 {i+1}: {call[0]}")
        
        # 验证 call_openai_for_scene 只被调用了一次（对于 ep_001）
        print(f"call_openai_for_scene 调用次数: {mock_call_openai.call_count}")
        
        # 预期：只有 ep_001 应该生成 scene，ep_002 应该被跳过
        if mock_call_openai.call_count == 1:
            print("✓ 测试通过：只有 scene_available=True 的 episode 生成了 scene")
            return True
        else:
            print("✗ 测试失败：scene_available 过滤未正确工作")
            return False

if __name__ == "__main__":
    success = test_scene_available_filter()
    sys.exit(0 if success else 1)