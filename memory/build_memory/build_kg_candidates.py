#!/usr/bin/env python3
# 2025-12-30 changshengEVA
"""
Knowledge Graph Candidate Generation Module.
使用 kg_strong_filter_v1 prompt 处理所有 scene 文件，生成 KG 候选事实。
"""

import os
import sys
import json
import yaml
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from tqdm import tqdm

# 添加项目根目录到 Python 路径，确保可以导入 load_model
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 路径配置
SCENES_ROOT = PROJECT_ROOT / "data" / "memory" / "scenes"
KG_CANDIDATES_ROOT = PROJECT_ROOT / "data" / "memory" / "kg_candidates"
KG_FILTER_PROMPT_PATH = PROJECT_ROOT / "config" / "prompt" / "kg_filter.yaml"

def ensure_directory(path: Path):
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)

def load_kg_filter_prompt() -> str:
    """
    从 kg_filter.yaml 加载 kg_strong_filter_v1 prompt
    
    Returns:
        prompt 字符串，如果失败则返回空字符串
    """
    try:
        with open(KG_FILTER_PROMPT_PATH, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        # 获取 kg_strong_filter_v1 prompt
        prompt = config.get("kg_strong_filter_v1", "")
        if not prompt:
            logger.error(f"在 {KG_FILTER_PROMPT_PATH} 中找不到 kg_strong_filter_v1 prompt")
            return ""
        
        return prompt
    except Exception as e:
        logger.error(f"加载 kg_filter prompt 失败 {KG_FILTER_PROMPT_PATH}: {e}")
        return ""

def find_all_scene_files() -> List[Tuple[Path, str, str]]:
    """
    扫描所有 scene 文件
    
    Returns:
        List of (scene_file_path, user_id, scene_id)
    """
    scene_files = []
    
    # 扫描 by_user 目录
    by_user_dir = SCENES_ROOT / "by_user"
    if not by_user_dir.exists():
        logger.warning(f"Scene 目录不存在: {by_user_dir}")
        return scene_files
    
    for user_dir in by_user_dir.iterdir():
        if user_dir.is_dir():
            user_id = user_dir.name
            for scene_dir in user_dir.iterdir():
                if scene_dir.is_dir() and scene_dir.name.startswith("scene_"):
                    scene_id = scene_dir.name
                    scene_file = scene_dir / "v1.0.json"
                    if scene_file.exists():
                        scene_files.append((scene_file, user_id, scene_id))
    
    logger.info(f"找到 {len(scene_files)} 个 scene 文件")
    return scene_files

def load_scene_data(scene_file: Path) -> Optional[Dict]:
    """加载 scene JSON 文件"""
    try:
        with open(scene_file, 'r', encoding='utf-8') as f:
            scene_data = json.load(f)
        return scene_data
    except Exception as e:
        logger.error(f"读取 scene 文件失败 {scene_file}: {e}")
        return None

def build_kg_filter_prompt(scene_data: Dict) -> str:
    """
    构建 KG filter prompt，将 scene 数据作为 JSON 字符串插入
    
    Args:
        scene_data: scene 数据字典
        
    Returns:
        完整的 prompt 字符串
    """
    # 加载基础 prompt
    base_prompt = load_kg_filter_prompt()
    if not base_prompt:
        return ""
    
    # 将 scene 数据转换为格式化的 JSON 字符串
    scene_json = json.dumps(scene_data, ensure_ascii=False, indent=2)
    
    # 替换 prompt 中的 <json_string> 占位符
    prompt = base_prompt.replace("<json_string>", scene_json)
    
    return prompt

def call_openai_for_kg_candidates(prompt: str) -> Optional[Dict]:
    """
    调用 OpenAI API 生成 KG 候选
    
    Args:
        prompt: 完整的 prompt 字符串
        
    Returns:
        KG 候选 JSON 字典，如果失败则返回 None
    """
    try:
        from load_model.OpenAIcall import get_llm
        llm = get_llm(model_temperature=0.1)  # 低温度以获得更确定性的输出
        llm_response = llm(prompt)
        
        # 解析 JSON 响应
        import re
        # 尝试提取 JSON 部分
        json_match = re.search(r'\{.*\}', llm_response, re.DOTALL)
        if json_match:
            kg_candidates = json.loads(json_match.group())
        else:
            # 如果找不到 JSON，尝试直接解析整个响应
            kg_candidates = json.loads(llm_response)
        
        return kg_candidates
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        logger.error(f"LLM 响应: {llm_response[:500] if 'llm_response' in locals() else 'N/A'}")
        return None

def normalize_kg_facts(kg_candidates: Dict) -> Dict:
    """
    标准化 KG facts 格式，确保包含 entities, relations, attributes 字段
    
    Args:
        kg_candidates: KG 候选数据字典
        
    Returns:
        标准化后的 KG 候选数据字典
    """
    # 确保有 facts 字段
    if "facts" not in kg_candidates:
        logger.warning("KG 候选缺少 'facts' 字段，将包装为 facts")
        kg_candidates = {"facts": kg_candidates}
    
    facts = kg_candidates.get("facts", {})
    
    # 确保 facts 是字典类型
    if not isinstance(facts, dict):
        logger.warning(f"facts 字段不是字典类型: {type(facts)}，重置为空字典")
        facts = {}
        kg_candidates["facts"] = facts
    
    # 确保包含必需的字段
    required_fields = ["entities", "relations", "attributes"]
    for field in required_fields:
        if field not in facts:
            logger.info(f"facts 缺少 '{field}' 字段，添加为空列表")
            facts[field] = []
        elif not isinstance(facts[field], list):
            logger.warning(f"facts['{field}'] 不是列表类型: {type(facts[field])}，重置为空列表")
            facts[field] = []
    
    return kg_candidates

def save_kg_candidates(kg_candidates: Dict, scene_id: str, user_id: str) -> bool:
    """
    保存 KG 候选到文件
    
    Args:
        kg_candidates: KG 候选数据字典
        scene_id: scene ID
        user_id: 用户 ID
        
    Returns:
        保存成功返回 True，失败返回 False
    """
    try:
        # 确保目录存在
        output_dir = KG_CANDIDATES_ROOT / "strong"
        ensure_directory(output_dir)
        
        # 构建输出文件名
        output_file = output_dir / f"{scene_id}.kg_candidate.json"
        
        # 添加元数据
        kg_candidates_with_meta = {
            "scene_id": scene_id,
            "user_id": user_id,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "prompt_version": "kg_strong_filter_v1",
            **kg_candidates
        }
        
        # 保存文件
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(kg_candidates_with_meta, f, ensure_ascii=False, indent=2)
        
        logger.info(f"KG 候选保存成功: {output_file}")
        return True
    except Exception as e:
        logger.error(f"保存 KG 候选失败: {e}")
        return False

def process_single_scene(scene_file: Path, user_id: str, scene_id: str) -> bool:
    """
    处理单个 scene 文件
    
    Args:
        scene_file: scene 文件路径
        user_id: 用户 ID
        scene_id: scene ID
        
    Returns:
        处理成功返回 True，失败返回 False
    """
    logger.info(f"处理 scene: {scene_id} (用户: {user_id})")
    
    # 1. 加载 scene 数据
    scene_data = load_scene_data(scene_file)
    if not scene_data:
        logger.error(f"无法加载 scene 数据: {scene_file}")
        return False
    
    # 2. 构建 prompt
    prompt = build_kg_filter_prompt(scene_data)
    if not prompt:
        logger.error(f"无法构建 prompt for scene: {scene_id}")
        return False
    
    # 3. 调用 OpenAI API
    kg_candidates = call_openai_for_kg_candidates(prompt)
    if not kg_candidates:
        logger.error(f"无法生成 KG 候选 for scene: {scene_id}")
        return False
    
    # 4. 验证输出格式
    if "facts" not in kg_candidates:
        logger.warning(f"KG 候选缺少 'facts' 字段: {scene_id}")
        # 尝试包装为正确的格式
        kg_candidates = {"facts": kg_candidates}
    
    # 5. 标准化 KG facts 格式
    kg_candidates = normalize_kg_facts(kg_candidates)
    
    # 6. 保存结果
    success = save_kg_candidates(kg_candidates, scene_id, user_id)
    if not success:
        logger.error(f"保存 KG 候选失败: {scene_id}")
        return False
    
    logger.info(f"Scene {scene_id} 处理完成")
    return True

def scan_and_build_kg_candidates() -> Dict:
    """
    扫描所有 scene 文件并构建 KG 候选
    
    Returns:
        处理结果统计字典
    """
    logger.info("开始 scan_and_build_kg_candidates...")
    
    # 确保输出目录存在
    ensure_directory(KG_CANDIDATES_ROOT / "strong")
    
    # 查找所有 scene 文件
    scene_files = find_all_scene_files()
    if not scene_files:
        logger.warning("没有找到 scene 文件")
        return {"status": "no_scenes_found"}
    
    # 处理统计
    stats = {
        "total_scenes": len(scene_files),
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "failed_list": [],
        "start_time": datetime.utcnow().isoformat() + "Z"
    }
    
    # 处理每个 scene
    for scene_file, user_id, scene_id in tqdm(scene_files, desc="处理 scenes"):
        try:
            success = process_single_scene(scene_file, user_id, scene_id)
            if success:
                stats["succeeded"] += 1
            else:
                stats["failed"] += 1
                stats["failed_list"].append({
                    "scene_id": scene_id,
                    "user_id": user_id,
                    "error": "处理失败"
                })
            stats["processed"] += 1
        except Exception as e:
            logger.error(f"处理 scene {scene_id} 时发生异常: {e}")
            stats["failed"] += 1
            stats["failed_list"].append({
                "scene_id": scene_id,
                "user_id": user_id,
                "error": str(e)
            })
            stats["processed"] += 1
    
    # 完成统计
    stats["end_time"] = datetime.utcnow().isoformat() + "Z"
    stats["duration_seconds"] = (datetime.fromisoformat(stats["end_time"][:-1]) - 
                                 datetime.fromisoformat(stats["start_time"][:-1])).total_seconds()
    
    logger.info(f"scan_and_build_kg_candidates 完成!")
    logger.info(f"处理结果: 总共 {stats['total_scenes']} 个 scenes")
    logger.info(f"成功: {stats['succeeded']}, 失败: {stats['failed']}")
    
    return stats

def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="构建 KG 候选事实")
    parser.add_argument("--scan", action="store_true", help="扫描并处理所有 scenes")
    parser.add_argument("--scene", type=str, help="处理指定的 scene (格式: user_id:scene_id)")
    
    args = parser.parse_args()
    
    if args.scene:
        # 处理单个 scene
        try:
            user_id, scene_id = args.scene.split(":")
            scene_file = SCENES_ROOT / "by_user" / user_id / scene_id / "v1.0.json"
            
            if not scene_file.exists():
                logger.error(f"Scene 文件不存在: {scene_file}")
                return
            
            success = process_single_scene(scene_file, user_id, scene_id)
            if success:
                print(f"Scene {scene_id} 处理成功!")
            else:
                print(f"Scene {scene_id} 处理失败!")
        except ValueError:
            logger.error("无效的 scene 格式，请使用 user_id:scene_id")
        except Exception as e:
            logger.error(f"处理单个 scene 失败: {e}")
    
    elif args.scan:
        # 批量处理所有 scenes
        result = scan_and_build_kg_candidates()
        print(f"批量处理完成!")
        print(f"结果: {json.dumps(result, indent=2, ensure_ascii=False)}")
    
    else:
        # 默认行为：扫描并处理
        result = scan_and_build_kg_candidates()
        print(f"处理完成!")
        print(f"结果: {json.dumps(result, indent=2, ensure_ascii=False)}")

if __name__ == "__main__":
    main()