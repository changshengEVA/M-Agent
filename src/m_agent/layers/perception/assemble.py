"""Helpers that turn runtime/chat inputs into :class:`PerceptionInput`."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from m_agent.layers.perception.contracts import ActivationFrame, PerceptionInput, Stimulus


def normalize_history_messages(
    history: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(history, list):
        return out
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "") or "").strip().lower()
        content = str(item.get("content", "") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        out.append({"role": role, "content": content})
    return out


def build_perception_input(
    *,
    thread_id: str,
    conversation_id: str,
    transaction_id: Optional[str] = None,
    stimulus_id: Optional[str] = None,
    stimulus: Stimulus,
    history_messages: Optional[List[Dict[str, Any]]] = None,
    scene_context: str = "",
    activation: Optional[ActivationFrame] = None,
    stimulus_view: str = "",
) -> PerceptionInput:
    """Build the normalized thinking-layer input."""
    return PerceptionInput(
        thread_id=thread_id,
        conversation_id=conversation_id,
        transaction_id=str(transaction_id or "").strip() or None,
        stimulus=stimulus,
        stimulus_id=str(stimulus_id or "").strip() or None,
        dialogue_history=normalize_history_messages(history_messages),
        scene_context=str(scene_context or "").strip(),
        activation=activation,
        stimulus_view=str(stimulus_view or "").strip(),
    )
