#!/usr/bin/env python3
# 2025-12-27 changshengEVA
"""
Episode Information Scoring 妯″潡銆?
鎵弿 episodes 鐩綍涓嬬殑 episode 鏂囦欢锛屽姣忎釜 episode 杩涜淇℃伅璇勫垎璇勪及銆?
鎻愪緵娓呯悊 qualifications 鏂囦欢鐨勬帴鍙ｃ€?
"""

import os
import json
import yaml
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable
from tqdm import tqdm

from m_agent.paths import CONFIG_DIR, memory_stage_dir
# 娣诲姞椤圭洰鏍圭洰褰曞埌 Python 璺緞锛岀‘淇濆彲浠ュ鍏?load_model

# 閰嶇疆鏃ュ織锛氬彧鏄剧ず WARNING 鍙婁互涓婄骇鍒紝鍑忓皯杈撳嚭鍣煶
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 璺緞閰嶇疆
DIALOGUES_ROOT = memory_stage_dir("default", "dialogues")
EPISODES_ROOT = memory_stage_dir("default", "episodes")
CONFIG_PATH = CONFIG_DIR / "prompt" / "episode.yaml"

def load_prompts(memory_owner_name: str = "changshengEVA") -> Dict:
    """Load scoring prompts and replace the memory owner placeholder."""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    prompts = config.get('scoring_sys', {})
    # 鏇挎崲 prompts 涓殑 <memory_owner_name> 鍗犱綅绗?
    if isinstance(prompts, dict):
        for key, value in prompts.items():
            if isinstance(value, str):
                prompts[key] = value.replace('<memory_owner_name>', memory_owner_name)
            elif isinstance(value, dict):
                # 澶勭悊宓屽瀛楀吀锛堜緥濡?scoring_sys 涓殑鍚勪釜璇勫垎缁村害锛?
                for sub_key, sub_value in value.items():
                    if isinstance(sub_value, str):
                        value[sub_key] = sub_value.replace('<memory_owner_name>', memory_owner_name)
    return prompts

def ensure_directory(path: Path):
    """Ensure a directory exists."""
    path.mkdir(parents=True, exist_ok=True)

def scan_episode_files(episodes_root: Path = None) -> List[Path]:
    """
    Scan all episode files and return their paths.

    Args:
        episodes_root: Optional episodes root override.
    """
    if episodes_root is None:
        episodes_root = EPISODES_ROOT
    
    episode_files = []
    # 鎵弿 by_dialogue 鐩綍
    by_dialogue_dir = episodes_root / "by_dialogue"
    if not by_dialogue_dir.exists():
        return episode_files
    
    for dialogue_dir in by_dialogue_dir.iterdir():
        if dialogue_dir.is_dir():
            episode_file = dialogue_dir / "episodes_v1.json"
            if episode_file.exists():
                episode_files.append(episode_file)
    
    return episode_files

def get_qualification_path(episode_file: Path) -> Path:
    """
    Build the qualification file path for an episode file.

    Layout:
        episodes/by_dialogue/{dialogue_id}/qualifications_v1.json
    """
    dialogue_dir = episode_file.parent
    return dialogue_dir / "qualifications_v1.json"

def episode_needs_qualification(episode_file: Path) -> bool:
    """Return whether a qualification file still needs to be generated."""
    qualification_file = get_qualification_path(episode_file)
    return not qualification_file.exists()

