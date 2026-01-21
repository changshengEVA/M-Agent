#!/usr/bin/env python3
# 2026-01-18 changshengEVA
"""
Knowledge Graph Candidate Formation 模块。
扫描 kg_available 为 true 的 episode，提取对话内容，使用 kg_filter prompt 生成 kg_candidate。
每个 kg_candidate 保存为单独文件，存储在 {id}/kg_candidates 目录，按编号从00001开始存储。

使用 prompt_version 参数控制模型使用的 prompt 版本。
"""

import os
import sys
import json
import yaml
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
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

# 配置日志：只显示 WARNING 及以上级别，减少输出噪音
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 路径配置
DIALOGUES_ROOT = PROJECT_ROOT / "data" / "memory" / "default" / "dialogues"
EPISODES_ROOT = PROJECT_ROOT / "data" / "memory" / "default" / "episodes"
CONFIG_PATH = PROJECT_ROOT / "config" / "prompt" / "kg_filter.yaml"

def load_prompts() -> Dict:
    """从 config/prompt/kg_filter.yaml 加载 prompts"""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config


def ensure_directory(path: Path):
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)

def scan_eligibility_files(episodes_root: Path = None) -> List[Path]:
    """
    扫描所有 eligibility 文件。
    返回所有找到的 eligibility 文件路径列表。
    
    Args:
        episodes_root: episodes根目录，如果为None则使用默认的EPISODES_ROOT
    """
    if episodes_root is None:
        episodes_root = EPISODES_ROOT
    
    eligibility_files = []
    # 扫描 by_dialogue 目录
    by_dialogue_dir = episodes_root / "by_dialogue"
    if not by_dialogue_dir.exists():
        return eligibility_files
    
    for dialogue_dir in by_dialogue_dir.iterdir():
        if dialogue_dir.is_dir():
            eligibility_file = dialogue_dir / "eligibility_v1.json"
            if eligibility_file.exists():
                eligibility_files.append(eligibility_file)
    
    return eligibility_files

def get_kg_candidates_root(episodes_root: Path = None) -> Path:
    """
    获取 kg_candidates 根目录。
    格式: {episodes_root}/../kg_candidates
    """
    if episodes_root is None:
        episodes_root = EPISODES_ROOT
    return episodes_root.parent / "kg_candidates"

def get_next_kg_candidate_number(kg_candidates_root: Path) -> int:
    """
    获取下一个 kg_candidate 文件编号。
    扫描现有文件，返回最大的编号+1，如果没有任何文件则返回1。
    """
    ensure_directory(kg_candidates_root)
    
    max_number = 0
    for file_path in kg_candidates_root.iterdir():
        if file_path.is_file() and file_path.suffix == '.json':
            try:
                # 文件名格式: 00001.json, 00002.json 等
                number_str = file_path.stem
                number = int(number_str)
                if number > max_number:
                    max_number = number
            except ValueError:
                continue
    
    return max_number + 1

def get_kg_candidate_path_by_number(kg_candidates_root: Path, number: int) -> Path:
    """
    根据编号生成 kg_candidate 文件路径。
    格式: {kg_candidates_root}/{number:05d}.json
    """
    return kg_candidates_root / f"{number:05d}.json"

