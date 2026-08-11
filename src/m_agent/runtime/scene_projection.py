"""Project runtime stimuli and tool steps into the chronological Scene.

Scene is a semantic timeline, not a dump of execution internals.  Tool calls
therefore become an ``Action`` containing the invoked arguments, while their
results become a separate ``Stimulus_EXECUTION_FEEDBACK`` entry.
"""

from __future__ import annotations

import json
import math
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional

from m_agent.layers.perception.contracts import StimulusKind
from m_agent.runtime.domain.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
)
from m_agent.runtime.turn_support.execution_feedback import (
    feedback_summary_from_tool_history,
)
from m_agent.systems.scene.protocols import SceneWriter


MAX_SCENE_TOOL_TEXT_CHARS = 8_000
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)
_FEEDBACK_RESULT_KEYS = (
    "success",
    "mode",
    "action",
    "query",
    "url",
    "count",
    "result_count",
    "content_chars",
    "answer",
    "message",
    "summary",
    "error",
    "finalize",
    "needs_clarification",
    "partial",
    "stage",
    "tool_invoked",
    "missing_fields",
    "insufficient",
)


def scene_entry_type_for_stimulus(kind: StimulusKind) -> SceneEntryType:
    return {
        StimulusKind.USER_MESSAGE: SceneEntryType.STIMULUS_USER_MESSAGE,
        StimulusKind.EXECUTION_FEEDBACK: (
            SceneEntryType.STIMULUS_EXECUTION_FEEDBACK
        ),
        StimulusKind.SCHEDULED_PLAN: SceneEntryType.STIMULUS_SCHEDULED_PLAN,
        StimulusKind.OBSERVATION_TRIGGER: (
            SceneEntryType.STIMULUS_OBSERVATION_TRIGGER
        ),
    }[kind]


