from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from m_agent.chat.simple_chat_agent import ChatMemoryPersistence, SimpleMemoryChatAgent


class StubMemoryCore:
    def __init__(self, root: Path) -> None:
        self.workflow_id = "test_agent"
        self.memory_root = root / "data" / "memory" / self.workflow_id
        self.dialogues_dir = self.memory_root / "dialogues"
        self.episodes_dir = self.memory_root / "episodes"
        self.dialogues_dir.mkdir(parents=True, exist_ok=True)
        self.episodes_dir.mkdir(parents=True, exist_ok=True)
        self.load_calls: list[Path] = []

    def load_from_episode_path(self, path: Path) -> Dict[str, Any]:
        self.load_calls.append(Path(path))
        return {"success": True, "path": str(path)}


def _load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_persist_round_writes_dialogue_episode_and_imports(tmp_path: Path) -> None:
    memory_core = StubMemoryCore(tmp_path)
    persistence = ChatMemoryPersistence(
        memory_core,
        user_name="User",
        assistant_name="Memory Assistant",
    )

    result = persistence.persist_round(
        thread_id="demo-thread",
        user_message="我今天去了图书馆。",
        assistant_message="记住了，你今天去了图书馆。",
        agent_result={
            "plan_summary": "Used direct retrieval.",
            "tool_calls": [{"call_id": 1, "tool_name": "search_details"}],
        },
    )

    assert result["success"] is True
    dialogue_file = Path(result["dialogue_file"])
    episode_file = Path(result["episode_file"])
    eligibility_file = Path(result["eligibility_file"])
    episode_situation_file = Path(result["episode_situation_file"])

    assert dialogue_file.exists()
    assert episode_file.exists()
    assert eligibility_file.exists()
    assert episode_situation_file.exists()

    dialogue_data = _load_json(dialogue_file)
    assert dialogue_data["participants"] == ["User", "Memory Assistant"]
    assert len(dialogue_data["turns"]) == 2
    assert dialogue_data["turns"][0]["text"] == "我今天去了图书馆。"
    assert dialogue_data["turns"][1]["text"] == "记住了，你今天去了图书馆。"

    episode_data = _load_json(episode_file)
    assert episode_data["dialogue_id"] == dialogue_data["dialogue_id"]
    assert episode_data["episodes"][0]["episode_id"] == "ep_001"
    assert episode_data["episodes"][0]["turn_span"] == [0, 1]

    situation_data = _load_json(episode_situation_file)
    episode_key = f"{dialogue_data['dialogue_id']}:ep_001"
    assert episode_key in situation_data["episodes"]
    assert memory_core.load_calls == [episode_file]


def test_merge_chat_system_prompt_keeps_base_rules_and_adds_persona() -> None:
    merged = SimpleMemoryChatAgent._merge_chat_system_prompt(
        "BASE SYSTEM PROMPT",
        "你是一个温和的长期陪伴型助手。",
    )

    assert "BASE SYSTEM PROMPT" in merged
    assert "[Chat Persona]" in merged
    assert "你是一个温和的长期陪伴型助手。" in merged
    assert "Keep the original retrieval rules" in merged
