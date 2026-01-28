#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知识图谱数据构建流程：从kg_candidates生成kg_data

功能：
1. 接收处理流ID
2. 从data/memory/{id}/kg_candidates/读取候选文件（批量处理）
3. 或处理单个kg_candidate JSON文件
4. 使用KGManager类合并候选信息到知识图谱
5. 生成kg_data到data/memory/{id}/kg_data/目录

使用方式：
# 批量处理整个目录
python pipeline/build_kg_data.py --id test3

# 处理单个文件
python pipeline/build_kg_data.py --id test3 --file 00001.json

# 处理单个文件（完整路径）
python pipeline/build_kg_data.py --id test3 --file-path data/memory/test3/kg_candidates/00001.json
"""

import json
import os
import logging
from datetime import datetime
from pathlib import Path
import sys
import argparse

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 添加项目根目录到sys.path以便导入KGManager
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
sys.path.append(project_root)

try:
    from memory.memory_sys.kg_manager import KGManager
except ImportError as e:
    logger.error(f"导入KGManager失败: {e}")
    logger.error("请确保memory/memory_sys/kg_manager.py文件存在")
    sys.exit(1)


def get_output_path(process_id: str, stage_name: str) -> Path:
    """
    获取输出目录路径
    
    Args:
        process_id: 处理流ID
        stage_name: 阶段名称（如 "kg_candidates", "kg_data"）
        
    Returns:
        输出目录路径（基于项目根目录的绝对路径）
    """
    return Path(project_root) / "data" / "memory" / process_id / stage_name


def process_single_kg_candidate(process_id: str, file_path: str, clear_existing: bool = False) -> dict:
    """
    处理单个kg_candidate JSON文件
    
    Args:
        process_id: 处理流ID
        file_path: kg_candidate文件路径（可以是相对路径或绝对路径）
        clear_existing: 是否清除现有的kg_data目录（默认False，即合并模式）
        
    Returns:
        处理结果字典
    """
    logger.info("=" * 50)
    logger.info(f"开始处理单个kg_candidate文件，处理流ID: {process_id}")
    logger.info(f"文件路径: {file_path}")
    logger.info("=" * 50)
    
    # 构建kg_data目录路径
    kg_data_dir = get_output_path(process_id, "kg_data")
    
    # 如果clear_existing为True，则删除现有的kg_data目录
    if clear_existing and kg_data_dir.exists():
        logger.warning(f"清除现有的kg_data目录: {kg_data_dir}")
        import shutil
        shutil.rmtree(kg_data_dir)
    
    # 确保kg_data目录存在
    kg_data_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # 初始化KGManager
        logger.info("初始化KGManager...")
        kg_manager = KGManager(
            kg_data_dir=str(kg_data_dir),
            workflow_id=process_id
        )
        
        # 加载JSON文件
        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            # 尝试相对于kg_candidates目录查找
            kg_candidates_dir = get_output_path(process_id, "kg_candidates")
            candidate_file = kg_candidates_dir / file_path
            if candidate_file.exists():
                file_path_obj = candidate_file
            else:
                logger.error(f"文件不存在: {file_path}")
                return {"success": False, "error": f"文件不存在: {file_path}"}
        
        logger.info(f"加载文件: {file_path_obj}")
        with open(file_path_obj, 'r', encoding='utf-8') as f:
            kg_candidate_json = json.load(f)
        
        # 处理单个kg_candidate
        logger.info("开始处理kg_candidate...")
        result = kg_manager.receive_kg_candidate(kg_candidate_json)
        
        # 输出处理结果
        logger.info("=" * 50)
        logger.info("单个kg_candidate处理完成")
        
        if result.get("success", False):
            stats = result.get("stats", {})
            logger.info(f"处理状态: 成功")
            logger.info(f"文件编号: {result.get('file_number', 'unknown')}")
            logger.info(f"实体处理: {stats.get('entities', {}).get('saved', 0)}个")
            logger.info(f"特征处理: {stats.get('features', {}).get('saved', 0)}个")
            logger.info(f"关系处理: {stats.get('relations', {}).get('saved', 0)}个")
            logger.info(f"属性处理: {stats.get('attributes', {}).get('saved', 0)}个")
        else:
            logger.error(f"处理状态: 失败")
            logger.error(f"错误信息: {result.get('message', '未知错误')}")
        
        # 获取当前的KG统计信息
        final_stats = kg_manager.get_stats()
        if final_stats.get('success', False):
            logger.info(f"当前实体总数: {final_stats.get('entity_count', 0)}")
            logger.info(f"当前关系总数: {final_stats.get('relation_count', 0)}")
            logger.info(f"当前特征总数: {final_stats.get('feature_count', 0)}")
            logger.info(f"当前属性总数: {final_stats.get('attribute_count', 0)}")
        
        logger.info(f"输出目录: {kg_data_dir}")
        logger.info("=" * 50)
        
        # 返回结果
        result["kg_data_dir"] = str(kg_data_dir)
        result["final_stats"] = final_stats
        return result
        
    except Exception as e:
        logger.error(f"处理kg_candidate文件失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"success": False, "error": str(e)}


def build_kg_data_for_id(process_id: str, clear_existing: bool = False) -> dict:
    """
    为指定处理流ID构建知识图谱数据（批量处理整个目录）
    
    Args:
        process_id: 处理流ID
        clear_existing: 是否清除现有的kg_data目录（默认False，即合并模式）
        
    Returns:
        处理统计信息字典
    """
    logger.info("=" * 50)
    logger.info(f"开始批量构建知识图谱数据，处理流ID: {process_id}")
    logger.info("=" * 50)
    
    # 构建目录路径
    kg_candidates_dir = get_output_path(process_id, "kg_candidates")
    kg_data_dir = get_output_path(process_id, "kg_data")
    
    # 检查kg_candidates目录是否存在
    if not kg_candidates_dir.exists():
        logger.error(f"kg_candidates目录不存在: {kg_candidates_dir}")
        return {"success": False, "error": f"kg_candidates目录不存在: {kg_candidates_dir}"}
    
    # 检查kg_candidates目录中是否有文件
    kg_candidate_files = list(kg_candidates_dir.glob("*.json"))
    if not kg_candidate_files:
        logger.warning(f"kg_candidates目录中没有JSON文件: {kg_candidates_dir}")
        return {"success": False, "error": f"kg_candidates目录中没有JSON文件"}
    
    logger.info(f"kg_candidates目录: {kg_candidates_dir}")
    logger.info(f"找到 {len(kg_candidate_files)} 个候选文件")
    logger.info(f"kg_data输出目录: {kg_data_dir}")
    
    # 如果clear_existing为True，则删除现有的kg_data目录
    if clear_existing and kg_data_dir.exists():
        logger.warning(f"清除现有的kg_data目录: {kg_data_dir}")
        import shutil
        shutil.rmtree(kg_data_dir)
    
    # 确保kg_data目录存在
    kg_data_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # 初始化KGManager
        logger.info("初始化KGManager...")
        kg_manager = KGManager(
            kg_data_dir=str(kg_data_dir),
            workflow_id=process_id
        )
        
        # 处理kg_candidates目录中的所有文件
        logger.info("开始批量处理kg_candidates文件...")
        stats = kg_manager.process_kg_candidates_directory(str(kg_candidates_dir))
        
        # 输出统计信息
        logger.info("=" * 50)
        logger.info("知识图谱数据批量构建完成")
        logger.info(f"总文件数: {stats.get('total_files', 0)}")
        logger.info(f"成功处理: {stats.get('successful_files', 0)}")
        logger.info(f"失败处理: {stats.get('failed_files', 0)}")
        
        # 从total_stats中获取详细统计
        total_stats = stats.get('total_stats', {})
        logger.info(f"实体处理: {total_stats.get('entities_saved', 0)}个")
        logger.info(f"特征处理: {total_stats.get('features_saved', 0)}个")
        logger.info(f"关系处理: {total_stats.get('relations_saved', 0)}个")
        logger.info(f"属性处理: {total_stats.get('attributes_saved', 0)}个")
        
        # 获取最终的KG统计信息
        final_stats = kg_manager.get_stats()
        if final_stats.get('success', False):
            logger.info(f"实体总数: {final_stats.get('entity_count', 0)}")
            logger.info(f"关系总数: {final_stats.get('relation_count', 0)}")
            logger.info(f"特征总数: {final_stats.get('feature_count', 0)}")
            logger.info(f"属性总数: {final_stats.get('attribute_count', 0)}")
        
        logger.info(f"输出目录: {kg_data_dir}")
        logger.info("=" * 50)
        
        # 返回统计信息
        stats["success"] = True
        stats["kg_data_dir"] = str(kg_data_dir)
        stats["final_stats"] = final_stats
        return stats
        
    except Exception as e:
        logger.error(f"构建知识图谱数据失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"success": False, "error": str(e)}


def stage6_build_kg_data_for_id(process_id: str, clear_existing: bool = False) -> bool:
    """
    第六阶段：构建知识图谱数据（可作为pipeline的一个阶段）
    
    Args:
        process_id: 处理流ID
        clear_existing: 是否清除现有的kg_data目录
        
    Returns:
        是否成功
    """
    logger.info("=" * 50)
    logger.info(f"开始第六阶段：为处理流 {process_id} 构建知识图谱数据")
    logger.info("=" * 50)
    
    result = build_kg_data_for_id(process_id, clear_existing)
    
    if result.get("success", False):
        logger.info("第六阶段完成")
        return True
    else:
        logger.error(f"第六阶段失败: {result.get('error', '未知错误')}")
        return False


def integrate_with_existing_pipeline(process_id: str, prompt_version: str = "v1", 
                                     include_stage6: bool = True, clear_existing: bool = False) -> bool:
    """
    与现有pipeline集成，作为第六阶段
    
    Args:
        process_id: 处理流ID
        prompt_version: prompt版本（v1 或 v2）
        include_stage6: 是否包含第六阶段（kg_data构建）
        clear_existing: 是否清除现有的kg_data目录
        
    Returns:
        是否成功
    """
    logger.info(f"开始为处理流 {process_id} 执行扩展数据构造流程")
    logger.info(f"使用 prompt 版本: {prompt_version}")
    logger.info(f"包含第六阶段: {include_stage6}")
    
    # 首先需要导入现有的pipeline函数
    try:
        from pipeline.memory_pre import (
            stage1_construct_dialogues_for_id,
            stage2_construct_episodes_for_id,
            stage3_form_kg_candidates_for_id,
            stage4_form_scenes_for_id,
            stage5_form_scene_features_for_id
        )
    except ImportError:
        logger.error("无法导入现有的pipeline函数")
        return False
    
    # 第一阶段：构造 dialogues
    if not stage1_construct_dialogues_for_id(process_id):
        logger.warning("第一阶段失败，跳过后续阶段")
        return False
    
    # 第二阶段：构造 episodes
    if not stage2_construct_episodes_for_id(process_id):
        logger.warning("第二阶段失败，跳过第三阶段")
        return False
    
    # 第三阶段：形成KG候选
    if not stage3_form_kg_candidates_for_id(process_id, prompt_version):
        logger.warning("第三阶段失败")
        return False
    
    # 第四阶段：形成 scene
    if not stage4_form_scenes_for_id(process_id):
        logger.warning("第四阶段失败")
        return False
    
    # 第五阶段：形成 scene 特征（可选）
    # 注意：第五阶段会更新kg_candidates文件，添加特征信息
    # 因此第六阶段应该在第五阶段之后执行
    try:
        if not stage5_form_scene_features_for_id(process_id, force_update=False):
            logger.warning("第五阶段失败，但继续执行第六阶段")
    except Exception as e:
        logger.warning(f"第五阶段执行异常: {e}，但继续执行第六阶段")
    
    # 第六阶段：构建知识图谱数据
    if include_stage6:
        if not stage6_build_kg_data_for_id(process_id, clear_existing):
            logger.warning("第六阶段失败")
            # 第六阶段失败不视为整个流程失败，因为它是可选的增强功能
            # 但仍然记录警告
    
    stage_count = 6 if include_stage6 else 5
    logger.info("=" * 50)
    logger.info(f"处理流 {process_id} 的所有数据构造流程完成（包含 {stage_count} 个阶段）")
    logger.info(f"使用 prompt 版本: {prompt_version}")
    logger.info("=" * 50)
    return True


def stage6_process_single_file(process_id: str, file_path: str, clear_existing: bool = False) -> bool:
    """
    第六阶段：处理单个kg_candidate文件（可作为pipeline的一个阶段）
    
    Args:
        process_id: 处理流ID
        file_path: kg_candidate文件路径
        clear_existing: 是否清除现有的kg_data目录
        
    Returns:
        是否成功
    """
    logger.info("=" * 50)
    logger.info(f"开始第六阶段：为处理流 {process_id} 处理单个kg_candidate文件")
    logger.info("=" * 50)
    
    result = process_single_kg_candidate(process_id, file_path, clear_existing)
    
    if result.get("success", False):
        logger.info("第六阶段完成")
        return True
    else:
        logger.error(f"第六阶段失败: {result.get('error', '未知错误')}")
        return False


def main():
    """
    主函数：命令行接口
    """
    parser = argparse.ArgumentParser(
        description="知识图谱数据构建流程：从kg_candidates生成kg_data"
    )
    parser.add_argument("--id", type=str, required=True,
                       help="处理流ID（必需）")
    parser.add_argument("--clear", action="store_true",
                       help="清除现有的kg_data目录（默认False，即合并模式）")
    parser.add_argument("--integrate", action="store_true",
                       help="与现有pipeline集成，执行完整流程（包含前5个阶段）")
    parser.add_argument("--kg-prompt-version", type=str, default="v1",
                       help="KG候选生成的prompt版本（v1 或 v2，默认v1），仅在--integrate时使用")
    parser.add_argument("--file", type=str,
                       help="处理单个kg_candidate文件（文件名，如00001.json）")
    parser.add_argument("--file-path", type=str,
                       help="处理单个kg_candidate文件（完整路径）")
    
    args = parser.parse_args()
    
    logger.info(f"开始执行知识图谱数据构建流程，处理流ID: {args.id}")
    logger.info(f"清除模式: {args.clear}")
    logger.info(f"集成模式: {args.integrate}")
    
    # 检查是否指定了单个文件
    if args.file or args.file_path:
        # 处理单个文件模式
        if args.file and args.file_path:
            logger.error("不能同时指定--file和--file-path参数")
            return
        
        file_to_process = args.file_path if args.file_path else args.file
        logger.info(f"处理单个文件: {file_to_process}")
        
        if args.integrate:
            logger.error("单个文件模式不支持--integrate参数")
            return
        
        success = stage6_process_single_file(args.id, file_to_process, args.clear)
    elif args.integrate:
        # 集成模式：执行完整pipeline（包含第六阶段）
        logger.info(f"KG prompt 版本: {args.kg_prompt_version}")
        success = integrate_with_existing_pipeline(
            args.id,
            args.kg_prompt_version,
            include_stage6=True,
            clear_existing=args.clear
        )
    else:
        # 独立模式：批量处理整个目录（默认行为）
        success = stage6_build_kg_data_for_id(args.id, args.clear)
    
    if success:
        logger.info("=" * 50)
        logger.info(f"处理流 {args.id} 的知识图谱数据构建流程完成")
        logger.info("=" * 50)
    else:
        logger.error("知识图谱数据构建流程失败")


if __name__ == "__main__":
    main()