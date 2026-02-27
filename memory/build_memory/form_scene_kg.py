#!/usr/bin/env python3
# 2026-01-26 changshengEVA
"""
Scene Feature Extraction 模块。
扫描所有已生成 kg 和 scene 的 episode，使用 scene_feature_v1 prompt 提取实体特征。
将特征保存到对应的 kg_candidates 文件中，并更新 episode_situation.json 状态。
"""

import os
import sys
import json
import yaml
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Callable
from tqdm import tqdm

# 添加项目根目录到 Python 路径，确保可以导入 load_model
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 导入 episode 状态管理器
try:
    from .episode_status_manager import get_status_manager
except ImportError:
    # 如果相对导入失败，尝试绝对导入
    sys.path.insert(0, str(Path(__file__).parent))
    from episode_status_manager import get_status_manager

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 路径配置
CONFIG_PATH = PROJECT_ROOT / "config" / "prompt" / "scene.yaml"

def load_prompts(memory_owner_name: str = "changshengEVA") -> Dict:
    """从 config/prompt/scene.yaml 加载 prompts，并替换 <memory_owner_name> 占位符"""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 替换 prompts 中的 <memory_owner_name> 占位符
    if isinstance(config, dict):
        for key, value in config.items():
            if isinstance(value, str):
                config[key] = value.replace('<memory_owner_name>', memory_owner_name)
    
    return config

def ensure_directory(path: Path):
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)

def extract_workflow_id_from_path(memory_root: Path) -> str:
    """
    从 memory 根目录路径中提取工作流 ID。
    路径格式: .../data/memory/{workflow_id}/
    如果无法提取，返回 "default"。
    """
    try:
        parts = memory_root.parts
        if "memory" in parts:
            idx = parts.index("memory")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        workflow_id = memory_root.name
        if workflow_id and workflow_id != "memory":
            return workflow_id
    except Exception:
        pass
    return "default"

def get_memory_root(workflow_id: str = "test2") -> Path:
    """获取 memory 根目录"""
    return PROJECT_ROOT / "data" / "memory" / workflow_id

def get_episode_situation_path(memory_root: Path) -> Path:
    """获取 episode_situation.json 路径"""
    return memory_root / "episodes" / "episode_situation.json"

def get_scene_root(memory_root: Path) -> Path:
    """获取 scene 根目录"""
    return memory_root / "scene"

def get_kg_candidates_root(memory_root: Path) -> Path:
    """获取 kg_candidates 根目录"""
    return memory_root / "kg_candidates"

