#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
test_generate_action.py

目标：
1) 从 scene.source 定位原始对话并还原为正常对话文本
2) 使用 config/prompt/action_extraction.yaml 中的 prompt 提取 action
3) 解析返回字段，并结合 scene/source 与阿里云 embedding 补全为统一结构

输出结构：
{
  "actor": "...",
  "action": "...",
  "evidence": {
    "episode_id": "...",
    "dialogue_id": "..."
  },
  "embedding": [...float]
}
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # type: ignore
except Exception:
    yaml = None

# 允许直接运行该脚本
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SCENE_PATH = PROJECT_ROOT / "data" / "memory" / "testlocomo" / "scene" / "00076.json"
DIALOGUES_ROOT = PROJECT_ROOT / "data" / "memory" / "testlocomo" / "dialogues"
PROMPT_PATH = PROJECT_ROOT / "config" / "prompt" / "action_extraction.yaml"
PROMPT_KEY = "action_extractio_v1"


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be dict: {path}")
    return data


def load_prompt(path: Path, key: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    if yaml is not None:
        data = yaml.safe_load(raw) or {}
        prompt = data.get(key, "")
        if isinstance(prompt, str) and prompt.strip():
            return prompt

    # 兜底：简易 YAML 解析（仅支持 key: | 的多行文本）
    lines = raw.splitlines()
    key_prefix = f"{key}:"
    start_idx = -1
    for i, line in enumerate(lines):
        if line.strip().startswith(key_prefix):
            start_idx = i
            break
    if start_idx < 0:
        raise ValueError(f"Prompt '{key}' not found in {path}")

    # 收集后续缩进块
    block: List[str] = []
    for line in lines[start_idx + 1:]:
        if not line.strip():
            block.append("")
            continue
        if line.startswith("  ") or line.startswith("\t"):
            block.append(line[2:] if line.startswith("  ") else line.lstrip("\t"))
        else:
            break
    prompt = "\n".join(block).strip()
    if not prompt:
        raise ValueError(f"Prompt '{key}' is empty in {path}")
    return prompt


def find_dialogue_file(dialogues_root: Path, dialogue_id: str) -> Optional[Path]:
    direct = dialogues_root / f"{dialogue_id}.json"
    if direct.exists():
        return direct
    for p in dialogues_root.rglob(f"{dialogue_id}.json"):
        if p.is_file():
            return p
    return None


def extract_turns(dialogue_data: Dict[str, Any], turn_span: List[int]) -> List[Dict[str, Any]]:
    turns = dialogue_data.get("turns", [])
    if not isinstance(turns, list):
        return []
    if isinstance(turn_span, list) and len(turn_span) == 2 and all(isinstance(x, int) for x in turn_span):
        start_id, end_id = turn_span
        selected: List[Dict[str, Any]] = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            tid = turn.get("turn_id")
            if isinstance(tid, int) and start_id <= tid <= end_id:
                selected.append(turn)
        return selected
    return [t for t in turns if isinstance(t, dict)]


def normalize_line(speaker: str, text: str) -> str:
    speaker = (speaker or "Unknown").strip()
    text = (text or "").strip()
    if not text:
        return f"{speaker}:"
    # 去掉 text 中重复的说话人前缀
    for prefix in (f"{speaker}:", f"{speaker}："):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return f"{speaker}: {text}"


def turns_to_dialogue_block(turns: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for t in turns:
        lines.append(normalize_line(str(t.get("speaker", "Unknown")), str(t.get("text", ""))))
    return "\n".join(lines)


def extract_json_from_text(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return []

    # 兼容 ```json ... ```
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    # 优先数组
    arr = re.search(r"\[[\s\S]*\]", text)
    if arr:
        return json.loads(arr.group(0))

    obj = re.search(r"\{[\s\S]*\}", text)
    if obj:
        return [json.loads(obj.group(0))]

    raise ValueError("No JSON payload found in LLM response")


def call_action_extraction(dialogue_block: str, prompt_template: str) -> List[Dict[str, Any]]:
    full_prompt = prompt_template.replace("{dialogue_block}", dialogue_block)
    llm = build_llm_func(model_temperature=0.1)
    response = llm(full_prompt)
    parsed = extract_json_from_text(response)
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        raise ValueError("Action extraction result is not a JSON list")
    return [x for x in parsed if isinstance(x, dict)]


def build_llm_func(model_temperature: float = 0.1):
    try:
        from load_model.OpenAIcall import get_llm

        return get_llm(model_temperature=model_temperature)
    except Exception as wrapper_exc:
        logger.warning("OpenAI wrapper unavailable, fallback to direct OpenAI client: %s", wrapper_exc)

    import openai

    api_key = (os.getenv("API_SECRET_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Missing API_SECRET_KEY/OPENAI_API_KEY for action extraction")
    base_url = (os.getenv("BASE_URL") or "https://api.openai.com/v1").strip()
    model = (os.getenv("OPENAI_CHAT_MODEL") or "gpt-4.1").strip()
    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    def _llm(prompt: str) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=model_temperature,
            max_tokens=1500,
        )
        return (resp.choices[0].message.content or "").strip()

    return _llm


def build_alibaba_embed_func():
    try:
        from load_model.AlibabaEmbeddingCall import get_embed_model

        return get_embed_model()
    except Exception as wrapper_exc:
        logger.warning("Alibaba wrapper unavailable, fallback to direct DashScope client: %s", wrapper_exc)

    import openai

    api_key = os.getenv("ALIBABA_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing ALIBABA_API_KEY for Alibaba embedding")

    base_url = (
        os.getenv("ALIBABA_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    model = (os.getenv("ALIBABA_EMBED_MODEL") or "text-embedding-v4").strip()
    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    def _embed(text: str) -> List[float]:
        cleaned = (text or "").strip()
        if not cleaned:
            return []
        resp = client.embeddings.create(model=model, input=cleaned)
        return resp.data[0].embedding

    return _embed


def fallback_extract_actions(turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keywords = ("perform", "festival", "rehears", "choreograph", "practice")
    results: List[Dict[str, Any]] = []
    for t in turns:
        speaker = str(t.get("speaker", "")).strip()
        text = str(t.get("text", "")).strip()
        lower = text.lower()
        if not text:
            continue
        if any(k in lower for k in keywords):
            action = text
            # 尽量抽出句子级 evidence
            sentences = re.split(r"(?<=[.!?])\s+", text)
            if sentences:
                for sent in sentences:
                    if any(k in sent.lower() for k in keywords):
                        action = sent.strip()
                        break
            results.append(
                {
                    "actor": speaker,
                    "action": action,
                    "evidence_sentence": action,
                }
            )
    if results:
        return results

    if turns:
        t0 = turns[0]
        text = str(t0.get("text", "")).strip()
        return [
            {
                "actor": str(t0.get("speaker", "")).strip(),
                "action": text,
                "evidence_sentence": text,
            }
        ]
    return []


def infer_actor_from_evidence(evidence_sentence: str, turns: List[Dict[str, Any]], participants: List[str]) -> str:
    evidence = evidence_sentence.strip().lower()
    if evidence:
        for t in turns:
            speaker = str(t.get("speaker", "")).strip()
            text = str(t.get("text", "")).strip().lower()
            if evidence in text and speaker:
                return speaker
    return participants[0] if participants else ""


def normalize_actor(actor: str, participants: List[str]) -> str:
    actor = actor.strip()
    if not actor:
        return actor
    mapping = {p.lower(): p for p in participants}
    return mapping.get(actor.lower(), actor)


def score_action_item(item: Dict[str, Any]) -> int:
    action = str(item.get("action", "")).lower()
    score = 0
    if "festival" in action:
        score += 5
    if "perform" in action:
        score += 3
    if "will" in action or "next" in action:
        score += 1
    return score


def complete_action_item(
    raw_item: Dict[str, Any],
    source_ep: Dict[str, Any],
    turns: List[Dict[str, Any]],
    participants: List[str],
    embed_model,
) -> Dict[str, Any]:
    evidence_sentence = str(raw_item.get("evidence_sentence", "")).strip()
    actor = str(raw_item.get("actor", "")).strip()
    action = str(raw_item.get("action", "")).strip()

    if not actor:
        actor = infer_actor_from_evidence(evidence_sentence, turns, participants)
    actor = normalize_actor(actor, participants)

    if not action:
        action = evidence_sentence
    if not action:
        action = "unknown_action"

    embedding: List[float] = []
    try:
        vec = embed_model(action)
        if isinstance(vec, list):
            embedding = vec
    except Exception as exc:
        logger.warning("Embedding generation failed, fallback to empty list: %s", exc)

    return {
        "actor": actor,
        "action": action,
        "evidence": {
            "episode_id": str(source_ep.get("episode_id", "")),
            "dialogue_id": str(source_ep.get("dialogue_id", "")),
        },
        #"embedding": embedding,
    }


def main() -> None:
    scene = load_json(SCENE_PATH)
    prompt_template = load_prompt(PROMPT_PATH, PROMPT_KEY)

    source = scene.get("source", {})
    episodes = source.get("episodes", []) if isinstance(source, dict) else []
    if not episodes:
        raise ValueError("scene.source.episodes is empty")

    source_ep = episodes[0]
    dialogue_id = str(source_ep.get("dialogue_id", "")).strip()
    turn_span = source_ep.get("turn_span", [])
    if not dialogue_id:
        raise ValueError("scene.source.episodes[0].dialogue_id is empty")

    dialogue_file = find_dialogue_file(DIALOGUES_ROOT, dialogue_id)
    if dialogue_file is None:
        raise FileNotFoundError(f"Dialogue file not found for {dialogue_id}")

    dialogue_data = load_json(dialogue_file)
    participants = dialogue_data.get("participants", [])
    if not isinstance(participants, list):
        participants = []
    participants = [str(p) for p in participants]

    turns = extract_turns(dialogue_data, turn_span if isinstance(turn_span, list) else [])
    dialogue_block = turns_to_dialogue_block(turns)
    logger.info("Dialogue block generated from source:\n%s", dialogue_block)

    try:
        raw_actions = call_action_extraction(dialogue_block, prompt_template)
    except Exception as exc:
        logger.warning("LLM extraction failed, fallback to rule-based extraction: %s", exc)
        raw_actions = fallback_extract_actions(turns)

    if not raw_actions:
        raise ValueError("No action extracted from dialogue")

    try:
        embed_model = build_alibaba_embed_func()
    except Exception as exc:
        logger.warning("Alibaba embedding model init failed, use empty embedding fallback: %s", exc)
        embed_model = lambda _text: []
    completed = [
        complete_action_item(
            raw_item=item,
            source_ep=source_ep,
            turns=turns,
            participants=participants,
            embed_model=embed_model,
        )
        for item in raw_actions
    ]

    # 选最核心 action（优先 festival/perform）
    final_action = sorted(completed, key=score_action_item, reverse=True)[0]
    output = {
        "all_actions": completed,
        "top_action": final_action,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