def load_eligibility(eligibility_file: Path) -> Dict:
    """加载 eligibility JSON 文件"""
    with open(eligibility_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_episodes(dialogue_dir: Path, episode_version: str = "v1") -> Dict:
    """加载 episode JSON 文件"""
    episode_file = dialogue_dir / f"episodes_{episode_version}.json"
    if not episode_file.exists():
        raise FileNotFoundError(f"Episode file not found: {episode_file}")
    
    with open(episode_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def find_dialogue_file(dialogue_id: str, dialogues_root: Path = None) -> Optional[Path]:
    """
    根据 dialogue_id 查找对应的对话文件。
    搜索 dialogues 目录下的所有子目录。
    
    Args:
        dialogue_id: 对话ID
        dialogues_root: 对话根目录，如果为None则使用默认的DIALOGUES_ROOT
    """
    if dialogues_root is None:
        dialogues_root = DIALOGUES_ROOT
    
    # 搜索所有可能的目录结构
    search_patterns = [
        ("by_user", "*", "*"),      # by_user/{user_id}/{year-month}/
        ("by_flipflop", "*", "*"),  # by_flipflop/{flipflop_id}/{year-month}/
        ("", "*", "*"),             # 直接搜索根目录下的 {user_id}/{year-month}/
    ]
    
    for base_dir, user_pattern, date_pattern in search_patterns:
        search_dir = dialogues_root / base_dir
        if not search_dir.exists():
            continue
            
        # 遍历用户目录
        for user_dir in search_dir.iterdir():
            if user_dir.is_dir():
                # 遍历年月目录
                for year_month_dir in user_dir.iterdir():
                    if year_month_dir.is_dir():
                        dialogue_file = year_month_dir / f"{dialogue_id}.json"
                        if dialogue_file.exists():
                            return dialogue_file
    
    # 如果没有找到，尝试递归搜索整个目录
    for file_path in dialogues_root.rglob(f"{dialogue_id}.json"):
        if file_path.is_file():
            return file_path
    
    return None

def load_dialogue(dialogue_file: Path) -> Dict:
    """加载对话 JSON 文件"""
    with open(dialogue_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def build_episode_with_content(episode_meta: Dict, dialogue_data: Dict) -> Dict:
    """
    根据 episode 元数据和对话数据构建完整的 episode 内容。
    
    Args:
        episode_meta: episode 元数据（来自 episodes_v1.json）
        dialogue_data: 完整的对话数据
        
    Returns:
        包含完整内容的 episode 字典
    """
    # 提取 turn_span
    turn_span = episode_meta.get('turn_span', [0, 0])
    start_id, end_id = turn_span[0], turn_span[1]
    
    # 提取对应的对话轮次
    turns = dialogue_data.get('turns', [])
    episode_turns = []
    
    for turn in turns:
        turn_id = turn.get('turn_id', -1)
        if start_id <= turn_id <= end_id:
            episode_turns.append(turn)
    
    # 构建完整的 episode 结构
    episode_with_content = episode_meta.copy()
    episode_with_content['turns'] = episode_turns
    episode_with_content['dialogue_content'] = {
        'dialogue_id': dialogue_data.get('dialogue_id', ''),
        'user_id': dialogue_data.get('user_id', ''),
        'participants': dialogue_data.get('participants', []),
        'meta': dialogue_data.get('meta', {})
    }
    
    return episode_with_content

def filter_kg_available_episodes(eligibility_data: Dict, episode_data: Dict) -> List[Dict]:
    """
    过滤出 kg_available 为 true 的 episode。
    
    Args:
        eligibility_data: eligibility 数据
        episode_data: episode 数据
        
    Returns:
        包含 episode_id 和完整 episode 内容的列表
    """
    kg_available_episodes = []
    
    # 建立 episode_id 到 episode 元数据的索引
    episode_index = {}
    for ep in episode_data.get("episodes", []):
        ep_id = ep.get("episode_id")
        if ep_id:
            episode_index[ep_id] = ep
    
    # 遍历 eligibility 结果
    for result in eligibility_data.get("results", []):
        if result.get("kg_available", False):
            ep_id = result.get("episode_id")
            ep = episode_index.get(ep_id)
            if ep:
                kg_available_episodes.append({
                    "episode_id": ep_id,
                    "dialogue_id": result.get("dialogue_id", ""),
                    "episode_meta": ep,
                    "eligibility_result": result
                })
    
    return kg_available_episodes

def call_openai_for_kg_candidate(episode_with_content: Dict, prompt_template: str) -> Dict:
    """
    调用 OpenAI 进行 kg_candidate 提取。
    返回解析后的 JSON 结果。
    """
    from load_model.OpenAIcall import get_llm
    
    # 获取 LLM 实例，温度设为 0.1 以获得更确定性的输出
    llm = get_llm(model_temperature=0.1)
    
    # 将 episode JSON 转换为字符串用于插入
    episode_str = json.dumps(episode_with_content, ensure_ascii=False, indent=2)
    full_prompt = prompt_template.replace('<txt_string>', episode_str)
    
    try:
        response_text = llm(full_prompt)
        # 解析 JSON 响应
        # 响应可能包含额外的文本，尝试提取 JSON 部分
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            response_text = json_match.group(0)
        
        result = json.loads(response_text)
        return result
    except json.JSONDecodeError as e:
        logger.error(f"解析 OpenAI 响应失败: {e}")
        logger.error(f"响应文本: {response_text[:500]}...")
        raise
    except Exception as e:
        logger.error(f"调用 OpenAI 失败: {e}")
        raise

def save_kg_candidates_as_individual_files(kg_candidates: List[Dict], kg_candidates_root: Path):
    """
    将每个 kg_candidate 保存为单独的文件，按照编号从00001开始存储。
    
    Args:
        kg_candidates: kg_candidate 列表
        kg_candidates_root: kg_candidates 根目录
    """
    ensure_directory(kg_candidates_root)
    
    # 获取下一个起始编号
    start_number = get_next_kg_candidate_number(kg_candidates_root)
    
    saved_files = []
    for i, kg_candidate in enumerate(kg_candidates):
        file_number = start_number + i
        file_path = get_kg_candidate_path_by_number(kg_candidates_root, file_number)
        
        # 构建单个 kg_candidate 文件内容
        kg_candidate_output = {
            "file_number": file_number,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            **kg_candidate
        }
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(kg_candidate_output, f, ensure_ascii=False, indent=2)
        
        saved_files.append(file_path)
    
    return saved_files

def extract_workflow_id_from_path(episodes_root: Path) -> str:
    """
    从 episodes 根目录路径中提取工作流 ID。
    路径格式: .../data/memory/{workflow_id}/episodes
    如果无法提取，返回 "default"。
    """
    try:
        # 将路径转换为字符串并标准化
        parts = episodes_root.parts
        # 查找 "memory" 的索引
        if "memory" in parts:
            idx = parts.index("memory")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        # 如果找不到，尝试从路径名推断
        # 假设 episodes_root 的父目录是 workflow_id
        workflow_id = episodes_root.parent.name
        if workflow_id and workflow_id != "episodes":
            return workflow_id
    except Exception:
        pass
    return "default"

def process_eligibility_file(eligibility_file: Path,
                            prompts: Dict,
                            prompt_version: str = "v1",
                            dialogues_root: Path = None,
                            episodes_root: Path = None,
                            kg_candidates_root: Path = None,
                            force_update: bool = False) -> bool:
    """
    处理单个 eligibility 文件，生成 kg_candidate（新格式：每个kg_candidate单独文件）
    
    Args:
        eligibility_file: eligibility 文件路径
        prompts: prompt 模板字典
        prompt_version: prompt 版本（v1 或 v2）
        dialogues_root: 对话根目录
        episodes_root: episodes根目录
        kg_candidates_root: kg_candidates根目录（如果为None，则使用默认位置）
        force_update: 是否强制更新，即使已生成也重新生成
    """
    try:
        # 确定 episodes_root
        if episodes_root is None:
            episodes_root = EPISODES_ROOT
        
        # 提取工作流 ID
        workflow_id = extract_workflow_id_from_path(episodes_root)
        
        # 获取状态管理器
        status_manager = get_status_manager(workflow_id=workflow_id)
        
        # 加载 eligibility
        eligibility_data = load_eligibility(eligibility_file)
        
        # 获取 dialogue_id
        if eligibility_data.get("results"):
            dialogue_id = eligibility_data["results"][0].get("dialogue_id", "")
        else:
            # 从目录名推断
            dialogue_id = eligibility_file.parent.name
        
        # 加载对应的 episodes
        dialogue_dir = eligibility_file.parent
        episode_data = load_episodes(dialogue_dir)
        
        # 验证 dialogue_id 一致性
        if episode_data.get("dialogue_id") and episode_data["dialogue_id"] != dialogue_id:
            logger.warning(f"Dialogue ID mismatch: eligibility={dialogue_id}, episode={episode_data['dialogue_id']}")
            dialogue_id = episode_data["dialogue_id"]
        
        # 查找并加载对应的对话文件
        dialogue_file = find_dialogue_file(dialogue_id, dialogues_root)
        if not dialogue_file:
            logger.error(f"找不到对话文件: {dialogue_id}")
            return False
        
        dialogue_data = load_dialogue(dialogue_file)
        
        # 过滤出 kg_available 为 true 的 episode
        kg_available_episodes = filter_kg_available_episodes(eligibility_data, episode_data)
        
        if not kg_available_episodes:
            logger.info(f"对话 {dialogue_id} 没有 kg_available 为 true 的 episode，跳过")
            return True
        
        # 为每个 kg_available episode 生成 kg_candidate
        kg_candidates = []
        skipped_count = 0
        
        for kg_ep in kg_available_episodes:
            episode_id = kg_ep["episode_id"]
            episode_meta = kg_ep["episode_meta"]
            episode_key = f"{dialogue_id}:{episode_id}"
            
            # 检查是否已生成 kg_candidate
            if not force_update and status_manager.is_kg_candidates_generated(episode_key):
                logger.info(f"Episode {episode_key} 已生成 kg_candidate，跳过")
                skipped_count += 1
                continue
            
            # 构建包含完整内容的 episode
            episode_with_content = build_episode_with_content(episode_meta, dialogue_data)
            
            # 根据 prompt_version 选择 prompt 模板
            prompt_key = f"kg_strong_filter_{prompt_version}"
            prompt_template = prompts.get(prompt_key, "")
            
            # 如果找不到指定版本的 prompt，尝试使用第一个可用的 prompt
            if not prompt_template:
                logger.warning(f"未找到指定版本的 prompt: {prompt_key}，尝试使用第一个可用的 prompt")
                if prompts:
                    prompt_key = list(prompts.keys())[0]
                    prompt_template = prompts.get(prompt_key, "")
                    logger.info(f"使用替代 prompt: {prompt_key}")
            
            if not prompt_template:
                logger.error(f"未找到任何可用的 prompt 模板")
                return False
            
            try:
                kg_result = call_openai_for_kg_candidate(episode_with_content, prompt_template)
                kg_candidates.append({
                    "episode_id": episode_id,
                    "dialogue_id": dialogue_id,
                    "kg_candidate": kg_result,
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                    "prompt_version": prompt_version,
                    "prompt_key": prompt_key
                })
            except Exception as e:
                logger.error(f"为 episode {episode_id} 生成 kg_candidate 失败: {e}")
                # 继续处理其他 episode
                continue
        
        if not kg_candidates:
            if skipped_count > 0:
                logger.info(f"对话 {dialogue_id} 的所有 {skipped_count} 个 episode 已生成 kg_candidate，跳过")
            else:
                logger.info(f"对话 {dialogue_id} 没有成功生成任何 kg_candidate")
            return True
        
        # 保存 kg_candidate 文件（新格式：单独文件）
        if kg_candidates_root is None:
            kg_candidates_root = get_kg_candidates_root(episodes_root)
        
        saved_files = save_kg_candidates_as_individual_files(kg_candidates, kg_candidates_root)
        
        # 更新状态
        for i, kg_candidate in enumerate(kg_candidates):
            episode_id = kg_candidate["episode_id"]
            episode_key = f"{dialogue_id}:{episode_id}"
            kg_file = saved_files[i].name if i < len(saved_files) else f"unknown_{i}.json"
            status_manager.mark_kg_candidates_generated(episode_key, kg_file, kg_candidate["generated_at"])
        
        logger.info(f"为对话 {dialogue_id} 生成 {len(saved_files)} 个 kg_candidate 文件，跳过 {skipped_count} 个已生成的，保存到 {kg_candidates_root}，使用 prompt 版本: {prompt_version}")
        
        return True
        
    except Exception as e:
        logger.error(f"处理 eligibility 文件 {eligibility_file} 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def scan_and_form_kg_candidates(prompt_version: str = "v1",
                                use_tqdm: bool = True,
                                force_update: bool = False,
                                dialogues_root: Path = None,
                                episodes_root: Path = None,
                                kg_candidates_root: Path = None):
    """
    主函数：扫描所有 eligibility 文件，为需要生成 kg_candidate 的对话创建 kg_candidate。
    使用新格式：每个kg_candidate保存为单独文件。
    
    Args:
        prompt_version: prompt 版本（v1 或 v2，默认 v1）
        use_tqdm: 是否使用 tqdm 显示进度条（默认 True）
        force_update: 是否强制更新 kg_candidate 文件（即使文件已存在，默认 False）
        dialogues_root: 对话根目录，如果为None则使用默认的DIALOGUES_ROOT
        episodes_root: episodes根目录，如果为None则使用默认的EPISODES_ROOT
        kg_candidates_root: kg_candidates根目录，如果为None则使用默认位置（episodes_root/../kg_candidates）
    """
    # 确定使用的根目录
    if episodes_root is None:
        episodes_root = EPISODES_ROOT
    if dialogues_root is None:
        dialogues_root = DIALOGUES_ROOT
    if kg_candidates_root is None:
        kg_candidates_root = get_kg_candidates_root(episodes_root)
    
    # 确保 episodes 根目录存在
    ensure_directory(episodes_root)
    
    # 加载 prompts
    prompts = load_prompts()
    if not prompts:
        logger.error("未找到 kg_filter prompts")
        return
    
    # 验证 prompt_version 是否有效
    expected_prompt_key = f"kg_strong_filter_{prompt_version}"
    if expected_prompt_key not in prompts:
        logger.warning(f"未找到指定版本的 prompt: {expected_prompt_key}")
        logger.warning(f"可用的 prompt 版本: {list(prompts.keys())}")
        # 尝试使用第一个可用的 prompt
        if prompts:
            first_key = list(prompts.keys())[0]
            logger.info(f"将使用第一个可用的 prompt: {first_key}")
            # 从第一个 key 中提取版本号
            import re
            match = re.search(r'kg_strong_filter_(v\d+)', first_key)
            if match:
                prompt_version = match.group(1)
                logger.info(f"自动切换到 prompt 版本: {prompt_version}")
    
    # 扫描所有 eligibility 文件
    eligibility_files = scan_eligibility_files(episodes_root)
    
    if not eligibility_files:
        # 没有需要处理的文件，静默退出
        logger.info("没有找到 eligibility 文件")
        return
    
    # 处理文件
    if use_tqdm:
        file_iter = tqdm(eligibility_files, desc=f"生成 kg_candidates (prompt: {prompt_version})")
    else:
        file_iter = eligibility_files
    
    success_count = 0
    for eligibility_file in file_iter:
        if process_eligibility_file(
            eligibility_file,
            prompts,
            prompt_version,
            dialogues_root,
            episodes_root,
            kg_candidates_root,
            force_update=force_update
        ):
            success_count += 1
    
    logger.info(f"成功处理 {success_count}/{len(eligibility_files)} 个 eligibility 文件，使用 prompt 版本: {prompt_version}")

def clear_all_kg_candidates(kg_candidates_root: Path = None, confirm: bool = False):
    """
    清理所有 kg_candidate 文件（新格式）。
    
    Args:
        kg_candidates_root: kg_candidates根目录，如果为None则使用默认位置
        confirm: 如果为 True，则实际删除文件；如果为 False，只显示将要删除的文件列表
    """
    if kg_candidates_root is None:
        kg_candidates_root = get_kg_candidates_root()
    
    if not kg_candidates_root.exists():
        print(f"kg_candidates 目录不存在: {kg_candidates_root}")
        return
    
    kg_candidate_files = []
    for file_path in kg_candidates_root.iterdir():
        if file_path.is_file() and file_path.suffix == '.json':
            try:
                # 只删除数字命名的文件
                int(file_path.stem)
                kg_candidate_files.append(file_path)
            except ValueError:
                continue
    
    if not kg_candidate_files:
        print("没有找到 kg_candidate 文件")
        return
    
    print(f"找到 {len(kg_candidate_files)} 个 kg_candidate 文件:")
    for kg_candidate_file in kg_candidate_files:
        print(f"  - {kg_candidate_file}")
    
    if not confirm:
        print("\n这只是预览。要实际删除这些文件，请运行: clear_all_kg_candidates(confirm=True)")
        return
    
    # 实际删除文件
    deleted_count = 0
    for kg_candidate_file in kg_candidate_files:
        try:
            kg_candidate_file.unlink()
            print(f"已删除: {kg_candidate_file}")
            deleted_count += 1
        except Exception as e:
            print(f"删除失败 {kg_candidate_file}: {e}")
    
    print(f"\n成功删除 {deleted_count}/{len(kg_candidate_files)} 个文件")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Knowledge Graph Candidate Formation 模块（新格式：每个kg_candidate单独文件）")
    parser.add_argument("--scan", action="store_true", help="扫描并生成 kg_candidates")
    parser.add_argument("--clear", action="store_true", help="清理所有 kg_candidate 文件")
    parser.add_argument("--confirm", action="store_true", help="确认删除（与 --clear 一起使用）")
    parser.add_argument("--prompt-version", default="v1", help="prompt 版本（v1 或 v2，默认 v1）")
    parser.add_argument("--force-update", action="store_true",
                       help="强制更新 kg_candidate 文件（即使文件已存在）")
    
    args = parser.parse_args()
    
    if args.clear:
        clear_all_kg_candidates(confirm=args.confirm)
    elif args.scan:
        scan_and_form_kg_candidates(
            prompt_version=args.prompt_version,
            force_update=args.force_update
        )
    else:
        # 默认行为：扫描并生成
        scan_and_form_kg_candidates(
            prompt_version=args.prompt_version,
            force_update=args.force_update
        )
    
