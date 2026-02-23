#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据构造流程（简化版）：
支持通过参数控制 kg/scene 的 prompt 版本，可选包含第五阶段（scene特征提取）

五个阶段：
1. 构造 dialogues
2. 构造 episodes
3. 形成KG候选
4. 形成 scene（theme 和 diary）
5. 形成 scene 特征（实体特征提取）
"""

import json
import os
import shutil
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
import sys
# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# 导入数据加载模块
try:
    from load_data import load_dialogues
except ImportError:
    # 如果导入失败，使用本地定义的函数（向后兼容）
    # 添加项目根目录到 sys.path（pipeline 目录的父目录）
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    sys.path.append(project_root)
    from load_data.dialog_history_loader import load_dialogues

# 导入工具函数
try:
    from utils import save_dialogue
    from utils.memory_build_utils import build_episodes_with_id
    from memory.build_memory.form_kg_candidate import scan_and_form_kg_candidates
    from memory.build_memory.form_scene import scan_and_form_scenes
    from memory.build_memory.form_scene_kg import scan_and_extract_features
except ImportError:
    # 如果导入失败，使用本地定义的函数（向后兼容）
    # 添加项目根目录到 sys.path（pipeline 目录的父目录）
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    sys.path.append(project_root)
    from utils.dialogue_utils import save_dialogue
    from utils.memory_build_utils import build_episodes_with_id
    from memory.build_memory.form_kg_candidate import scan_and_form_kg_candidates
    from memory.build_memory.form_scene import scan_and_form_scenes
    from memory.build_memory.form_scene_kg import scan_and_extract_features


# 路径配置
PROJECT_ROOT = Path(__file__).parent.parent


def get_output_path(process_id: str, stage_name: str) -> Path:
    """
    Args:
        process_id: 处理流ID
        stage_name: 阶段名称（如 "dialogues", "episodes", "kg_candidates"）
        
    Returns:
        输出目录路径（基于项目根目录的绝对路径）
    """
    return PROJECT_ROOT / "data" / "memory" / process_id / stage_name


def stage1_construct_dialogues_for_id(process_id: str, data_source: str = None, loader_type: str = "auto"):
    """
    第一阶段：构造 dialogues 并保存到 data/memory/{id}/dialogues 目录
    
    Args:
        process_id: 处理流ID
        data_source: 数据源路径（文件或目录），如果为 None 则使用默认路径
        loader_type: 加载器类型，可选值：
                    - "auto": 自动检测（默认）
                    - "realtalk": 强制使用 realtalk 加载器
                    - "default": 强制使用默认加载器
    """
    logger.info("=" * 50)
    logger.info(f"开始第一阶段：为处理流 {process_id} 构造 dialogues")
    logger.info(f"数据源: {data_source if data_source else '默认'}")
    logger.info(f"加载器类型: {loader_type}")
    logger.info("=" * 50)
    
    # 1. 加载 dialogue 列表
    dialogues = load_dialogues(data_source, loader_type)
    if not dialogues:
        logger.error("没有加载到 dialogue 数据，退出")
        return False
    
    logger.info(f"共加载 {len(dialogues)} 个 dialogue")
    
    # 2. 构建目标目录
    target_dir = get_output_path(process_id, "dialogues")
    target_dir.mkdir(parents=True, exist_ok=True)
    
    # 3. 保存 dialogues
    successful_count = 0
    failed_count = 0
    
    for i, dialogue in enumerate(dialogues):
        logger.info(f"保存第 {i+1}/{len(dialogues)} 个 dialogue: {dialogue.get('dialogue_id')}")
        
        if save_dialogue(dialogue, str(target_dir)):
            successful_count += 1
        else:
            failed_count += 1
    
    # 4. 输出统计信息
    logger.info("=" * 50)
    logger.info("第一阶段完成")
    logger.info(f"成功保存: {successful_count} 个")
    logger.info(f"失败: {failed_count} 个")
    logger.info(f"输出目录: {target_dir}")
    logger.info("=" * 50)
    
    return successful_count > 0


def stage2_construct_episodes_for_id(process_id: str, memory_owner_name: str = "changshengEVA"):
    """
    第二阶段：构造 episodes 并保存到 data/memory/{id}/episodes 目录
    
    Args:
        process_id: 处理流ID
        memory_owner_name: 记忆所有者的名称，用于替换prompt中的<memory_owner_name>占位符
    """
    logger.info("=" * 50)
    logger.info(f"开始第二阶段：为处理流 {process_id} 构造 episodes")
    logger.info(f"记忆所有者名称: {memory_owner_name}")
    logger.info("=" * 50)
    
    # 使用新的工具函数构建 episodes
    logger.info(f"调用新的 memory build 方法构建 episodes...")
    if not build_episodes_with_id(process_id, str(PROJECT_ROOT), memory_owner_name):
        logger.error("构建 episodes 失败")
        return False
    
    # 统计生成的文件数量
    episodes_root = get_output_path(process_id, "episodes")
    by_dialogue_dir = episodes_root / "by_dialogue"
    
    episode_files_count = 0
    qualification_files_count = 0
    eligibility_files_count = 0
    
    if by_dialogue_dir.exists():
        for dialogue_dir_name in os.listdir(by_dialogue_dir):
            dialogue_dir = by_dialogue_dir / dialogue_dir_name
            if dialogue_dir.is_dir():
                for filename in os.listdir(dialogue_dir):
                    if filename.endswith('.json'):
                        if filename == 'episodes_v1.json':
                            episode_files_count += 1
                        elif filename == 'qualifications_v1.json':
                            qualification_files_count += 1
                        elif filename.startswith('eligibility_'):
                            eligibility_files_count += 1
    
    # 输出统计信息
    logger.info("=" * 50)
    logger.info("第二阶段完成")
    logger.info(f"生成 episodes 文件: {episode_files_count} 个")
    logger.info(f"生成 qualifications 文件: {qualification_files_count} 个")
    logger.info(f"生成 eligibility 文件: {eligibility_files_count} 个")
    logger.info(f"记忆所有者名称: {memory_owner_name}")
    logger.info(f"输出目录: {episodes_root}")
    logger.info("=" * 50)
    
    return episode_files_count > 0


def stage3_form_kg_candidates_for_id(process_id: str, prompt_version: str = "v1", memory_owner_name: str = "changshengEVA"):
    """
    第三阶段：形成KG候选，为kg_available为true的episode生成kg_candidate
    
    Args:
        process_id: 处理流ID
        prompt_version: prompt版本（v1 或 v2），默认v1
        memory_owner_name: 记忆所有者的名称，用于替换prompt中的<memory_owner_name>占位符
    """
    logger.info("=" * 50)
    logger.info(f"开始第三阶段：为处理流 {process_id} 形成KG候选")
    logger.info(f"使用 prompt 版本: {prompt_version}")
    logger.info(f"记忆所有者名称: {memory_owner_name}")
    logger.info("=" * 50)
    
    # 构建目录路径
    dialogues_root = get_output_path(process_id, "dialogues")
    episodes_root = get_output_path(process_id, "episodes")
    kg_candidates_root = get_output_path(process_id, "kg_candidates")
    
    # 确保目录存在
    dialogues_root.mkdir(parents=True, exist_ok=True)
    episodes_root.mkdir(parents=True, exist_ok=True)
    kg_candidates_root.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"对话目录: {dialogues_root}")
    logger.info(f"Episodes目录: {episodes_root}")
    logger.info(f"KG候选目录: {kg_candidates_root}")
    
    try:
        # 调用 form_kg_candidate 模块的主函数
        logger.info("开始扫描并生成 kg_candidates...")
        scan_and_form_kg_candidates(
            prompt_version=prompt_version,
            dialogues_root=dialogues_root,
            episodes_root=episodes_root,
            kg_candidates_root=kg_candidates_root,
            memory_owner_name=memory_owner_name
        )
        
        # 统计生成的 kg_candidate 文件数量
        kg_candidate_files_count = 0
        if kg_candidates_root.exists():
            for file_path in kg_candidates_root.iterdir():
                if file_path.is_file() and file_path.suffix == '.json':
                    # 检查文件名格式是否为数字（如 00001.json）
                    try:
                        int(file_path.stem)
                        kg_candidate_files_count += 1
                    except ValueError:
                        # 不是数字格式的文件，跳过
                        continue
        
        # 输出统计信息
        logger.info("=" * 50)
        logger.info("第三阶段完成")
        logger.info(f"生成 kg_candidate 文件: {kg_candidate_files_count} 个")
        logger.info(f"使用 prompt 版本: {prompt_version}")
        logger.info(f"记忆所有者名称: {memory_owner_name}")
        logger.info(f"输出目录: {kg_candidates_root}")
        logger.info("=" * 50)
        
        return kg_candidate_files_count > 0
        
    except Exception as e:
        logger.error(f"形成KG候选失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def stage4_form_scenes_for_id(process_id: str,
                              scene_prompt_version: str = "v1",
                              memory_owner_name: str = "changshengEVA"):
    """
    第四阶段：形成 scene，为每个 episode 生成 scene（theme 和 diary）
    
    Args:
        process_id: 处理流ID
        scene_prompt_version: scene prompt版本（v1 或 v2），默认v1
        memory_owner_name: 记忆所有者的名称，用于替换prompt中的<memory_owner_name>占位符
    """
    logger.info("=" * 50)
    logger.info(f"开始第四阶段：为处理流 {process_id} 形成 scene")
    logger.info(f"使用 scene prompt 版本: {scene_prompt_version}")
    logger.info(f"记忆所有者名称: {memory_owner_name}")
    logger.info("=" * 50)
    
    # 构建目录路径
    dialogues_root = get_output_path(process_id, "dialogues")
    episodes_root = get_output_path(process_id, "episodes")
    scene_root = get_output_path(process_id, "scene")
    
    # 确保目录存在
    dialogues_root.mkdir(parents=True, exist_ok=True)
    episodes_root.mkdir(parents=True, exist_ok=True)
    scene_root.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"对话目录: {dialogues_root}")
    logger.info(f"Episodes目录: {episodes_root}")
    logger.info(f"Scene目录: {scene_root}")
    
    try:
        # 调用 form_scene 模块的主函数
        logger.info("开始扫描并生成 scenes...")
        scan_and_form_scenes(
            prompt_version=scene_prompt_version,
            dialogues_root=dialogues_root,
            episodes_root=episodes_root,
            scene_root=scene_root,
            memory_owner_name=memory_owner_name
        )
        
        # 统计生成的 scene 文件数量
        scene_files_count = 0
        if scene_root.exists():
            for file_path in scene_root.iterdir():
                if file_path.is_file() and file_path.suffix == '.json':
                    # 检查文件名格式是否为数字（如 00001.json）
                    try:
                        int(file_path.stem)
                        scene_files_count += 1
                    except ValueError:
                        # 不是数字格式的文件，跳过
                        continue
        
        # 输出统计信息
        logger.info("=" * 50)
        logger.info("第四阶段完成")
        logger.info(f"生成 scene 文件: {scene_files_count} 个")
        logger.info(f"使用 scene prompt 版本: {scene_prompt_version}")
        logger.info(f"记忆所有者名称: {memory_owner_name}")
        logger.info(f"输出目录: {scene_root}")
        logger.info("=" * 50)
        
        return scene_files_count > 0
        
    except Exception as e:
        logger.error(f"形成 scene 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def stage5_form_scene_features_for_id(process_id: str, force_update: bool = False, memory_owner_name: str = "changshengEVA"):
    """
    第五阶段：形成 scene 特征，为已生成 kg 和 scene 的 episode 提取实体特征
    
    Args:
        process_id: 处理流ID
        force_update: 是否强制更新特征文件（即使已生成也重新生成）
        memory_owner_name: 记忆所有者的名称，用于替换prompt中的<memory_owner_name>占位符
    """
    logger.info("=" * 50)
    logger.info(f"开始第五阶段：为处理流 {process_id} 形成 scene 特征")
    logger.info(f"记忆所有者名称: {memory_owner_name}")
    logger.info("=" * 50)
    
    # 构建目录路径
    memory_root = PROJECT_ROOT / "data" / "memory" / process_id
    
    # 确保目录存在
    memory_root.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Memory 根目录: {memory_root}")
    
    try:
        # 调用 form_scene_kg 模块的主函数
        logger.info("开始扫描并提取 scene 特征...")
        scan_and_extract_features(
            workflow_id=process_id,
            force_update=force_update,
            use_tqdm=True,
            memory_owner_name=memory_owner_name
        )
        
        # 统计已更新的 kg_candidate 文件数量
        kg_candidates_root = get_output_path(process_id, "kg_candidates")
        updated_files_count = 0
        
        if kg_candidates_root.exists():
            for file_path in kg_candidates_root.iterdir():
                if file_path.is_file() and file_path.suffix == '.json':
                    try:
                        # 检查文件名格式是否为数字（如 00001.json）
                        int(file_path.stem)
                        
                        # 读取文件检查是否包含 features 字段
                        with open(file_path, 'r', encoding='utf-8') as f:
                            kg_data = json.load(f)
                        
                        # 检查是否包含 features 字段
                        features = kg_data.get("kg_candidate", {}).get("facts", {}).get("features", None)
                        if features is not None:
                            updated_files_count += 1
                    except (ValueError, json.JSONDecodeError):
                        # 不是数字格式的文件或JSON解析错误，跳过
                        continue
        
        # 输出统计信息
        logger.info("=" * 50)
        logger.info("第五阶段完成")
        logger.info(f"更新 kg_candidate 文件: {updated_files_count} 个（包含特征）")
        logger.info(f"记忆所有者名称: {memory_owner_name}")
        logger.info(f"输出目录: {kg_candidates_root}")
        logger.info("=" * 50)
        
        return updated_files_count > 0
        
    except Exception as e:
        logger.error(f"形成 scene 特征失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def run_full_pipeline_for_id(process_id: str, data_source: str = None, loader_type: str = "auto",
                           prompt_version: str = "v1", include_stage5: bool = True,
                           scene_prompt_version: str = "v1",
                           memory_owner_name: str = "changshengEVA"):
    """
    为指定ID运行完整的数据构造流程
    
    Args:
        process_id: 处理流ID
        data_source: 数据源路径（文件或目录），如果为 None 则使用默认路径
        loader_type: 加载器类型，可选值：
                    - "auto": 自动检测（默认）
                    - "realtalk": 强制使用 realtalk 加载器
                    - "default": 强制使用默认加载器
        prompt_version: prompt版本（v1 或 v2），默认v1
        include_stage5: 是否包含第五阶段（scene特征提取），默认True
        scene_prompt_version: scene prompt版本（v1 或 v2），默认v1
        memory_owner_name: 记忆所有者的名称，用于替换prompt中的<memory_owner_name>占位符
    """
    logger.info(f"开始为处理流 {process_id} 执行完整数据构造流程")
    logger.info(f"数据源: {data_source if data_source else '默认'}")
    logger.info(f"加载器类型: {loader_type}")
    logger.info(f"使用 prompt 版本: {prompt_version}")
    logger.info(f"使用 scene prompt 版本: {scene_prompt_version}")
    logger.info(f"包含第五阶段: {include_stage5}")
    logger.info(f"记忆所有者名称: {memory_owner_name}")
    
    # # 第一阶段：构造 dialogues
    # if not stage1_construct_dialogues_for_id(process_id, data_source, loader_type):
    #     logger.warning("第一阶段失败，跳过后续阶段")
    #     return False
    
    # # 第二阶段：构造 episodes
    # if not stage2_construct_episodes_for_id(process_id, memory_owner_name):
    #     logger.warning("第二阶段失败，跳过第三阶段")
    #     return False
    
    # # 第三阶段：形成KG候选
    # if not stage3_form_kg_candidates_for_id(process_id, prompt_version, memory_owner_name):
    #     logger.warning("第三阶段失败")
    #     return False
    
    # # 第四阶段：形成 scene
    # if not stage4_form_scenes_for_id(process_id, scene_prompt_version, memory_owner_name):
    #     logger.warning("第四阶段失败")
    #     return False
    
    # 第五阶段：形成 scene 特征（可选）
    if include_stage5:
        if not stage5_form_scene_features_for_id(process_id, force_update=False, memory_owner_name=memory_owner_name):
            logger.warning("第五阶段失败")
            # 第五阶段失败不视为整个流程失败，因为它是可选的增强功能
            # 但仍然记录警告
    
    stage_count = 5 if include_stage5 else 4
    logger.info("=" * 50)
    logger.info(f"处理流 {process_id} 的所有数据构造流程完成（包含 {stage_count} 个阶段）")
    logger.info(f"数据源: {data_source if data_source else '默认'}")
    logger.info(f"加载器类型: {loader_type}")
    logger.info(f"使用 prompt 版本: {prompt_version}")
    logger.info(f"使用 scene prompt 版本: {scene_prompt_version}")
    logger.info(f"记忆所有者名称: {memory_owner_name}")
    logger.info("=" * 50)
    return True


def main():
    import argparse
    """
    主函数：支持数据源和加载器类型参数
    """
    parser = argparse.ArgumentParser(
        description="数据构造流程 - 支持指定数据源和加载器类型"
    )
    parser.add_argument("--id", type=str, required=True,
                       help="处理流ID（必需）")
    parser.add_argument("--data-source", type=str, default=None,
                       help="数据源路径（文件或目录），如果未指定则使用默认路径")
    parser.add_argument("--loader-type", type=str, default="auto",
                       choices=["auto", "realtalk", "default"],
                       help="加载器类型：auto（自动检测，默认）, realtalk（强制使用realtalk加载器）, default（强制使用默认加载器）")
    parser.add_argument("--kg-prompt-version", type=str, default="v3",
                       help="KG候选生成的prompt版本（v1 或 v2，默认v2）")
    parser.add_argument("--scene-prompt-version", type=str, default="v2",
                       help="Scene 生成的prompt版本（v1 或 v2，默认v1）")
    parser.add_argument("--no-stage5", action="store_true",
                       help="不包含第五阶段（scene特征提取）")
    parser.add_argument("--memory-owner-name", type=str, default="changshengEVA",
                       help="记忆所有者的名称，用于替换prompt中的<memory_owner_name>占位符（默认：changshengEVA）")
    
    args = parser.parse_args()
    
    # 直接运行完整流程
    success = run_full_pipeline_for_id(
        args.id,
        data_source=args.data_source,
        loader_type=args.loader_type,
        prompt_version=args.kg_prompt_version,
        scene_prompt_version=args.scene_prompt_version,
        include_stage5=not args.no_stage5,
        memory_owner_name=args.memory_owner_name
    )
    
    stage_count = 5 if not args.no_stage5 else 4
    if success:
        logger.info("=" * 50)
        logger.info(f"处理流 {args.id} 的数据构造流程完成（完整{stage_count}个阶段）")
        logger.info(f"数据源: {args.data_source if args.data_source else '默认'}")
        logger.info(f"加载器类型: {args.loader_type}")
        logger.info(f"记忆所有者名称: {args.memory_owner_name}")
        logger.info("=" * 50)
    else:
        logger.error("数据构造流程失败")


if __name__ == "__main__":
    main()

##测试私有数据：        python ./pipeline/memory_pre.py --id testdefault 
##测试realtalk数据      python ./pipeline/memory_pre.py --id testrt --data-source data\REALTALK\data\Chat_1_Emi_Elise.json --loader-type realtalk --memory-owner-name Emi