def _redact_scene_value(value: Any, *, key: str = "") -> Any:
    normalized_key = str(key or "").strip().lower()
    if normalized_key and any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(item_key): _redact_scene_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_scene_value(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    return str(value)


def _json_text(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        _redact_scene_value(payload),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _bounded_json_text(
    payload: Mapping[str, Any],
    *,
    fallback: Mapping[str, Any],
) -> str:
    rendered = _json_text(payload)
    if len(rendered) <= MAX_SCENE_TOOL_TEXT_CHARS:
        return rendered
    compact = dict(fallback)
    compact["truncated"] = True
    rendered = _json_text(compact)
    if len(rendered) <= MAX_SCENE_TOOL_TEXT_CHARS:
        return rendered
    # Keep the record valid JSON even when a single returned answer is huge.
    string_values = {
        key: str(compact.get(key, ""))
        for key in ("tool_name", "error", "summary")
        if compact.get(key) not in (None, "")
    }
    minimal: Dict[str, Any] = {}
    if "success" in compact:
        minimal["success"] = bool(compact.get("success"))
    minimal["truncated"] = True
    # Bound the serialized form, not the Python strings: quotes, backslashes,
    # and control characters can expand several-fold during JSON escaping.
    low = 0
    high = max((len(value) for value in string_values.values()), default=0)
    best = _json_text(minimal)
    while low <= high:
        limit = (low + high) // 2
        candidate_payload = {
            **minimal,
            **{
                key: value[:limit]
                for key, value in string_values.items()
                if limit > 0
            },
        }
        candidate = _json_text(candidate_payload)
        if len(candidate) <= MAX_SCENE_TOOL_TEXT_CHARS:
            best = candidate
            low = limit + 1
        else:
            high = limit - 1
    return best


def render_tool_action_text(tool_step: Mapping[str, Any]) -> str:
    """Render the concrete invocation, never the tool name alone."""

    name = str(tool_step.get("tool_name", "") or "").strip() or "tool"
    raw_arguments = tool_step.get("params")
    arguments = dict(raw_arguments) if isinstance(raw_arguments, Mapping) else {}
    return _bounded_json_text(
        {
            "tool_name": name,
            "arguments": arguments,
        },
        fallback={
            "tool_name": name,
            "arguments": "(too large; see payload_ref)",
        },
    )


def render_tool_feedback_text(tool_step: Mapping[str, Any]) -> str:
    """Render provable tool output for the execution-feedback Stimulus."""

    name = str(tool_step.get("tool_name", "") or "").strip() or "tool"
    result = tool_step.get("result")
    result_dict = dict(result) if isinstance(result, Mapping) else None
    error = str(tool_step.get("error", "") or "").strip()
    if result_dict is not None:
        result_error = str(result_dict.get("error", "") or "").strip()
        if result_error:
            error = result_error
        success = bool(result_dict.get("success", not error))
    else:
        success = not error
    # Scene is an audit projection, so a non-standard custom-tool result must
    # not make an already admitted stimulus fail.  The shared renderer is more
    # opinionated (for example it coerces count fields to integers); fall back
    # to the verbatim structured result when those optional hints are invalid.
    try:
        summary = feedback_summary_from_tool_history([dict(tool_step)])
    except (TypeError, ValueError, OverflowError):
        summary = ""
    payload: Dict[str, Any] = {
        "tool_name": name,
        "success": success,
    }
    if result_dict is not None:
        payload["result"] = result_dict
    elif result is not None:
        payload["result"] = result
    if error:
        payload["error"] = error
    if summary:
        payload["summary"] = summary

    compact_result = {
        key: result_dict[key]
        for key in _FEEDBACK_RESULT_KEYS
        if result_dict is not None and key in result_dict
    }
    fallback: Dict[str, Any] = {
        "tool_name": name,
        "success": success,
        "result": compact_result,
        "summary": summary,
    }
    if error:
        fallback["error"] = error
    return _bounded_json_text(payload, fallback=fallback)


def _payload_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, Mapping) else {}


def _stimulus_actor_name(
    stimulus: StimulusEnvelope,
    *,
    agent_name: str,
    tool_name: str = "",
) -> str:
    payload = stimulus.payload if isinstance(stimulus.payload, dict) else {}
    observation = _payload_mapping(payload, "observation")
    observation_payload = _payload_mapping(observation, "payload")
    if stimulus.kind == StimulusKind.USER_MESSAGE:
        return str(
            payload.get("user_name")
            or payload.get("username")
            or payload.get("subject")
            or observation.get("subject")
            or payload.get("owner_id")
            or "user"
        ).strip() or "user"
    if stimulus.kind == StimulusKind.EXECUTION_FEEDBACK:
        return str(tool_name or payload.get("tool_name") or "execution").strip()
    if stimulus.kind == StimulusKind.SCHEDULED_PLAN:
        return str(
            payload.get("agent_name")
            or payload.get("assistant_name")
            or agent_name
            or "Agent"
        ).strip() or "Agent"
    return str(
        payload.get("observation_name")
        or payload.get("monitor_id")
        or observation_payload.get("observation_name")
        or observation_payload.get("monitor_id")
        or observation.get("source")
        or payload.get("source")
        or "observation"
    ).strip() or "observation"


def _stimulus_actor_role(kind: StimulusKind) -> SceneActor:
    if kind == StimulusKind.USER_MESSAGE:
        return SceneActor.USER
    if kind == StimulusKind.SCHEDULED_PLAN:
        return SceneActor.ASSISTANT
    return SceneActor.WORK


def _feedback_entries(
    stimulus: StimulusEnvelope,
    *,
    transaction_id: str,
    agent_name: str,
) -> List[SceneEntry]:
    payload = stimulus.payload if isinstance(stimulus.payload, dict) else {}
    history = payload.get("tool_history")
    steps = [item for item in history if isinstance(item, Mapping)] if isinstance(history, list) else []
    if not steps:
        summary = str(
            payload.get("summary")
            or stimulus.text
            or "Execution finished without structured tool evidence."
        ).strip()
        steps = [
            {
                "tool_name": str(payload.get("tool_name", "") or "execution"),
                "result": {
                    "success": bool(payload.get("success", True)),
                    "summary": summary,
                },
            }
        ]
    base_append_id = str(stimulus.stimulus_id or "").strip()
    effect_id = str(payload.get("effect_id", "") or "").strip()
    entries: List[SceneEntry] = []
    for index, step in enumerate(steps, start=1):
        tool_name = str(step.get("tool_name", "") or "").strip() or "execution"
        append_id = (
            base_append_id
            if len(steps) == 1
            else f"{base_append_id}:feedback:{index}"
        ) or None
        payload_ref = str(payload.get("payload_ref", "") or "").strip()
        if not payload_ref and effect_id:
            payload_ref = f"effect:{effect_id}:tool_history:{index}"
        entries.append(
            SceneEntry(
                seq=0,
                occurred_at=stimulus.occurred_at,
                entry_type=SceneEntryType.STIMULUS_EXECUTION_FEEDBACK,
                actor=SceneActor.WORK,
                actor_name=_stimulus_actor_name(
                    stimulus,
                    agent_name=agent_name,
                    tool_name=tool_name,
                ),
                text=render_tool_feedback_text(step),
                append_id=append_id,
                transaction_id=transaction_id,
                delegate_id=stimulus.delegate_id,
                tool_name=tool_name,
                payload_ref=payload_ref or None,
            )
        )
    return entries


def build_stimulus_scene_entries(
    stimulus: StimulusEnvelope,
    *,
    transaction_id: str,
    agent_name: str = "Agent",
) -> List[SceneEntry]:
    """Build idempotent Scene rows for any of the four top-level stimuli."""

    if stimulus.kind == StimulusKind.EXECUTION_FEEDBACK:
        return _feedback_entries(
            stimulus,
            transaction_id=transaction_id,
            agent_name=agent_name,
        )
    payload = stimulus.payload if isinstance(stimulus.payload, dict) else {}
    text = str(stimulus.text or "").strip()
    if not text:
        text = str(payload.get("stimulus_view", "") or "").strip()
    if not text:
        return []
    return [
        SceneEntry(
            seq=0,
            occurred_at=stimulus.occurred_at,
            entry_type=scene_entry_type_for_stimulus(stimulus.kind),
            actor=_stimulus_actor_role(stimulus.kind),
            actor_name=_stimulus_actor_name(
                stimulus,
                agent_name=agent_name,
            ),
            text=text,
            append_id=str(stimulus.stimulus_id or "").strip() or None,
            transaction_id=str(transaction_id or "").strip() or None,
            delegate_id=stimulus.delegate_id,
            payload_ref=str(payload.get("payload_ref", "") or "").strip() or None,
        )
    ]


def append_stimulus_scene_entries(
    scene_writer: SceneWriter,
    stimulus: StimulusEnvelope,
    *,
    transaction_id: str,
    agent_name: str = "Agent",
) -> List[SceneEntry]:
    """Append one stimulus projection with stable append identities."""

    stored: List[SceneEntry] = []
    for entry in build_stimulus_scene_entries(
        stimulus,
        transaction_id=transaction_id,
        agent_name=agent_name,
    ):
        if entry.append_id is None:
            stored.append(scene_writer.append(stimulus.conversation_id, entry))
            continue
        try:
            stored.append(
                scene_writer.append(
                    stimulus.conversation_id,
                    entry,
                    append_id=entry.append_id,
                )
            )
        except TypeError:
            stored.append(scene_writer.append(stimulus.conversation_id, entry))
    return stored


__all__ = [
    "MAX_SCENE_TOOL_TEXT_CHARS",
    "append_stimulus_scene_entries",
    "build_stimulus_scene_entries",
    "render_tool_action_text",
    "render_tool_feedback_text",
    "scene_entry_type_for_stimulus",
]
