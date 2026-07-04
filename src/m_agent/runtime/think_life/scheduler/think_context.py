"""Bridge Think-life contracts to layer PerceptionInput / ThinkContext."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from m_agent.layers.perception import PerceptionInput, build_perception_input
from m_agent.runtime.think_life.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    Stimulus,
    StimulusKind,
    TransactionRecord,
)
from m_agent.runtime.think_life.scheduler.execution_feedback import (
    build_feedback_user_message,
    extract_last_tool_step,
    feedback_summary_from_tool_history,
    looks_like_multi_step_request,
)
from m_agent.systems.scene.protocols import SceneReader


@dataclass
class ThinkContext:
    transaction: TransactionRecord
    stimulus: Stimulus
    scene_tail: List[SceneEntry]


def read_scene_segment(
    scene_reader: SceneReader,
    thread_id: str,
    *,
    max_entries: int,
) -> List[SceneEntry]:
    """Scene entries in the current flush segment (since last memory flush), capped."""
    tid = str(thread_id or "").strip()
    cap = max(1, int(max_entries or 40))
    since_fn = getattr(scene_reader, "entries_since_flush", None)
    if callable(since_fn):
        entries = list(since_fn(tid))
    else:
        entries = list(scene_reader.tail(tid, limit=cap))
    if len(entries) > cap:
        return entries[-cap:]
    return entries


def format_scene_tail(entries: List[SceneEntry], *, max_chars: int = 8000) -> str:
    if not entries:
        return ""
    lines: List[str] = ["[Scene log — current segment since last memory flush]"]
    used = 0
    for entry in entries:
        line = f"- ({entry.occurred_at}) [{entry.actor.value}/{entry.entry_type.value}] {entry.text}"
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def latest_user_utterance_from_scene(entries: List[SceneEntry]) -> str:
    """Most recent user utterance in chronological scene tail (for feedback turns)."""
    for entry in reversed(entries):
        if entry.actor == SceneActor.USER and entry.entry_type == SceneEntryType.UTTERANCE:
            text = str(entry.text or "").strip()
            if text:
                return text
    return ""


def build_perception_for_stimulus(
    *,
    transaction: TransactionRecord,
    stimulus: Stimulus,
    scene_reader: SceneReader,
    scene_context_max_entries: int,
    history_messages: Optional[List[Dict[str, Any]]] = None,
) -> PerceptionInput:
    scene_tail = read_scene_segment(
        scene_reader,
        transaction.thread_id,
        max_entries=scene_context_max_entries,
    )
    pending_user_request = ""
    if stimulus.kind == StimulusKind.EXECUTION_FEEDBACK:
        pending_user_request = latest_user_utterance_from_scene(scene_tail)
    user_message = _stimulus_user_message(
        stimulus,
        pending_user_request=pending_user_request,
    )
    system_context: Dict[str, Any] = {
        "think_life": True,
        "transaction_id": transaction.transaction_id,
        "stimulus_kind": stimulus.kind.value,
        "scene_tail_text": format_scene_tail(scene_tail),
    }
    if stimulus.kind == StimulusKind.EXECUTION_FEEDBACK:
        tool_history = stimulus.payload.get("tool_history")
        structured_summary = feedback_summary_from_tool_history(tool_history)
        last_step = extract_last_tool_step(tool_history)
        system_context["execution_feedback"] = {
            "delegate_id": stimulus.delegate_id,
            "summary": structured_summary or stimulus.payload.get("summary", ""),
            "llm_summary": stimulus.payload.get("summary", ""),
            "structured_summary": structured_summary,
            "last_tool_step": last_step,
            "multi_step_request": looks_like_multi_step_request(pending_user_request),
        }
        if pending_user_request:
            system_context["pending_user_request"] = pending_user_request
    source = "user"
    if stimulus.kind == StimulusKind.HEARTBEAT:
        source = "schedule"
    elif stimulus.kind == StimulusKind.EXECUTION_FEEDBACK:
        source = "execution_feedback"

    return build_perception_input(
        message=user_message,
        thread_id=transaction.thread_id,
        conversation_id=transaction.transaction_id,
        history_messages=history_messages,
        source=source,
        system_context=system_context,
    )


def _stimulus_user_message(
    stimulus: Stimulus,
    *,
    pending_user_request: str = "",
) -> str:
    if stimulus.kind == StimulusKind.USER_MESSAGE:
        return str(stimulus.payload.get("text", "") or "").strip()
    if stimulus.kind == StimulusKind.HEARTBEAT:
        return str(stimulus.payload.get("text", "") or stimulus.payload.get("prompt", "") or "").strip()
    if stimulus.kind == StimulusKind.EXECUTION_FEEDBACK:
        return build_feedback_user_message(
            pending_user_request=pending_user_request,
            tool_history=stimulus.payload.get("tool_history"),
            llm_summary=str(stimulus.payload.get("summary", "") or "").strip(),
        )
    return str(stimulus.payload.get("text", "") or "").strip() or "[stimulus]"