def load_episode_situation(episode_situation_path: Path) -> Dict:
    """加载 episode_situation.json 文件"""
    with open(episode_situation_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_episode_situation(episode_situation_path: Path, data: Dict):
    """保存 episode_situation.json 文件"""
    with open(episode_situation_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def find_eligible_episodes(episode_situation: Dict) -> List[Dict]:
    """
    找到符合条件的 episode：
    1. kg_candidates_generated == True
    2. scene_generated == True
    3. feature_generated != True (如果已生成，根据参数决定是否跳过)
    
    返回 episode 信息列表
    """
    episodes = episode_situation.get("episodes", {})
    eligible = []
    
    for episode_key, episode_data in episodes.items():
        kg_generated = episode_data.get("kg_candidates_generated", False)
        scene_generated = episode_data.get("scene_generated", False)
        feature_generated = episode_data.get("feature_generated", False)
        
        if kg_generated and scene_generated:
            eligible.append({
                "episode_key": episode_key,
                "episode_data": episode_data,
                "feature_generated": feature_generated
            })
    
    return eligible

def load_scene_file(scene_root: Path, scene_file_name: str) -> Dict:
    """加载 scene 文件"""
    scene_path = scene_root / scene_file_name
    if not scene_path.exists():
        raise FileNotFoundError(f"Scene 文件不存在: {scene_path}")
    
    with open(scene_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def extract_diary_from_scene(scene_data: Dict) -> str:
    """从 scene 数据中提取 diary 字段"""
    return scene_data.get("diary", "")

def load_kg_candidates_file(kg_candidates_root: Path, kg_file_name: str) -> Dict:
    """加载 kg_candidates 文件"""
    kg_path = kg_candidates_root / kg_file_name
    if not kg_path.exists():
        raise FileNotFoundError(f"KG candidates 文件不存在: {kg_path}")
    
    with open(kg_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def extract_entities_from_kg_candidates(kg_data: Dict) -> List[str]:
    """从 kg_candidates 数据中提取实体 ID 列表"""
    entities = []
    kg_candidate = kg_data.get("kg_candidate", {})
    facts = kg_candidate.get("facts", {})
    
    for entity_info in facts.get("entities", []):
        entity_id = entity_info.get("id")
        if entity_id:
            entities.append(entity_id)
    
    return entities

def call_llm_for_features(diary: str, entities: List[str], prompt_template: str, scene_id: str) -> List[Dict]:
    """
    调用 LLM 进行特征提取。
    返回解析后的 JSON 列表，格式为 [{"entity_id": "...", "feature": "...", "scene_id": "..."}, ...]
    """
    from load_model.OpenAIcall import get_llm
    
    # 获取 LLM 实例，温度设为 0.1 以获得更确定性的输出
    llm = get_llm(model_temperature=0.1)
    
    # 构建输入
    entities_str = json.dumps(entities, ensure_ascii=False)
    full_prompt = prompt_template.replace('<DIARY>', diary).replace('<ENTITIES_LIST>', entities_str)
    
    try:
        response_text = llm(full_prompt)
        # 解析 JSON 响应
        # 响应可能包含额外的文本，尝试提取 JSON 部分
        json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
        if json_match:
            response_text = json_match.group(0)
        
        result = json.loads(response_text)
        
        # 验证结果格式
        if not isinstance(result, list):
            raise ValueError(f"LLM 返回的不是列表: {type(result)}")
        
        for item in result:
            if not isinstance(item, dict) or "entity_id" not in item or "feature" not in item:
                raise ValueError(f"列表项格式不正确: {item}")
        
        # 为每个特征添加 scene_id
        for item in result:
            item["scene_id"] = scene_id
        
        return result
    except json.JSONDecodeError as e:
        logger.error(f"解析 LLM 响应失败: {e}")
        logger.error(f"响应文本: {response_text[:500]}...")
        raise
    except Exception as e:
        logger.error(f"调用 LLM 失败: {e}")
        raise

def attach_feature_embeddings(
    features: List[Dict],
    embed_model: Optional[Callable[[Any], Any]] = None
) -> List[Dict]:
    """
    为每个特征生成文本 embedding，并写入 feature_embedding 字段。
    """
    if not features:
        return features

    if embed_model is None:
        from load_model.BGEcall import get_embed_model
        embed_model = get_embed_model()

    for item in features:
        feature_text = item.get("feature", "")
        if not feature_text:
            item["feature_embedding"] = []
            continue

        try:
            embedding = embed_model(feature_text)
            if isinstance(embedding, list):
                item["feature_embedding"] = embedding
            else:
                logger.warning(f"特征 embedding 格式异常，写入空列表: {feature_text[:50]}...")
                item["feature_embedding"] = []
        except Exception as e:
            logger.error(f"生成特征 embedding 失败: {feature_text[:50]}..., 错误: {e}")
            raise

    return features

def add_features_to_kg_candidates(kg_data: Dict, features: List[Dict]) -> Dict:
    """
    将特征添加到 kg_candidates 数据中。
    在 facts 字段下添加 features 子字段。
    """
    kg_data = kg_data.copy()  # 避免修改原始数据
    
    # 确保结构存在
    if "kg_candidate" not in kg_data:
        kg_data["kg_candidate"] = {}
    
    if "facts" not in kg_data["kg_candidate"]:
        kg_data["kg_candidate"]["facts"] = {}
    
    # 添加 features 字段
    kg_data["kg_candidate"]["facts"]["features"] = features
    
    return kg_data

def save_kg_candidates_file(kg_candidates_root: Path, kg_file_name: str, kg_data: Dict):
    """保存 kg_candidates 文件"""
    kg_path = kg_candidates_root / kg_file_name
    with open(kg_path, 'w', encoding='utf-8') as f:
        json.dump(kg_data, f, ensure_ascii=False, indent=2)

def update_episode_status(episode_situation: Dict, episode_key: str, kg_file_name: str):
    """
    更新 episode 状态，添加特征生成信息。
    """
    if episode_key not in episode_situation["episodes"]:
        logger.error(f"Episode {episode_key} 不在 episode_situation 中")
        return
    
    episode_data = episode_situation["episodes"][episode_key]
    episode_data["feature_generated"] = True
    episode_data["feature_generated_at"] = datetime.utcnow().isoformat() + "Z"
    episode_data["feature_file"] = kg_file_name

def process_episode(episode_info: Dict, 
                   memory_root: Path,
                   prompts: Dict,
                   force_update: bool = False,
                   embed_model: Optional[Callable[[Any], Any]] = None) -> bool:
    """
    处理单个 episode，提取特征并保存。
    
    Args:
        episode_info: episode 信息字典
        memory_root: memory 根目录
        prompts: prompt 模板字典
        force_update: 是否强制更新，即使已生成也重新生成
    
    Returns:
        处理成功返回 True，否则返回 False
    """
    episode_key = episode_info["episode_key"]
    episode_data = episode_info["episode_data"]
    feature_generated = episode_info["feature_generated"]
    
    # 如果已生成且不强制更新，则跳过
    if feature_generated and not force_update:
        logger.info(f"Episode {episode_key} 已生成特征，跳过")
        return True
    
    try:
        # 获取文件路径
        scene_file_name = episode_data.get("scene_file")
        kg_file_name = episode_data.get("kg_candidate_file")
        
        if not scene_file_name:
            logger.error(f"Episode {episode_key} 缺少 scene_file 字段")
            return False
        
        if not kg_file_name:
            logger.error(f"Episode {episode_key} 缺少 kg_candidate_file 字段")
            return False
        
        # 加载 scene 文件并提取 diary 和 scene_id
        scene_root = get_scene_root(memory_root)
        scene_data = load_scene_file(scene_root, scene_file_name)
        diary = extract_diary_from_scene(scene_data)
        scene_id = scene_data.get("scene_id", "")
        
        if not diary:
            logger.error(f"Scene 文件 {scene_file_name} 中没有 diary 字段")
            return False
        
        if not scene_id:
            logger.warning(f"Scene 文件 {scene_file_name} 中没有 scene_id 字段，使用默认值")
            scene_id = f"scene_{scene_file_name.replace('.json', '')}"
        
        # 加载 kg_candidates 文件并提取实体列表
        kg_candidates_root = get_kg_candidates_root(memory_root)
        kg_data = load_kg_candidates_file(kg_candidates_root, kg_file_name)
        entities = extract_entities_from_kg_candidates(kg_data)
        
        if not entities:
            logger.warning(f"KG candidates 文件 {kg_file_name} 中没有实体，跳过特征提取")
            # 仍然标记为已生成，但特征列表为空
            features = []
        else:
            # 获取 prompt 模板
            prompt_key = "scene_feature_v1"
            prompt_template = prompts.get(prompt_key, "")
            
            if not prompt_template:
                logger.error(f"未找到 prompt 模板: {prompt_key}")
                return False
            
            # 调用 LLM 生成特征
            features = call_llm_for_features(diary, entities, prompt_template, scene_id)

        # 为特征补充文本 embedding 字段
        features = attach_feature_embeddings(features, embed_model=embed_model)
        
        # 将特征添加到 kg_candidates 数据中
        kg_data_with_features = add_features_to_kg_candidates(kg_data, features)
        
        # 保存更新后的 kg_candidates 文件
        save_kg_candidates_file(kg_candidates_root, kg_file_name, kg_data_with_features)
        
        logger.info(f"Episode {episode_key}: 成功提取 {len(features)} 个特征")
        return True
        
    except Exception as e:
        logger.error(f"处理 episode {episode_key} 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def scan_and_extract_features(workflow_id: str = "test2",
                             force_update: bool = False,
                             use_tqdm: bool = True,
                             memory_owner_name: str = "changshengEVA",
                             embed_model: Optional[Callable[[Any], Any]] = None):
    """
    主函数：扫描所有符合条件的 episode，提取特征。
    
    Args:
        workflow_id: 工作流 ID，对应 data/memory/{workflow_id} 目录
        force_update: 是否强制更新特征文件（即使已生成也重新生成）
        use_tqdm: 是否使用 tqdm 显示进度条
        memory_owner_name: 记忆所有者名称，用于替换 prompt 中的占位符
    """
    # 获取 memory 根目录
    memory_root = get_memory_root(workflow_id)
    
    # 检查目录是否存在
    if not memory_root.exists():
        logger.error(f"Memory 目录不存在: {memory_root}")
        return
    
    # 加载 episode_situation.json
    episode_situation_path = get_episode_situation_path(memory_root)
    if not episode_situation_path.exists():
        logger.error(f"Episode situation 文件不存在: {episode_situation_path}")
        return
    
    episode_situation = load_episode_situation(episode_situation_path)
    
    # 加载 prompts，并替换 <memory_owner_name> 占位符
    prompts = load_prompts(memory_owner_name=memory_owner_name)
    if not prompts:
        logger.error("未找到 scene prompts")
        return
    
    # 找到符合条件的 episode
    eligible_episodes = find_eligible_episodes(episode_situation)
    
    if not eligible_episodes:
        logger.info("没有找到符合条件的 episode")
        return
    
    logger.info(f"找到 {len(eligible_episodes)} 个符合条件的 episode")
    
    # 处理每个 episode
    if use_tqdm:
        episode_iter = tqdm(eligible_episodes, desc="提取特征")
    else:
        episode_iter = eligible_episodes
    
    success_count = 0
    for episode_info in episode_iter:
        if process_episode(
            episode_info,
            memory_root,
            prompts,
            force_update,
            embed_model=embed_model
        ):
            success_count += 1
            
            # 更新 episode_situation.json 状态
            update_episode_status(episode_situation, 
                                 episode_info["episode_key"], 
                                 episode_info["episode_data"].get("kg_candidate_file", ""))
    
    # 保存更新后的 episode_situation.json
    if success_count > 0:
        save_episode_situation(episode_situation_path, episode_situation)
        logger.info(f"已更新 episode_situation.json")
    
    logger.info(f"成功处理 {success_count}/{len(eligible_episodes)} 个 episode")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Scene Feature Extraction 模块")
    parser.add_argument("--workflow-id", type=str, default="test2",
                       help="工作流 ID，对应 data/memory/{workflow_id} 目录")
    parser.add_argument("--force-update", action="store_true",
                       help="强制更新特征文件（即使已生成也重新生成）")
    parser.add_argument("--no-tqdm", action="store_true",
                       help="不使用 tqdm 进度条")
    
    args = parser.parse_args()
    
    scan_and_extract_features(
        workflow_id=args.workflow_id,
        force_update=args.force_update,
        use_tqdm=not args.no_tqdm
    )
