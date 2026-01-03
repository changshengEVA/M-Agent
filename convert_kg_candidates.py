#!/usr/bin/env python3
"""
将 kg_candidates 数据转换为统一的 KG 数据格式
输入: data/memory/kg_candidates/strong/*.kg_candidate.json
输出: data/memory/kg_data/kg_data.json
"""

import json
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Set, Any

def load_kg_candidates(input_dir: str) -> Dict[str, Any]:
    """
    加载所有 kg_candidate.json 文件，聚合实体、关系和场景
    """
    input_path = Path(input_dir)
    if not input_path.exists():
        raise FileNotFoundError(f"输入目录不存在: {input_dir}")
    
    entities: Dict[str, Dict] = {}  # id -> entity dict
    relations: List[Dict] = []
    scenes: Dict[str, Dict] = {}
    
    # 遍历所有 JSON 文件
    json_files = list(input_path.glob("*.kg_candidate.json"))
    print(f"找到 {len(json_files)} 个 KG 候选文件")
    
    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            print(f"警告: 无法读取文件 {file_path}: {e}")
            continue
        
        scene_id = data.get("scene_id", file_path.stem)
        user_id = data.get("user_id", "unknown")
        generated_at = data.get("generated_at", "")
        prompt_version = data.get("prompt_version", "")
        
        # 保存场景信息
        scenes[scene_id] = {
            "scene_id": scene_id,
            "user_id": user_id,
            "generated_at": generated_at,
            "prompt_version": prompt_version
        }
        
        # 处理实体
        facts = data.get("facts", {})
        for entity_data in facts.get("entities", []):
            entity_id = entity_data.get("id")
            if not entity_id:
                continue
            
            entity_type = entity_data.get("type", "unknown")
            confidence = entity_data.get("confidence", 0.0)
            
            if entity_id in entities:
                # 更新现有实体
                existing = entities[entity_id]
                # 添加场景到列表（如果不存在）
                if scene_id not in existing["scenes"]:
                    existing["scenes"].append(scene_id)
                # 更新置信度为最高值
                if confidence > existing["confidence"]:
                    existing["confidence"] = confidence
                # 类型冲突时保留原有类型（或合并？这里简单保留第一个）
            else:
                # 创建新实体
                entities[entity_id] = {
                    "id": entity_id,
                    "type": entity_type,
                    "confidence": confidence,
                    "scenes": [scene_id]
                }
        
        # 处理关系
        for rel_data in facts.get("relations", []):
            subject = rel_data.get("subject")
            relation = rel_data.get("relation")
            obj = rel_data.get("object")
            confidence = rel_data.get("confidence", 0.0)
            
            if subject and relation and obj:
                relations.append({
                    "subject": subject,
                    "relation": relation,
                    "object": obj,
                    "confidence": confidence,
                    "scene_id": scene_id
                })
    
    return {
        "entities": list(entities.values()),
        "relations": relations,
        "scenes": list(scenes.values())
    }

def save_kg_data(output_path: str, kg_data: Dict[str, Any]):
    """
    保存 KG 数据到 JSON 文件
    """
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 添加元数据
    result = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "source_dir": "data/memory/kg_candidates/strong",
            "total_scenes": len(kg_data["scenes"]),
            "total_entities": len(kg_data["entities"]),
            "total_relations": len(kg_data["relations"])
        },
        "entities": kg_data["entities"],
        "relations": kg_data["relations"],
        "scenes": kg_data["scenes"]
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"KG 数据已保存到: {output_path}")
    print(f"统计: {len(kg_data['scenes'])} 个场景, {len(kg_data['entities'])} 个实体, {len(kg_data['relations'])} 个关系")

def main():
    # 路径配置
    project_root = Path(__file__).parent
    input_dir = project_root / "data" / "memory" / "kg_candidates" / "strong"
    output_dir = project_root / "data" / "memory" / "kg_data"
    output_file = output_dir / "kg_data.json"
    
    print("开始转换 KG 候选数据...")
    print(f"输入目录: {input_dir}")
    print(f"输出文件: {output_file}")
    
    try:
        kg_data = load_kg_candidates(str(input_dir))
        save_kg_data(str(output_file), kg_data)
        print("转换完成！")
    except Exception as e:
        print(f"转换失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()