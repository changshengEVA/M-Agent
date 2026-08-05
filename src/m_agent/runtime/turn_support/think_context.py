"""Bridge Runtime contracts to layer PerceptionInput / ThinkContext."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, List, Optional

from m_agent.layers.perception import (
    ActivationFrame,
    EventFrame,
    EvidenceFrame,
    ObjectiveFrame,
    PerceptionInput,
    build_perception_input,
)
from m_agent.runtime.domain.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
    StimulusKind,
    TransactionRecord,
)
from m_agent.runtime.turn_support.execution_feedback import (
    build_feedback_user_message,
    extract_last_tool_step,
    feedback_summary_from_tool_history,
    looks_like_multi_step_request,
)
from m_agent.systems.scene.protocols import SceneReader


@dataclass
class ThinkContext:
    transaction: TransactionRecord
    stimulus: StimulusEnvelope
    scene_tail: List[SceneEntry]


def read_scene_segment(
    scene_reader: SceneReader,
    thread_id: str,
    *,
    max_entries: int,
    entry_filter: Optional[Callable[[SceneEntry], bool]] = None,
) -> List[SceneEntry]:
    """Scene entries in the current flush segment (since last memory flush), capped."""
    tid = str(thread_id or "").strip()
    cap = max(1, int(max_entries or 40))
    since_fn = getattr(scene_reader, "entries_since_flush", None)
    if callable(since_fn):
        entries = list(since_fn(tid))
    else:
        # Standard Scene readers implement entries_since_flush. The wider
        # fallback prevents internal events from starving a filtered view.
        fallback_cap = cap if entry_filter is None else max(cap * 8, 320)
        entries = list(scene_reader.tail(tid, limit=fallback_cap))
    if entry_filter is not None:
        entries = [entry for entry in entries if entry_filter(entry)]
    if len(entries) > cap:
        return entries[-cap:]
    return entries


def format_scene_tail(
    entries: List[SceneEntry],
    *,
    current_transaction_id: str = "",
    max_chars: int = 8000,
) -> str:
    if not entries:
        return ""
    current_tx = str(current_transaction_id or "").strip()
    context_refs: Dict[str, str] = {}

    def tx_ref(entry: SceneEntry) -> str:
        entry_tx = str(entry.transaction_id or "").strip()
        if current_tx and entry_tx == current_tx:
            return "current"
        if not entry_tx:
            return "unbound"
        if entry_tx not in context_refs:
            context_refs[entry_tx] = f"context_{len(context_refs) + 1}"
        return context_refs[entry_tx]

    header = "[Scene Context — contextual history, not Observed Evidence]"
    rendered: List[str] = []
    for entry in entries:
        scope = tx_ref(entry)
        if (
            scope != "current"
            and entry.entry_type
            not in {SceneEntryType.UTTERANCE, SceneEntryType.REPLY}
        ):
            # Internal statements from another transaction are neither user
            # dialogue nor evidence for the current activation.
            continue
        line = (
            f"- ({entry.occurred_at}) "
            f"[tx={scope} {entry.actor.value}/{entry.entry_type.value}] "
            f"{entry.text}"
        )
        rendered.append(line)

    # Context is a chronological suffix. Select it newest-first so a long
    # segment can never retain stale entries by dropping the latest evidence.
    # Reverse the selected suffix again before rendering to preserve Scene
    # order for the model.
    budget = max(0, int(max_chars or 0) - len(header) - 1)
    selected_reversed: List[str] = []
    used = 0
    for line in reversed(rendered):
        separator = 1 if selected_reversed else 0
        required = len(line) + separator
        if required > budget - used:
            if not selected_reversed and budget > 3:
                # A single oversized newest entry is still more useful than
                # an empty or stale context.
                selected_reversed.append(line[: budget - 3].rstrip() + "...")
            break
        selected_reversed.append(line)
        used += required
    if not selected_reversed:
        return header
    return "\n".join([header, *reversed(selected_reversed)])


def _is_current_user_scene_entry(
    entry: SceneEntry,
    *,
    stimulus: StimulusEnvelope,
    transaction: TransactionRecord,
) -> bool:
    """Return whether ``entry`` is the Scene copy of this user stimulus."""

    if stimulus.kind != StimulusKind.USER_MESSAGE:
        return False
    if entry.actor != SceneActor.USER or entry.entry_type != SceneEntryType.UTTERANCE:
        return False
    stimulus_id = str(stimulus.stimulus_id or "").strip()
    append_id = str(entry.append_id or "").strip()
    if stimulus_id and append_id:
        return append_id == stimulus_id
    # Compatibility fallback for Scene writers that predate append identity.
    return bool(
        str(entry.transaction_id or "").strip() == transaction.transaction_id
        and str(entry.occurred_at or "").strip() == str(stimulus.occurred_at or "").strip()
        and str(entry.text or "").strip() == str(stimulus.text or "").strip()
    )


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
    stimulus: StimulusEnvelope,
    scene_reader: SceneReader,
    scene_context_max_entries: int,
    history_messages: Optional[List[Dict[str, Any]]] = None,
) -> PerceptionInput:
    scene_tail = read_scene_segment(
        scene_reader,
        transaction.conversation_id,
        max_entries=scene_context_max_entries,
    )
    pending_user_request = ""
    if stimulus.kind == StimulusKind.EXECUTION_FEEDBACK:
        pending_user_request = latest_user_utterance_from_scene(scene_tail)
    user_message = _stimulus_user_message(
        stimulus,
        pending_user_request=pending_user_request,
    )
    payload = dict(stimulus.payload or {})
    if stimulus.kind == StimulusKind.EXECUTION_FEEDBACK:
        tool_history = stimulus.payload.get("tool_history")
        structured_summary = feedback_summary_from_tool_history(tool_history)
        last_step = extract_last_tool_step(tool_history)
        payload["execution_feedback"] = {
            "delegate_id": stimulus.delegate_id,
            "summary": structured_summary or stimulus.payload.get("summary", ""),
            "llm_summary": stimulus.payload.get("summary", ""),
            "structured_summary": structured_summary,
            "last_tool_step": last_step,
            "multi_step_request": looks_like_multi_step_request(pending_user_request),
        }
        if pending_user_request:
            payload["pending_user_request"] = pending_user_request
    activation = _activation_for_stimulus(
        transaction=transaction,
        stimulus=stimulus,
        payload=payload,
    )
    context_tail = [
        entry
        for entry in scene_tail
        if not _is_current_user_scene_entry(
            entry,
            stimulus=stimulus,
            transaction=transaction,
        )
    ]
    return build_perception_input(
        thread_id=transaction.thread_id,
        conversation_id=transaction.conversation_id,
        transaction_id=transaction.transaction_id,
        stimulus=replace(stimulus.stimulus, text=user_message, payload=payload),
        history_messages=history_messages,
        scene_context=format_scene_tail(
            context_tail,
            current_transaction_id=transaction.transaction_id,
        ),
        activation=activation,
    )


def _transaction_objective(transaction: TransactionRecord) -> str:
    task_state = getattr(transaction, "task_state", None)
    return str(getattr(task_state, "goal", "") or "").strip()


def _activation_for_stimulus(
    *,
    transaction: TransactionRecord,
    stimulus: StimulusEnvelope,
    payload: Dict[str, Any],
) -> Optional[ActivationFrame]:
    parsed = ActivationFrame.from_dict(payload.get("activation"))
    activation_id = str(
        stimulus.activation_id
        or transaction.current_activation_id
        or ""
    ).strip() or None
    transaction_goal = _transaction_objective(transaction)

    if parsed is not None:
        event = parsed.event
        if not event.occurred_at:
            event = replace(event, occurred_at=stimulus.occurred_at)
        objective = parsed.objective
        # A native objective carried by the causal envelope is authoritative.
        # A legacy text recovery must not overwrite an already stable task goal.
        if (
            transaction_goal
            and (
                objective is None
                or str(objective.encoding or "").strip() == "legacy_text"
            )
        ):
            objective = ObjectiveFrame(
                description=transaction_goal,
                encoding="transaction_task_state",
            )
        return replace(
            parsed,
            event=event,
            objective=objective,
            activation_id=activation_id or parsed.activation_id,
        )

    if stimulus.kind == StimulusKind.SCHEDULED_PLAN:
        description = transaction_goal or str(
            stimulus.text or payload.get("prompt", "") or ""
        ).strip()
        encoding = "transaction_task_state" if transaction_goal else "legacy_text"
        return ActivationFrame(
            activation_id=activation_id,
            event=EventFrame(
                event_type="schedule_due",
                source="heartbeat",
                subject_ref=str(
                    stimulus.schedule_id or payload.get("schedule_id", "") or ""
                ).strip()
                or None,
                occurred_at=stimulus.occurred_at,
                facts={
                    "due_at_utc": str(payload.get("due_at_utc", "") or "").strip(),
                    "timezone_name": str(payload.get("timezone_name", "") or "").strip(),
                },
            ),
            objective=(
                ObjectiveFrame(description=description, encoding=encoding)
                if description
                else None
            ),
            evidence=[],
        )

    if stimulus.kind == StimulusKind.EXECUTION_FEEDBACK:
        evidence = _execution_evidence_frames(stimulus, payload)
        return ActivationFrame(
            activation_id=activation_id,
            event=EventFrame(
                event_type="execution_feedback_received",
                source="execution",
                subject_ref=str(
                    stimulus.delegate_id or payload.get("delegate_id", "") or ""
                ).strip()
                or None,
                occurred_at=stimulus.occurred_at,
                facts={"evidence_count": len(evidence)},
            ),
            objective=(
                ObjectiveFrame(
                    description=transaction_goal,
                    encoding="transaction_task_state",
                )
                if transaction_goal
                else None
            ),
            evidence=evidence,
        )

    if stimulus.kind == StimulusKind.OBSERVATION_TRIGGER:
        return ActivationFrame(
            activation_id=activation_id,
            event=EventFrame(
                event_type=str(payload.get("event_type", "") or "").strip()
                or "observation_trigger",
                source=str(payload.get("trigger_source", "") or "runtime").strip()
                or "runtime",
                occurred_at=stimulus.occurred_at,
                facts={
                    key: value
                    for key, value in payload.items()
                    if key not in {"activation", "evidence"}
                },
            ),
            objective=(
                ObjectiveFrame(
                    description=transaction_goal,
                    encoding="transaction_task_state",
                )
                if transaction_goal
                else None
            ),
            evidence=[],
        )
    return None


def _execution_evidence_frames(
    stimulus: StimulusEnvelope,
    payload: Dict[str, Any],
) -> List[EvidenceFrame]:
    history = payload.get("tool_history")
    if not isinstance(history, list):
        history = []
    evidence: List[EvidenceFrame] = []
    for index, item in enumerate(history):
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name", "") or "").strip() or "tool"
        result = item.get("result")
        result_dict = dict(result) if isinstance(result, dict) else {}
        summary = str(
            result_dict.get(
                "summary",
                result_dict.get("message", result_dict.get("answer", "")),
            )
            or ""
        ).strip()
        if not summary and result is not None:
            summary = str(result).strip()
        evidence_type = (
            "assistant_reply_committed"
            if tool_name == "reply_to_user" and result_dict.get("success") is True
            else "tool_outcome"
        )
        evidence.append(
            EvidenceFrame(
                evidence_type=evidence_type,
                source=tool_name,
                evidence_id=f"{stimulus.stimulus_id}:tool:{index}",
                summary=summary,
                occurred_at=stimulus.occurred_at,
                facts={
                    "tool_name": tool_name,
                    "success": result_dict.get("success"),
                    "count": result_dict.get("count"),
                    "partial": result_dict.get("partial"),
                    "needs_clarification": result_dict.get("needs_clarification"),
                },
            )
        )
    if not evidence:
        feedback = payload.get("execution_feedback")
        summary = (
            str(feedback.get("summary", "") or "").strip()
            if isinstance(feedback, dict)
            else str(payload.get("summary", "") or "").strip()
        )
        if summary:
            evidence.append(
                EvidenceFrame(
                    evidence_type="execution_feedback",
                    source="execution",
                    evidence_id=f"{stimulus.stimulus_id}:feedback",
                    summary=summary,
                    occurred_at=stimulus.occurred_at,
                )
            )
    return evidence


def _stimulus_user_message(
    stimulus: StimulusEnvelope,
    *,
    pending_user_request: str = "",
) -> str:
    if stimulus.kind == StimulusKind.USER_MESSAGE:
        return str(stimulus.text or "").strip()
    if stimulus.kind == StimulusKind.SCHEDULED_PLAN:
        return "schedule_due"
    if stimulus.kind == StimulusKind.EXECUTION_FEEDBACK:
        return build_feedback_user_message(
            pending_user_request=pending_user_request,
            tool_history=stimulus.payload.get("tool_history"),
            llm_summary=str(stimulus.payload.get("summary", "") or "").strip(),
        )
    return str(stimulus.text or "").strip() or "[stimulus]"
