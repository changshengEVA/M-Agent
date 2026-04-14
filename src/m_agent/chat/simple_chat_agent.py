from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from m_agent.agents.chat_controller_agent import (
    DEFAULT_CHAT_CONFIG_PATH,
    ChatControllerAgent,
)
from m_agent.memory.build_memory.filter_episode import save_eligibility, save_episode_situation


logger = logging.getLogger(__name__)


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
    """Persist chat rounds into MemoryCore's workflow directory and import them."""

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
        return self.persist_dialogue(
            thread_id=thread_id,
            rounds=[
                {
                    "user_message": user_message,
                    "assistant_message": assistant_message,
                    "agent_result": agent_result,
                }
            ],
            reason="chat_round_memory",
            source="simple_memory_chat_agent",
        )

    def persist_dialogue(
        self,
        *,
        thread_id: str,
        rounds: List[Dict[str, Any]],
        reason: str = "chat_thread_flush",
        source: str = "chat_api_thread_flush",
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        normalized_rounds = self._normalize_rounds(rounds)
        if not normalized_rounds:
            return {
                "success": False,
                "workflow_id": self.workflow_id,
                "error": "rounds are empty",
            }

        with self._lock:
            created_at = normalized_rounds[0]["user_at"]
            dialogue_id = self._build_dialogue_id(thread_id=thread_id, created_at=created_at)
            dialogue_payload = self._build_dialogue_payload(
                dialogue_id=dialogue_id,
                thread_id=thread_id,
                rounds=normalized_rounds,
                source=source,
            )
            dialogue_file = self._dialogue_file_path(dialogue_payload)
            episode_file = self._episode_file_path(dialogue_id)
            turn_count = len(dialogue_payload.get("turns", []))
            episode_payload = self._build_episode_payload(
                dialogue_id=dialogue_id,
                topic=self._build_episode_topic(normalized_rounds),
                generated_at=created_at,
                turn_span=[0, max(turn_count - 1, 0)],
            )
            eligibility_results = [
                {
                    "episode_id": "ep_001",
                    "dialogue_id": dialogue_id,
                    "eligible": True,
                    "reason": str(reason or "chat_thread_flush"),
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
                if progress_callback is None:
                    import_result = self.memory_core.load_from_episode_path(episode_file)
                else:
                    try:
                        import_result = self.memory_core.load_from_episode_path(
                            episode_file,
                            progress_callback=progress_callback,
                        )
                    except TypeError as exc:
                        if "progress_callback" not in str(exc):
                            raise
                        import_result = self.memory_core.load_from_episode_path(episode_file)
                import_success = bool(import_result.get("success", False))
                return {
                    "success": import_success,
                    "workflow_id": self.workflow_id,
                    "memory_root": str(self.memory_root),
                    "dialogue_id": dialogue_id,
                    "episode_id": "ep_001",
                    "round_count": len(normalized_rounds),
                    "turn_count": turn_count,
                    "dialogue_file": str(dialogue_file),
                    "episode_file": str(episode_file),
                    "eligibility_file": str(eligibility_file),
                    "episode_situation_file": str(episode_situation_file),
                    "import_result": import_result,
                    "error": None if import_success else str(import_result.get("error", "memory import failed")),
                }
            except Exception as exc:
                logger.exception("Persist chat dialogue failed for dialogue_id=%s", dialogue_id)
                return {
                    "success": False,
                    "workflow_id": self.workflow_id,
                    "memory_root": str(self.memory_root),
                    "dialogue_id": dialogue_id,
                    "episode_id": "ep_001",
                    "round_count": len(normalized_rounds),
                    "turn_count": turn_count,
                    "dialogue_file": str(dialogue_file),
                    "episode_file": str(episode_file),
                    "eligibility_file": str(eligibility_file),
                    "episode_situation_file": str(episode_situation_file),
                    "import_result": None,
                    "error": str(exc),
                }

    def _normalize_rounds(self, rounds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        if not isinstance(rounds, list):
            return normalized

        for item in rounds:
            if not isinstance(item, dict):
                continue
            user_message = str(item.get("user_message", "") or "").strip()
            assistant_message = str(item.get("assistant_message", "") or "").strip()
            if not user_message or not assistant_message:
                continue

            user_at = item.get("user_at")
            if isinstance(user_at, datetime):
                user_dt = user_at
            else:
                user_dt = _utc_now()

            assistant_at = item.get("assistant_at")
            if isinstance(assistant_at, datetime):
                assistant_dt = assistant_at
            else:
                assistant_dt = user_dt + timedelta(seconds=1)

            if assistant_dt < user_dt:
                assistant_dt = user_dt + timedelta(seconds=1)

            normalized.append(
                {
                    "user_message": user_message,
                    "assistant_message": assistant_message,
                    "user_at": user_dt,
                    "assistant_at": assistant_dt,
                    "agent_result": item.get("agent_result") if isinstance(item.get("agent_result"), dict) else None,
                }
            )
        return normalized

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
        rounds: List[Dict[str, Any]],
        source: str,
    ) -> Dict[str, Any]:
        first_round = rounds[0]
        start_at = first_round["user_at"]
        end_at = rounds[-1]["assistant_at"]
        trace_summary = self._build_trace_summary(rounds)
        turns: List[Dict[str, Any]] = []
        turn_id = 0
        for round_item in rounds:
            turns.append(
                {
                    "turn_id": turn_id,
                    "speaker": self.user_name,
                    "text": round_item["user_message"],
                    "timestamp": _to_utc_iso(round_item["user_at"]),
                }
            )
            turn_id += 1
            turns.append(
                {
                    "turn_id": turn_id,
                    "speaker": self.assistant_name,
                    "text": round_item["assistant_message"],
                    "timestamp": _to_utc_iso(round_item["assistant_at"]),
                }
            )
            turn_id += 1

        return {
            "dialogue_id": dialogue_id,
            "user_id": self.user_name,
            "participants": [self.user_name, self.assistant_name],
            "meta": {
                "start_time": _to_utc_iso(start_at),
                "end_time": _to_utc_iso(end_at),
                "language": "zh",
                "platform": "test_agent_web",
                "version": 1,
                "thread_id": str(thread_id or "").strip(),
                "source": str(source or "chat_api_thread_flush"),
                "round_count": len(rounds),
                "trace_summary": trace_summary,
            },
            "turns": turns,
        }

    def _build_trace_summary(self, rounds: List[Dict[str, Any]]) -> Dict[str, Any]:
        tool_call_count = 0
        recall_modes: List[str] = []
        for item in rounds:
            agent_result = item.get("agent_result")
            if not isinstance(agent_result, dict):
                continue
            if isinstance(agent_result.get("tool_call_count"), int):
                tool_call_count += int(agent_result.get("tool_call_count", 0) or 0)
            recall_mode = str(agent_result.get("recall_mode", "") or "").strip()
            if recall_mode and recall_mode not in recall_modes:
                recall_modes.append(recall_mode)

        return {
            "tool_call_count": tool_call_count,
            "recall_modes": recall_modes,
        }

    @staticmethod
    def _build_episode_payload(
        *,
        dialogue_id: str,
        topic: str,
        generated_at: datetime,
        turn_span: List[int],
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
                    "turn_span": turn_span,
                }
            ],
        }

    @staticmethod
    def _build_episode_topic(rounds: List[Dict[str, Any]]) -> str:
        user_messages = [str(item.get("user_message", "") or "").strip() for item in rounds if isinstance(item, dict)]
        user_messages = [text for text in user_messages if text]
        if not user_messages:
            return "chat dialogue"
        combined = " / ".join(user_messages[:3])
        if len(user_messages) > 3:
            combined += " / ..."
        return _truncate_text(combined, limit=80) or "chat dialogue"

    @staticmethod
    def _write_json(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


class SimpleMemoryChatAgent:
    """Thin application wrapper around ChatControllerAgent plus forced memory persistence."""

    def __init__(self, config_path: str | Path = DEFAULT_CHAT_CONFIG_PATH) -> None:
        self.chat_controller = ChatControllerAgent(config_path=config_path)
        self.memory_agent = self.chat_controller.memory_agent
        self.config_path = self.chat_controller.config_path
        self.default_thread_id = self.chat_controller.default_thread_id
        self.chat_persona_prompt = self.chat_controller.chat_persona_prompt
        self.chat_system_prompt = self.chat_controller.chat_system_prompt
        self.user_name = str(self.chat_controller.config.get("chat_user_name", "User") or "User")
        self.assistant_name = str(
            self.chat_controller.config.get("chat_assistant_name", "Memory Assistant")
            or "Memory Assistant"
        )
        self.persist_memory = bool(self.chat_controller.config.get("persist_memory", True))
        self.memory_persistence = ChatMemoryPersistence(
            self.memory_agent.memory_sys,
            user_name=self.user_name,
            assistant_name=self.assistant_name,
        )

    @staticmethod
    def _merge_chat_system_prompt(base_prompt: str, persona_prompt: str) -> str:
        return ChatControllerAgent._merge_chat_system_prompt(
            base_prompt,
            persona_prompt,
            prompt_language="en",
        )

    def get_schedule_agent(self):
        return self.chat_controller.get_schedule_agent()

    def chat(
        self,
        message: str,
        thread_id: Optional[str] = None,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        persist_memory: Optional[bool] = None,
        source: str = "user",
        system_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")

        safe_message = message.strip()
        controller_result = self.chat_controller.chat(
            message=safe_message,
            thread_id=thread_id,
            history_messages=history_messages,
            source=source,
            system_context=system_context,
        )
        active_thread_id = str(
            controller_result.get("thread_id", thread_id or self.default_thread_id) or self.default_thread_id
        ).strip() or self.default_thread_id
        answer_text = str(controller_result.get("answer", "") or "").strip()
        agent_result = controller_result.get("agent_result")
        should_persist = self.persist_memory if persist_memory is None else bool(persist_memory)

        if should_persist:
            memory_write = self.memory_persistence.persist_round(
                thread_id=active_thread_id,
                user_message=safe_message,
                assistant_message=answer_text,
                agent_result=agent_result if isinstance(agent_result, dict) else None,
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
            "question": safe_message,
            "answer": answer_text,
            "history_messages": controller_result.get("history_messages", []),
            "agent_result": agent_result,
            "memory_write": memory_write,
        }


def create_simple_memory_chat_agent(
    config_path: str | Path = DEFAULT_CHAT_CONFIG_PATH,
) -> SimpleMemoryChatAgent:
    return SimpleMemoryChatAgent(config_path=config_path)
