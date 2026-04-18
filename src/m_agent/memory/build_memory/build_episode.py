#!/usr/bin/env python3
"""
Build memory episodes (with segments) from raw dialogues.

Single-pass two-level segmentation:
  Dialogue → Episodes → Segments

The script scans turns in order and maintains both an episode buffer and a
segment buffer.  For each candidate turn the LLM makes a three-way decision:
  - append:        keep in current segment (and episode)
  - segment_break: finalise the current segment, start a new one in the same episode
  - episode_break: finalise both the current segment and episode, start fresh
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml
from tqdm import tqdm

from m_agent.config_paths import EPISODE_PROMPT_CONFIG_PATH
from m_agent.paths import memory_stage_dir
from m_agent.prompt_utils import load_resolved_prompt_config, normalize_prompt_language


logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

DIALOGUES_ROOT = memory_stage_dir("default", "dialogues")
EPISODES_ROOT = memory_stage_dir("default", "episodes")
CONFIG_PATH = EPISODE_PROMPT_CONFIG_PATH


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

def _replace_prompt_placeholders(value: Any, memory_owner_name: str) -> Any:
    if isinstance(value, str):
        return value.replace("<memory_owner_name>", memory_owner_name)
    if isinstance(value, dict):
        return {
            key: _replace_prompt_placeholders(sub_value, memory_owner_name)
            for key, sub_value in value.items()
        }
    if isinstance(value, list):
        return [_replace_prompt_placeholders(item, memory_owner_name) for item in value]
    return value


def load_prompts(memory_owner_name: str = "changshengEVA", prompt_language: str = "en") -> Dict[str, Any]:
    """Load dialogue segmentation prompts from the episode prompt config."""
    config = load_resolved_prompt_config(
        CONFIG_PATH,
        language=normalize_prompt_language(prompt_language),
    )

    prompts = config.get("dialogue_segmentation", {})
    if not isinstance(prompts, dict):
        return {}

    return _replace_prompt_placeholders(prompts, memory_owner_name)


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def ensure_directory(path: Path) -> None:
    """Ensure a directory exists."""
    path.mkdir(parents=True, exist_ok=True)


def scan_dialogue_files(dialogues_root: Optional[Path] = None) -> List[Path]:
    """
    Scan all dialogue JSON files.

    Supported layouts:
    1. {dialogues_root}/{year-month}/{dialogue_id}.json
    2. {dialogues_root}/{user_id}/{year-month}/{dialogue_id}.json
    3. {dialogues_root}/by_user/{user_id}/{year-month}/{dialogue_id}.json
    """
    if dialogues_root is None:
        dialogues_root = DIALOGUES_ROOT

    dialogue_files: List[Path] = []
    year_month_pattern = re.compile(r"^\d{4}-\d{2}$")

    for dir_path in dialogues_root.rglob("*"):
        if dir_path.is_dir() and year_month_pattern.match(dir_path.name):
            dialogue_files.extend(dir_path.glob("*.json"))

    return sorted(set(dialogue_files))


def get_episode_path(dialogue_file: Path, episodes_root: Optional[Path] = None) -> Path:
    """
    Build the target episode file path for a dialogue.

    Format: episodes/by_dialogue/{dialogue_id}/episodes_v1.json
    """
    if episodes_root is None:
        episodes_root = EPISODES_ROOT

    dialogue_id = dialogue_file.stem
    episode_dir = episodes_root / "by_dialogue" / dialogue_id
    return episode_dir / "episodes_v1.json"


def dialogue_needs_episodes(dialogue_file: Path, episodes_root: Optional[Path] = None) -> bool:
    """Return True when the dialogue still needs an episodes_v1.json file."""
    episode_file = get_episode_path(dialogue_file, episodes_root)
    return not episode_file.exists()


def load_dialogue(dialogue_file: Path) -> Dict[str, Any]:
    """Load one dialogue JSON file."""
    with open(dialogue_file, "r", encoding="utf-8") as file:
        return json.load(file)


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _get_llm_model(llm_model: Optional[Callable[[str], str]] = None) -> Callable[[str], str]:
    if llm_model is not None:
        return llm_model

    from m_agent.load_model.OpenAIcall import get_llm

    return get_llm(model_temperature=0.1)


def _parse_json_response(response_text: str) -> Dict[str, Any]:
    text = response_text.strip()
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if json_match:
        text = json_match.group(0)

    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response is not a JSON object.")
    return parsed


def _render_prompt(system_prompt: str, user_prompt_template: str, replacements: Dict[str, str]) -> str:
    user_prompt = user_prompt_template
    for key, value in replacements.items():
        user_prompt = user_prompt.replace(key, value)
    return f"{system_prompt}\n\n{user_prompt}".strip()


def _call_llm_json(
    *,
    system_prompt: str,
    user_prompt_template: str,
    replacements: Dict[str, str],
    llm_model: Callable[[str], str],
) -> Dict[str, Any]:
    prompt = _render_prompt(system_prompt, user_prompt_template, replacements)
    response_text = llm_model(prompt)
    return _parse_json_response(response_text)


def _serialize_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Turn normalisation
# ---------------------------------------------------------------------------

def _normalize_turns(dialogue_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_turns = dialogue_json.get("turns", [])
    if not isinstance(raw_turns, list):
        return []

    sortable_turns: List[Tuple[int, int, Dict[str, Any]]] = []
    for original_index, raw_turn in enumerate(raw_turns):
        if not isinstance(raw_turn, dict):
            continue

        turn = dict(raw_turn)
        if "turn_id" not in turn:
            turn["turn_id"] = original_index

        raw_turn_id = turn.get("turn_id")
        try:
            sort_turn_id = int(raw_turn_id)
        except (TypeError, ValueError):
            sort_turn_id = original_index

        sortable_turns.append((sort_turn_id, original_index, turn))

    sortable_turns.sort(key=lambda item: (item[0], item[1]))
    return [turn for _, _, turn in sortable_turns]


def _build_dialogue_context(dialogue_json: Dict[str, Any], turns: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "dialogue_id": dialogue_json.get("dialogue_id", ""),
        "user_id": dialogue_json.get("user_id", ""),
        "participants": dialogue_json.get("participants", []),
        "meta": dialogue_json.get("meta", {}),
        "turn_count": len(turns),
    }


# ---------------------------------------------------------------------------
# Three-way decision logic
# ---------------------------------------------------------------------------

_APPEND_ALIASES = frozenset({
    "append",
    "continue",
    "continue_current_episode",
    "same_episode",
    "keep",
    "stay",
})

_SEGMENT_BREAK_ALIASES = frozenset({
    "segment_break",
    "new_segment",
    "start_new_segment",
    "sub_split",
    "segment",
    "topic_shift",
})

_EPISODE_BREAK_ALIASES = frozenset({
    "episode_break",
    "split",
    "new_episode",
    "start_new_episode",
    "start_new",
    "boundary",
    "close_and_start_new",
})


def _normalize_decision(raw_decision: str) -> str:
    decision = raw_decision.strip().lower()

    if decision in _APPEND_ALIASES:
        return "append"
    if decision in _SEGMENT_BREAK_ALIASES:
        return "segment_break"
    if decision in _EPISODE_BREAK_ALIASES:
        return "episode_break"
    raise ValueError(f"Unsupported segmentation decision: {raw_decision!r}")


def _decide_turn_transition(
    dialogue_context: Dict[str, Any],
    completed_segments: List[Dict[str, Any]],
    segment_buffer: List[Dict[str, Any]],
    candidate_turn: Dict[str, Any],
    prompts: Dict[str, Any],
    llm_model: Callable[[str], str],
) -> str:
    """Ask the LLM for a three-way decision: append / segment_break / episode_break."""
    system_prompt = str(prompts.get("system_prompt", ""))
    decision_prompt = str(prompts.get("decision_prompt", ""))
    if not decision_prompt.strip():
        raise ValueError("dialogue_segmentation.decision_prompt is missing.")

    segments_summary = [
        {
            "segment_id": seg.get("segment_id", ""),
            "topic": seg.get("topic", ""),
            "turn_span": seg.get("turn_span", []),
        }
        for seg in completed_segments
    ]

    result = _call_llm_json(
        system_prompt=system_prompt,
        user_prompt_template=decision_prompt,
        replacements={
            "<DIALOGUE_CONTEXT_JSON>": _serialize_json(dialogue_context),
            "<COMPLETED_SEGMENTS_JSON>": _serialize_json(segments_summary),
            "<CURRENT_SEGMENT_BUFFER_JSON>": _serialize_json(segment_buffer),
            "<CANDIDATE_TURN_JSON>": _serialize_json(candidate_turn),
        },
        llm_model=llm_model,
    )
    return _normalize_decision(str(result.get("decision", "")))


# ---------------------------------------------------------------------------
# Segment building
# ---------------------------------------------------------------------------

def _fallback_topic(buffer: List[Dict[str, Any]]) -> str:
    texts = [str(turn.get("text", "")).strip() for turn in buffer if str(turn.get("text", "")).strip()]
    if texts:
        snippet = " ".join(texts[0].split()[:8]).strip(" .,;:!?")
        if snippet:
            return snippet

    if buffer:
        return f"interaction around turn {buffer[0].get('turn_id', 0)}"
    return "empty interaction"


def _generate_segment_topic(
    dialogue_context: Dict[str, Any],
    segment_buffer: List[Dict[str, Any]],
    prompts: Dict[str, Any],
    llm_model: Callable[[str], str],
) -> str:
    system_prompt = str(prompts.get("system_prompt", ""))
    topic_prompt = str(prompts.get("segment_topic_prompt", "") or prompts.get("topic_prompt", ""))
    if not topic_prompt.strip():
        return _fallback_topic(segment_buffer)

    try:
        result = _call_llm_json(
            system_prompt=system_prompt,
            user_prompt_template=topic_prompt,
            replacements={
                "<DIALOGUE_CONTEXT_JSON>": _serialize_json(dialogue_context),
                "<SEGMENT_BUFFER_JSON>": _serialize_json(segment_buffer),
                # backward compat placeholder used by the shared topic_prompt
                "<EPISODE_BUFFER_JSON>": _serialize_json(segment_buffer),
            },
            llm_model=llm_model,
        )
        topic = str(result.get("topic", "")).strip()
        if topic:
            return topic
    except Exception as exc:
        logger.warning("Segment topic generation failed, use fallback: %s", exc)

    return _fallback_topic(segment_buffer)


def _build_segment_entry(
    segment_index: int,
    segment_buffer: List[Dict[str, Any]],
    dialogue_context: Dict[str, Any],
    prompts: Dict[str, Any],
    llm_model: Callable[[str], str],
) -> Dict[str, Any]:
    topic = _generate_segment_topic(
        dialogue_context=dialogue_context,
        segment_buffer=segment_buffer,
        prompts=prompts,
        llm_model=llm_model,
    )
    return {
        "segment_id": f"seg_{segment_index:03d}",
        "turn_span": [
            segment_buffer[0]["turn_id"],
            segment_buffer[-1]["turn_id"],
        ],
        "topic": topic,
    }


# ---------------------------------------------------------------------------
# Episode building
# ---------------------------------------------------------------------------

def _generate_episode_topic(
    dialogue_context: Dict[str, Any],
    segments: List[Dict[str, Any]],
    episode_buffer: List[Dict[str, Any]],
    prompts: Dict[str, Any],
    llm_model: Callable[[str], str],
) -> str:
    """Generate an episode topic from its segment summaries (preferred) or full buffer."""
    system_prompt = str(prompts.get("system_prompt", ""))
    topic_prompt = str(prompts.get("topic_prompt", ""))
    if not topic_prompt.strip():
        return _fallback_topic(episode_buffer)

    segments_summary = [
        {"segment_id": s.get("segment_id", ""), "topic": s.get("topic", ""), "turn_span": s.get("turn_span", [])}
        for s in segments
    ]

    try:
        result = _call_llm_json(
            system_prompt=system_prompt,
            user_prompt_template=topic_prompt,
            replacements={
                "<DIALOGUE_CONTEXT_JSON>": _serialize_json(dialogue_context),
                "<EPISODE_SEGMENTS_JSON>": _serialize_json(segments_summary),
                # backward compat: old prompt may still use <EPISODE_BUFFER_JSON>
                "<EPISODE_BUFFER_JSON>": _serialize_json(segments_summary),
            },
            llm_model=llm_model,
        )
        topic = str(result.get("topic", "")).strip()
        if topic:
            return topic
    except Exception as exc:
        logger.warning("Episode topic generation failed, use fallback: %s", exc)

    return _fallback_topic(episode_buffer)


def _build_episode_entry(
    dialogue_id: str,
    episode_index: int,
    episode_buffer: List[Dict[str, Any]],
    segments: List[Dict[str, Any]],
    dialogue_context: Dict[str, Any],
    prompts: Dict[str, Any],
    llm_model: Callable[[str], str],
) -> Dict[str, Any]:
    topic = _generate_episode_topic(
        dialogue_context=dialogue_context,
        segments=segments,
        episode_buffer=episode_buffer,
        prompts=prompts,
        llm_model=llm_model,
    )
    return {
        "episode_id": f"ep_{episode_index:03d}",
        "topic": topic,
        "dialogue_id": dialogue_id,
        "turn_span": [
            episode_buffer[0]["turn_id"],
            episode_buffer[-1]["turn_id"],
        ],
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# Main single-pass segmentation
# ---------------------------------------------------------------------------

def segment_dialogue_with_buffer(
    dialogue_json: Dict[str, Any],
    prompts: Dict[str, Any],
    llm_model: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """
    Single-pass two-level segmentation: Dialogue → Episodes → Segments.

    The script owns scanning order and buffer lifecycle.  The LLM only
    decides append / segment_break / episode_break for each turn, and
    later summarises each finalised buffer with a topic.
    """
    resolved_llm_model = _get_llm_model(llm_model)
    turns = _normalize_turns(dialogue_json)
    if not turns:
        return {"episodes": []}

    dialogue_id = str(dialogue_json.get("dialogue_id", ""))
    dialogue_context = _build_dialogue_context(dialogue_json, turns)

    episodes: List[Dict[str, Any]] = []
    episode_segments: List[Dict[str, Any]] = []
    episode_buffer: List[Dict[str, Any]] = [turns[0]]
    segment_buffer: List[Dict[str, Any]] = [turns[0]]

    for candidate_turn in turns[1:]:
        decision = _decide_turn_transition(
            dialogue_context=dialogue_context,
            completed_segments=episode_segments,
            segment_buffer=segment_buffer,
            candidate_turn=candidate_turn,
            prompts=prompts,
            llm_model=resolved_llm_model,
        )

        if decision == "append":
            episode_buffer.append(candidate_turn)
            segment_buffer.append(candidate_turn)
            continue

        # --- Finalise current segment (both segment_break and episode_break) ---
        episode_segments.append(
            _build_segment_entry(
                segment_index=len(episode_segments) + 1,
                segment_buffer=segment_buffer,
                dialogue_context=dialogue_context,
                prompts=prompts,
                llm_model=resolved_llm_model,
            )
        )

        if decision == "segment_break":
            episode_buffer.append(candidate_turn)
            segment_buffer = [candidate_turn]
            continue

        # --- episode_break: also finalise the episode ---
        episodes.append(
            _build_episode_entry(
                dialogue_id=dialogue_id,
                episode_index=len(episodes) + 1,
                episode_buffer=episode_buffer,
                segments=episode_segments,
                dialogue_context=dialogue_context,
                prompts=prompts,
                llm_model=resolved_llm_model,
            )
        )
        episode_buffer = [candidate_turn]
        segment_buffer = [candidate_turn]
        episode_segments = []

    # --- Finalise the last segment and episode ---
    episode_segments.append(
        _build_segment_entry(
            segment_index=len(episode_segments) + 1,
            segment_buffer=segment_buffer,
            dialogue_context=dialogue_context,
            prompts=prompts,
            llm_model=resolved_llm_model,
        )
    )
    episodes.append(
        _build_episode_entry(
            dialogue_id=dialogue_id,
            episode_index=len(episodes) + 1,
            episode_buffer=episode_buffer,
            segments=episode_segments,
            dialogue_context=dialogue_context,
            prompts=prompts,
            llm_model=resolved_llm_model,
        )
    )

    return {"episodes": episodes}


# ---------------------------------------------------------------------------
# Output structure & persistence
# ---------------------------------------------------------------------------

def build_episode_structure(dialogue_id: str, segmentation_result: Dict[str, Any]) -> Dict[str, Any]:
    """Build the final episodes_v1.json payload."""
    return {
        "dialogue_id": dialogue_id,
        "episode_version": "v1",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "episodes": segmentation_result.get("episodes", []),
    }


def save_episodes(episode_data: Dict[str, Any], episode_file: Path) -> None:
    """Save episodes to disk."""
    ensure_directory(episode_file.parent)
    with open(episode_file, "w", encoding="utf-8") as file:
        json.dump(episode_data, file, ensure_ascii=False, indent=2)


def process_dialogue_file(
    dialogue_file: Path,
    prompts: Dict[str, Any],
    episodes_root: Optional[Path] = None,
    memory_owner_name: str = "changshengEVA",
    llm_model: Optional[Callable[[str], str]] = None,
) -> bool:
    """Process one dialogue file and generate episodes_v1.json."""
    _ = memory_owner_name
    try:
        dialogue_data = load_dialogue(dialogue_file)
        dialogue_id = dialogue_data.get("dialogue_id", dialogue_file.stem)
        if "dialogue_id" not in dialogue_data:
            dialogue_data = dict(dialogue_data)
            dialogue_data["dialogue_id"] = dialogue_id

        segmentation_result = segment_dialogue_with_buffer(
            dialogue_data,
            prompts,
            llm_model=llm_model,
        )
        episode_data = build_episode_structure(dialogue_id, segmentation_result)

        episode_file = get_episode_path(dialogue_file, episodes_root)
        save_episodes(episode_data, episode_file)
        return True
    except Exception as exc:
        logger.error("Failed to process dialogue file %s: %s", dialogue_file, exc)
        return False


def scan_and_build_episodes(
    use_tqdm: bool = True,
    dialogues_root: Optional[Path] = None,
    episodes_root: Optional[Path] = None,
    memory_owner_name: str = "changshengEVA",
    prompt_language: str = "en",
    llm_model: Optional[Callable[[str], str]] = None,
    max_workers: int = 1,
) -> None:
    """
    Scan dialogues and build episodes for files that do not yet have output.

    max_workers:
        When > 1, process dialogue files in parallel (ThreadPoolExecutor). Default 1 preserves
        legacy sequential behavior.
    """
    if episodes_root is None:
        episodes_root = EPISODES_ROOT
    if dialogues_root is None:
        dialogues_root = DIALOGUES_ROOT

    ensure_directory(episodes_root)

    prompts = load_prompts(memory_owner_name, prompt_language=prompt_language)
    if not prompts:
        logger.error("dialogue_segmentation prompts not found")
        return

    dialogue_files = scan_dialogue_files(dialogues_root)
    files_to_process = [file for file in dialogue_files if dialogue_needs_episodes(file, episodes_root)]

    if not files_to_process:
        return

    workers = max(1, int(max_workers))

    if workers == 1:
        file_iter = tqdm(files_to_process, desc="Building episodes") if use_tqdm else files_to_process
        for dialogue_file in file_iter:
            process_dialogue_file(
                dialogue_file,
                prompts,
                episodes_root,
                memory_owner_name,
                llm_model=llm_model,
            )
        return

    with ThreadPoolExecutor(max_workers=min(workers, len(files_to_process))) as pool:
        futures = [
            pool.submit(
                process_dialogue_file,
                dialogue_file,
                prompts,
                episodes_root,
                memory_owner_name,
                llm_model,
            )
            for dialogue_file in files_to_process
        ]
        iterator = as_completed(futures)
        if use_tqdm:
            iterator = tqdm(iterator, total=len(futures), desc="Building episodes")
        for fut in iterator:
            fut.result()


if __name__ == "__main__":
    scan_and_build_episodes()