def load_episodes(episode_file: Path) -> Dict:
    """鍔犺浇 episode JSON 鏂囦欢"""
    with open(episode_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def find_dialogue_file(dialogue_id: str, dialogues_root: Path = None) -> Optional[Path]:
    """
    鏍规嵁 dialogue_id 鏌ユ壘瀵瑰簲鐨勫璇濇枃浠躲€?
    鎼滅储 dialogues 鐩綍涓嬬殑鎵€鏈夊瓙鐩綍銆?
    
    Args:
        dialogue_id: 瀵硅瘽ID
        dialogues_root: 瀵硅瘽鏍圭洰褰曪紝濡傛灉涓篘one鍒欎娇鐢ㄩ粯璁ょ殑DIALOGUES_ROOT
    """
    if dialogues_root is None:
        dialogues_root = DIALOGUES_ROOT
    
    # 鎼滅储鎵€鏈夊彲鑳界殑鐩綍缁撴瀯
    search_patterns = [
        ("by_user", "*", "*"),      # by_user/{user_id}/{year-month}/
        ("by_flipflop", "*", "*"),  # by_flipflop/{flipflop_id}/{year-month}/
        ("", "*", "*"),             # 鐩存帴鎼滅储鏍圭洰褰曚笅鐨?{user_id}/{year-month}/
    ]
    
    for base_dir, user_pattern, date_pattern in search_patterns:
        search_dir = dialogues_root / base_dir
        if not search_dir.exists():
            continue
            
        # 閬嶅巻鐢ㄦ埛鐩綍
        for user_dir in search_dir.iterdir():
            if user_dir.is_dir():
                # 閬嶅巻骞存湀鐩綍
                for year_month_dir in user_dir.iterdir():
                    if year_month_dir.is_dir():
                        dialogue_file = year_month_dir / f"{dialogue_id}.json"
                        if dialogue_file.exists():
                            return dialogue_file
    
    # 濡傛灉娌℃湁鎵惧埌锛屽皾璇曢€掑綊鎼滅储鏁翠釜鐩綍
    for file_path in dialogues_root.rglob(f"{dialogue_id}.json"):
        if file_path.is_file():
            return file_path
    
    return None

def load_dialogue(dialogue_file: Path) -> Dict:
    """鍔犺浇瀵硅瘽 JSON 鏂囦欢"""
    with open(dialogue_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def build_episode_with_content(episode_meta: Dict, dialogue_data: Dict) -> Dict:
    """
    鏍规嵁 episode 鍏冩暟鎹拰瀵硅瘽鏁版嵁鏋勫缓瀹屾暣鐨?episode 鍐呭銆?
    
    Args:
        episode_meta: episode 鍏冩暟鎹紙鏉ヨ嚜 episodes_v1.json锛?
        dialogue_data: 瀹屾暣鐨勫璇濇暟鎹?
        
    Returns:
        鍖呭惈瀹屾暣鍐呭鐨?episode 瀛楀吀
    """
    # 鎻愬彇 turn_span
    turn_span = episode_meta.get('turn_span', [0, 0])
    start_id, end_id = turn_span[0], turn_span[1]
    
    # 鎻愬彇瀵瑰簲鐨勫璇濊疆娆?
    turns = dialogue_data.get('turns', [])
    episode_turns = []
    
    for turn in turns:
        turn_id = turn.get('turn_id', -1)
        if start_id <= turn_id <= end_id:
            episode_turns.append(turn)
    
    # 鏋勫缓瀹屾暣鐨?episode 缁撴瀯
    episode_with_content = episode_meta.copy()
    episode_with_content['turns'] = episode_turns
    episode_with_content['dialogue_content'] = {
        'dialogue_id': dialogue_data.get('dialogue_id', ''),
        'user_id': dialogue_data.get('user_id', ''),
        'participants': dialogue_data.get('participants', []),
        'meta': dialogue_data.get('meta', {})
    }
    
    return episode_with_content

def call_openai_for_scoring(
    episode_with_content: Dict,
    system_prompt: str,
    user_prompt_template: str,
    llm_model: Optional[Callable[[str], str]] = None
) -> Dict:
    """
    璋冪敤 OpenAI 杩涜鍗曚釜璇勫垎缁村害鐨勮瘎鍒嗐€?
    杩斿洖瑙ｆ瀽鍚庣殑 JSON 缁撴灉銆?
    """
    if llm_model is None:
        from m_agent.load_model.OpenAIcall import get_llm
        llm_model = get_llm(model_temperature=0.1)
    
    # 鑾峰彇 LLM 瀹炰緥锛屾俯搴﹁涓?0.1 浠ヨ幏寰楁洿纭畾鎬х殑杈撳嚭
    
    # 灏?episode JSON 杞崲涓哄瓧绗︿覆鐢ㄤ簬鎻掑叆
    episode_str = json.dumps(episode_with_content, ensure_ascii=False, indent=2)
    user_prompt = user_prompt_template.replace('<EPISODE_JSON>', episode_str)
    
    # 缁勫悎 system 鍜?user prompt锛堥€傜敤浜?text completion 妯″瀷锛?
    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    
    try:
        response_text = llm_model(full_prompt)
        # 瑙ｆ瀽 JSON 鍝嶅簲
        # 鍝嶅簲鍙兘鍖呭惈棰濆鐨勬枃鏈紝灏濊瘯鎻愬彇 JSON 閮ㄥ垎
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            response_text = json_match.group(0)
        
        result = json.loads(response_text)
        return result
    except json.JSONDecodeError as e:
        logger.error(f"瑙ｆ瀽 OpenAI 鍝嶅簲澶辫触: {e}")
        logger.error(f"鍝嶅簲鏂囨湰: {response_text[:500]}...")
        raise
    except Exception as e:
        logger.error(f"璋冪敤 OpenAI 澶辫触: {e}")
        raise


def call_openai_for_qualification(
    episode_with_content: Dict,
    scoring_sys: Dict,
    llm_model: Optional[Callable[[str], str]] = None
) -> Dict:
    """
    璋冪敤 OpenAI 杩涜澶氫釜璇勫垎缁村害鐨勮瘎鍒嗐€?
    渚濇鎸夌収 scoring_sys 涓殑鍐呭鍒嗗埆鎵撳垎锛岃繑鍥炴墍鏈夎瘎鍒嗙粨鏋滅殑瀛楀吀銆?
    """
    scoring_results = {}
    
    for scoring_name, scoring_config in scoring_sys.items():
        score_name = scoring_config.get('score_name', scoring_name)
        system_prompt = scoring_config.get('system_prompt', '')
        user_prompt = scoring_config.get('user_prompt', '')
        
        if not system_prompt or not user_prompt:
            logger.warning(f"Skipping scoring module {scoring_name}: missing prompt text")
            continue
        
        try:
            result = call_openai_for_scoring(
                episode_with_content,
                system_prompt,
                user_prompt,
                llm_model=llm_model
            )
            scoring_results[score_name] = result
        except Exception as e:
            logger.error(f"璇勫垎妯″潡 {scoring_name} 澶辫触: {e}")
            # 缁х画澶勭悊鍏朵粬璇勫垎妯″潡
            continue
    
    return scoring_results

def build_qualification_structure(dialogue_id: str, episode_id: str, scoring_results: Dict) -> Dict:
    """
    鏋勫缓鏈€缁堢殑 qualification 缁撴瀯锛岀鍚堣姹傜殑鏍煎紡銆?
    鍙繚鐣?scoring_sys 涓畾涔夌殑璇勫垎缁村害锛屼笉娣诲姞棰濆瀛楁銆?
    
    Args:
        dialogue_id: 瀵硅瘽ID
        episode_id: 鐗囨ID
        scoring_results: 瀛楀吀锛岄敭涓?score_name锛屽€间负璇勫垎妯″潡鐨勫師濮嬭緭鍑?
    """
    scene_potential_score = {}
    rationale = {}
    
    # 閬嶅巻鎵€鏈夎瘎鍒嗙粨鏋滐紝鎻愬彇鍒嗘暟鍜岀悊鐢?
    for score_name, result in scoring_results.items():
        # 鏍规嵁鐢ㄦ埛瑕佹眰锛屽瓧娈靛悕浣跨敤 score_name + "_novelty"
        expected_field = f"{score_name}_novelty"
        score_value = None
        
        # 灏濊瘯浠庨鏈熷瓧娈佃幏鍙栧垎鏁?
        if expected_field in result and isinstance(result[expected_field], int):
            score_value = result[expected_field]
            scene_potential_score[expected_field] = score_value
            rationale[expected_field] = result.get("rationale", "No rationale provided")
        else:
            # 濡傛灉棰勬湡瀛楁涓嶅瓨鍦紝灏濊瘯鏌ユ壘鍏朵粬鍒嗘暟瀛楁
            for key, value in result.items():
                if isinstance(value, int) and (key.endswith("_novelty") or key.endswith("_score")):
                    score_value = value
                    scene_potential_score[key] = score_value
                    rationale[key] = result.get("rationale", "No rationale provided")
                    break
            if score_value is None:
                # 濡傛灉娌℃湁鎵惧埌鍒嗘暟瀛楁锛岃烦杩囪璇勫垎妯″潡
                logger.warning(f"璇勫垎妯″潡 {score_name} 鏈壘鍒板垎鏁板瓧娈碉紝璺宠繃")
    
    # 濡傛灉娌℃湁浠讳綍璇勫垎缁撴灉锛屽垱寤虹┖缁撴瀯
    if not scene_potential_score:
        scene_potential_score = {}
        rationale = {}
    
    # 鍐崇瓥瀛楁鏆傛椂璁句负 pending锛堜笅娓哥▼搴忎細鍔ㄦ€佹娴嬶級
    decision = "pending"
    
    return {
        "episode_id": episode_id,
        "dialogue_id": dialogue_id,
        "qualification_version": "v1",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "scene_potential_score": scene_potential_score,
        "decision": decision,
        "rationale": rationale
    }

def process_episode_file(
    episode_file: Path,
    prompts: Dict,
    dialogues_root: Path = None,
    memory_owner_name: str = "changshengEVA",
    llm_model: Optional[Callable[[str], str]] = None
) -> bool:
    """澶勭悊鍗曚釜 episode 鏂囦欢锛岀敓鎴?qualification"""
    try:
        # 鍔犺浇 episodes
        episode_data = load_episodes(episode_file)
        dialogue_id = episode_data.get('dialogue_id', episode_file.parent.name)
        
        # 鏌ユ壘骞跺姞杞藉搴旂殑瀵硅瘽鏂囦欢
        dialogue_file = find_dialogue_file(dialogue_id, dialogues_root)
        if not dialogue_file:
            logger.error(f"鎵句笉鍒板璇濇枃浠? {dialogue_id}")
            return False
        
        dialogue_data = load_dialogue(dialogue_file)
        
        # 鑾峰彇鎵€鏈?episodes
        episodes = episode_data.get('episodes', [])
        if not episodes:
            logger.warning(f"Skipping dialogue {dialogue_id}: no episodes found")
            return False
        
        # 涓烘瘡涓?episode 鐢熸垚 qualification
        qualifications = []
        for episode in episodes:
            episode_id = episode.get('episode_id', 'unknown')
            
            # 鏋勫缓鍖呭惈瀹屾暣鍐呭鐨?episode
            episode_with_content = build_episode_with_content(episode, dialogue_data)
            
            # 璋冪敤 OpenAI 杩涜 qualification
            openai_result = call_openai_for_qualification(
                episode_with_content,
                prompts,
                llm_model=llm_model
            )
            
            # 鏋勫缓鏈€缁堢粨鏋?
            qualification = build_qualification_structure(dialogue_id, episode_id, openai_result)
            qualifications.append(qualification)
        
        # 淇濆瓨鏂囦欢
        qualification_file = get_qualification_path(episode_file)
        save_qualifications(qualifications, qualification_file)
        
        return True
    except Exception as e:
        logger.error(f"澶勭悊 episode 鏂囦欢 {episode_file} 澶辫触: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def save_qualifications(qualifications: List[Dict], qualification_file: Path):
    """Save qualification results to disk."""
    ensure_directory(qualification_file.parent)
    with open(qualification_file, 'w', encoding='utf-8') as f:
        json.dump({
            "qualifications": qualifications,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "qualification_version": "v1"
        }, f, ensure_ascii=False, indent=2)

def scan_and_qualify_episodes(
    use_tqdm: bool = True,
    dialogues_root: Path = None,
    episodes_root: Path = None,
    memory_owner_name: str = "changshengEVA",
    llm_model: Optional[Callable[[str], str]] = None
):
    """
    Scan episode files and generate qualification outputs when needed.

    Args:
        use_tqdm: Whether to render a progress bar.
        dialogues_root: Optional dialogue root override.
        episodes_root: Optional episodes root override.
        memory_owner_name: Placeholder value injected into prompt templates.
    """
    # 纭畾浣跨敤鐨勬牴鐩綍
    if episodes_root is None:
        episodes_root = EPISODES_ROOT
    if dialogues_root is None:
        dialogues_root = DIALOGUES_ROOT
    
    # 纭繚 episodes 鏍圭洰褰曞瓨鍦?
    ensure_directory(episodes_root)
    
    # 鍔犺浇 prompts锛屽苟鏇挎崲鍗犱綅绗?
    prompts = load_prompts(memory_owner_name)
    if not prompts:
        logger.error("鏈壘鍒?episode_information_scoring prompts")
        return
    
    # 鎵弿鎵€鏈?episode 鏂囦欢
    episode_files = scan_episode_files(episodes_root)
    
    # 杩囨护闇€瑕佸鐞嗙殑鏂囦欢
    files_to_process = []
    for file in episode_files:
        if episode_needs_qualification(file):
            files_to_process.append(file)
    
    if not files_to_process:
        # 娌℃湁闇€瑕佸鐞嗙殑鏂囦欢锛岄潤榛橀€€鍑?
        logger.info("娌℃湁闇€瑕佸鐞嗙殑 episode 鏂囦欢")
        return
    
    # 澶勭悊鏂囦欢
    if use_tqdm:
        file_iter = tqdm(files_to_process, desc="璇勪及 episodes")
    else:
        file_iter = files_to_process
    
    success_count = 0
    for episode_file in file_iter:
        if process_episode_file(
            episode_file,
            prompts,
            dialogues_root,
            memory_owner_name,
            llm_model=llm_model
        ):
            success_count += 1
    
    logger.info(f"鎴愬姛澶勭悊 {success_count}/{len(files_to_process)} 涓?episode 鏂囦欢")

def clear_all_qualifications(confirm: bool = False):
    """
    Remove generated qualification files.

    Args:
        confirm: When False, show a preview only.
    """
    # 鎵弿鎵€鏈?episode 鏂囦欢
    episode_files = scan_episode_files()
    
    qualification_files = []
    for episode_file in episode_files:
        qual_file = get_qualification_path(episode_file)
        if qual_file.exists():
            qualification_files.append(qual_file)
    
    if not qualification_files:
        print("娌℃湁鎵惧埌 qualifications_v1.json 鏂囦欢")
        return
    
    print(f"鎵惧埌 {len(qualification_files)} 涓?qualifications_v1.json 鏂囦欢:")
    for qual_file in qualification_files:
        print(f"  - {qual_file}")
    
    if not confirm:
        print("\n杩欏彧鏄瑙堛€傝瀹為檯鍒犻櫎杩欎簺鏂囦欢锛岃杩愯: clear_all_qualifications(confirm=True)")
        return
    
    # 瀹為檯鍒犻櫎鏂囦欢
    deleted_count = 0
    for qual_file in qualification_files:
        try:
            qual_file.unlink()
            print(f"宸插垹闄? {qual_file}")
            deleted_count += 1
        except Exception as e:
            print(f"鍒犻櫎澶辫触 {qual_file}: {e}")
    
    print(f"\nDeleted {deleted_count}/{len(qualification_files)} files.")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Episode qualification utility")
    parser.add_argument("--scan", action="store_true", help="scan and generate qualification files")
    parser.add_argument("--clear", action="store_true", help="remove generated qualification files")
    parser.add_argument("--confirm", action="store_true", help="confirm deletion when used with --clear")
    
    args = parser.parse_args()
    
    if args.clear:
        clear_all_qualifications(confirm=args.confirm)
    elif args.scan:
        scan_and_qualify_episodes()
    else:
        # 榛樿琛屼负锛氭壂鎻忓苟璇勪及
        scan_and_qualify_episodes()


