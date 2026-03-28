from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from m_agent.agents.memory_agent import AgentResponse, MemoryAgent
from m_agent.memory.build_memory.filter_episode import save_eligibility, save_episode_situation
from m_agent.paths import CONFIG_DIR


logger = logging.getLogger(__name__)

DEFAULT_CHAT_CONFIG_PATH = CONFIG_DIR / "prompt" / "test_agent_chat.yaml"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_slug(text: str, fallback: str = "chat") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(text or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-_")
    return cleaned[:48] or fallback


def _truncate_text(text: str, limit: int = 96) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


class ChatMemoryPersistence:
    """Persist each chat round into MemoryCore's workflow directory and import it."""

    def __init__(
        self,
        memory_core: Any,
        *,
        user_name: str = "User",
        assistant_name: str = "Memory Assistant",
    ) -> None:
        self.memory_core = memory_core
        self.user_name = str(user_name or "User")
        self.assistant_name = str(assistant_name or "Memory Assistant")
        self._lock = threading.Lock()

    @property
    def workflow_id(self) -> str:
        return str(getattr(self.memory_core, "workflow_id", "") or "").strip() or "default"

    @property
    def memory_root(self) -> Path:
        return Path(getattr(self.memory_core, "memory_root"))

    @property
    def dialogues_dir(self) -> Path:
        return Path(getattr(self.memory_core, "dialogues_dir"))

    @property
    def episodes_dir(self) -> Path:
        return Path(getattr(self.memory_core, "episodes_dir"))

    def persist_round(
        self,
        *,
        thread_id: str,
        user_message: str,
        assistant_message: str,
        agent_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        safe_user_message = str(user_message or "").strip()
        safe_assistant_message = str(assistant_message or "").strip()
        if not safe_user_message:
            return {
                "success": False,
                "workflow_id": self.workflow_id,
                "error": "user_message is empty",
            }
        if not safe_assistant_message:
            return {
                "success": False,
                "workflow_id": self.workflow_id,
                "error": "assistant_message is empty",
            }

        with self._lock:
            created_at = _utc_now()
            dialogue_id = self._build_dialogue_id(thread_id=thread_id, created_at=created_at)
            dialogue_payload = self._build_dialogue_payload(
                dialogue_id=dialogue_id,
                thread_id=thread_id,
                user_message=safe_user_message,
                assistant_message=safe_assistant_message,
                created_at=created_at,
                agent_result=agent_result,
            )
            dialogue_file = self._dialogue_file_path(dialogue_payload)
            episode_file = self._episode_file_path(dialogue_id)
            episode_payload = self._build_episode_payload(
                dialogue_id=dialogue_id,
                topic=self._build_episode_topic(safe_user_message),
                generated_at=created_at,
            )
            eligibility_results = [
                {
                    "episode_id": "ep_001",
                    "dialogue_id": dialogue_id,
                    "eligible": True,
                    "reason": "chat_round_memory",
                    "rule_hits": [],
                    "scene_available": True,
                    "kg_available": True,
                    "emo_available": True,
                    "factual_novelty": 2,
                    "emotional_novelty": 1,
                }
            ]
            eligibility_file = episode_file.parent / "eligibility_v1.json"
            episode_situation_file = self.episodes_dir / "episode_situation.json"

            try:
                self._write_json(dialogue_file, dialogue_payload)
                self._write_json(episode_file, episode_payload)
                save_eligibility(
                    eligibility_results,
                    dialogue_id,
                    eligibility_file,
                    eligibility_version="v1",
                )
                save_episode_situation(
                    eligibility_results,
                    dialogue_id,
                    episodes_root=self.episodes_dir,
                )
                import_result = self.memory_core.load_from_episode_path(episode_file)
                import_success = bool(import_result.get("success", False))
                return {
                    "success": import_success,
                    "workflow_id": self.workflow_id,
                    "memory_root": str(self.memory_root),
                    "dialogue_id": dialogue_id,
                    "episode_id": "ep_001",
                    "dialogue_file": str(dialogue_file),
                    "episode_file": str(episode_file),
                    "eligibility_file": str(eligibility_file),
                    "episode_situation_file": str(episode_situation_file),
                    "import_result": import_result,
                    "error": None if import_success else str(import_result.get("error", "memory import failed")),
                }
            except Exception as exc:
                logger.exception("Persist chat round failed for dialogue_id=%s", dialogue_id)
                return {
                    "success": False,
                    "workflow_id": self.workflow_id,
                    "memory_root": str(self.memory_root),
                    "dialogue_id": dialogue_id,
                    "episode_id": "ep_001",
                    "dialogue_file": str(dialogue_file),
                    "episode_file": str(episode_file),
                    "eligibility_file": str(eligibility_file),
                    "episode_situation_file": str(episode_situation_file),
                    "import_result": None,
                    "error": str(exc),
                }

    def _build_dialogue_id(self, *, thread_id: str, created_at: datetime) -> str:
        time_key = created_at.strftime("%Y%m%d_%H%M%S_%f")
        safe_thread = _safe_slug(thread_id or "thread", fallback="thread")
        return f"chat_{safe_thread}_{time_key}"

    def _dialogue_file_path(self, dialogue_payload: Dict[str, Any]) -> Path:
        meta = dialogue_payload.get("meta", {})
        start_time = str(meta.get("start_time", "") or "")
        try:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            year_month = start_dt.strftime("%Y-%m")
        except Exception:
            year_month = "unknown"
        return self.dialogues_dir / year_month / f"{dialogue_payload['dialogue_id']}.json"

    def _episode_file_path(self, dialogue_id: str) -> Path:
        return self.episodes_dir / "by_dialogue" / dialogue_id / "episodes_v1.json"

    def _build_dialogue_payload(
        self,
        *,
        dialogue_id: str,
        thread_id: str,
        user_message: str,
        assistant_message: str,
        created_at: datetime,
        agent_result: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        second_turn_at = created_at + timedelta(seconds=1)
        trace_summary: Dict[str, Any] = {}
        if isinstance(agent_result, dict):
            tool_calls = agent_result.get("tool_calls")
            trace_summary = {
                "plan_summary": agent_result.get("plan_summary"),
                "tool_call_count": len(tool_calls) if isinstance(tool_calls, list) else 0,
            }

        return {
            "dialogue_id": dialogue_id,
            "user_id": self.user_name,
            "participants": [self.user_name, self.assistant_name],
            "meta": {
                "start_time": _to_utc_iso(created_at),
                "end_time": _to_utc_iso(second_turn_at),
                "language": "zh",
                "platform": "test_agent_web",
                "version": 1,
                "thread_id": str(thread_id or "").strip(),
                "source": "simple_memory_chat_agent",
                "trace_summary": trace_summary,
            },
            "turns": [
                {
                    "turn_id": 0,
                    "speaker": self.user_name,
                    "text": user_message,
                    "timestamp": _to_utc_iso(created_at),
                },
                {
                    "turn_id": 1,
                    "speaker": self.assistant_name,
                    "text": assistant_message,
                    "timestamp": _to_utc_iso(second_turn_at),
                },
            ],
        }

    @staticmethod
    def _build_episode_payload(
        *,
        dialogue_id: str,
        topic: str,
        generated_at: datetime,
    ) -> Dict[str, Any]:
        return {
            "dialogue_id": dialogue_id,
            "episode_version": "v1",
            "generated_at": _to_utc_iso(generated_at),
            "episodes": [
                {
                    "episode_id": "ep_001",
                    "topic": topic,
                    "dialogue_id": dialogue_id,
                    "turn_span": [0, 1],
                }
            ],
        }

    @staticmethod
    def _build_episode_topic(user_message: str) -> str:
        return _truncate_text(user_message, limit=80) or "chat round"

    @staticmethod
    def _write_json(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


class SimpleMemoryChatAgent:
    """A thin conversational wrapper around MemoryAgent with durable chat memory."""

    def __init__(self, config_path: str | Path = DEFAULT_CHAT_CONFIG_PATH) -> None:
        self.memory_agent = MemoryAgent(config_path=config_path)
        self.config_path = self.memory_agent.config_path
        self.default_thread_id = str(
            self.memory_agent.config.get("thread_id", "test-agent-1")
        ).strip() or "test-agent-1"
        self.user_name = str(self.memory_agent.config.get("chat_user_name", "User") or "User")
        self.assistant_name = str(
            self.memory_agent.config.get("chat_assistant_name", "Memory Assistant")
            or "Memory Assistant"
        )
        self.persist_memory = bool(self.memory_agent.config.get("persist_memory", True))
        self.chat_persona_prompt = self._load_chat_persona_prompt()
        self.memory_persistence = ChatMemoryPersistence(
            self.memory_agent.memory_sys,
            user_name=self.user_name,
            assistant_name=self.assistant_name,
        )
        self._apply_chat_persona_prompt()

    def _load_chat_persona_prompt(self) -> str:
        prompt = self.memory_agent.memory_core_config.get("chat_persona_prompt")
        if not isinstance(prompt, str):
            return ""
        return prompt.strip()

    @staticmethod
    def _merge_chat_system_prompt(base_prompt: str, persona_prompt: str) -> str:
        safe_base = str(base_prompt or "").strip()
        safe_persona = str(persona_prompt or "").strip()
        if not safe_persona:
            return safe_base
        return (
            f"{safe_base}\n\n"
            "[Chat Persona]\n"
            f"{safe_persona}\n\n"
            "[Important]\n"
            "- Keep the original retrieval rules, tool-use constraints, and evidence discipline unchanged.\n"
            "- The persona only affects conversational tone, style, and how the final answer is phrased.\n"
            "- Do not fabricate shared history or claim memory without tool evidence.\n"
        ).strip()

    def _apply_chat_persona_prompt(self) -> None:
        if not self.chat_persona_prompt:
            return
        merged_prompt = self._merge_chat_system_prompt(
            self.memory_agent.system_prompt,
            self.chat_persona_prompt,
        )
        self.memory_agent.system_prompt = merged_prompt
        self.memory_agent.agent = create_agent(
            model=self.memory_agent.model,
            system_prompt=merged_prompt,
            tools=self.memory_agent.tools,
            response_format=ToolStrategy(AgentResponse),
        )

    def chat(self, message: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")

        active_thread_id = str(thread_id or self.default_thread_id).strip() or self.default_thread_id
        agent_result = self.memory_agent.ask(question=message.strip(), thread_id=active_thread_id)
        answer_text = str(agent_result.get("answer", "") or "").strip()

        if self.persist_memory:
            memory_write = self.memory_persistence.persist_round(
                thread_id=active_thread_id,
                user_message=message.strip(),
                assistant_message=answer_text,
                agent_result=agent_result,
            )
        else:
            memory_write = {
                "success": False,
                "workflow_id": self.memory_persistence.workflow_id,
                "error": "persist_memory is disabled",
            }

        return {
            "success": True,
            "thread_id": active_thread_id,
            "question": message.strip(),
            "answer": answer_text,
            "agent_result": agent_result,
            "memory_write": memory_write,
        }


def create_simple_memory_chat_agent(
    config_path: str | Path = DEFAULT_CHAT_CONFIG_PATH,
) -> SimpleMemoryChatAgent:
    return SimpleMemoryChatAgent(config_path=config_path)
