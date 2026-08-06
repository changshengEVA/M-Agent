"""Persona-owning, plan-only thinking layer for the Runtime runtime.

Each stimulus updates transaction task state and produces one structured
decision. Tool execution and user-visible replies are delegated by the
Runtime scheduler, so this layer never invokes capabilities directly.
"""
from __future__ import annotations

import logging
import json
from copy import deepcopy
from threading import RLock
from typing import Any, Callable, Dict, List, Optional

from m_agent.layers.execution.core import ExecutionAgent
from m_agent.layers.execution.model_provider import ModelProvider
from m_agent.systems.episodic import DefaultEpisodeRecorder, EpisodeRecorder
from m_agent.systems.wm import WMReader
from m_agent.layers.perception.contracts import PerceptionInput, StimulusKind
from m_agent.layers.thinking.persona import (
    build_capability_boundary_block,
)
from m_agent.layers.thinking.contracts import (
    DecisionOutput,
    TASK_COMPLETION_AWAITING_USER,
    TASK_COMPLETION_COMPLETED,
    TASK_COMPLETION_PROCESSING,
    TaskProgress,
    TaskProgressUpdate,
    TaskStateOutput,
    TransactionResolution,
    ThinkingDecision,
    ThinkingTurnOutput,
    is_silent_mode,
    normalize_task_completion_status,
    normalize_thinking_mode,
)
from m_agent.layers.thinking.state import (
    ConversationState,
    ConversationStateRegistry,
    ThinkingScratch,
    TransactionBoundState,
)
logger = logging.getLogger(__name__)

#: Callback signature for streaming planning state to the perception layer,
#: which forwards it to SSE. Implementations must be cheap and must not raise.
ThinkingEventEmitter = Callable[[str, Dict[str, Any]], None]

THINKING_MODE_SINGLE_CALL = "single_call"
THINKING_MODE_LEGACY_TWO_CALL = "legacy_two_call"
_THINKING_MODES = frozenset(
    {THINKING_MODE_SINGLE_CALL, THINKING_MODE_LEGACY_TWO_CALL}
)


class ThinkingAgent:
    """Update task state and choose the next Runtime action."""

    def __init__(
        self,
        *,
        execution_agent: ExecutionAgent,
        model_provider: ModelProvider,
        system_prompt: str,
        persona_prompt: str = "",
        wm_reader: Optional[WMReader] = None,
        episode_recorder: Optional[EpisodeRecorder] = None,
        state_registry: Optional[ConversationStateRegistry] = None,
        prompt_language: str = "zh",
        task_state_base_prompt: str = "",
        task_state_instructions_prompt: str = "",
        plan_instructions_prompt: str = "",
        thinking_turn_instructions_prompt: str = "",
        thinking_mode: str = THINKING_MODE_SINGLE_CALL,
        capability_boundary_header: str = "",
        fallback_answer_prompt: str = "",
        transaction_resolution_base_prompt: str = "",
        transaction_resolution_instructions_prompt: str = "",
    ) -> None:
        self.execution_agent = execution_agent
        self.model_provider = model_provider
        self.system_prompt = str(system_prompt or "").strip()
        self.persona_prompt = str(persona_prompt or "").strip()
        self.wm_reader = wm_reader
        self.episode_recorder: EpisodeRecorder = episode_recorder or DefaultEpisodeRecorder()
        self.state_registry = state_registry or ConversationStateRegistry()
        self._scratches: Dict[str, ThinkingScratch] = {}
        self._scratch_lock = RLock()
        self.prompt_language = str(prompt_language or "zh").strip().lower() or "zh"

        # YAML-overridable prompt fragments. Empty/None means "use built-in default".
        self._task_state_base_override = str(task_state_base_prompt or "").strip()
        self._task_state_instructions_override = str(task_state_instructions_prompt or "").strip()
        self._plan_instructions_override = str(plan_instructions_prompt or "").strip()
        self._thinking_turn_instructions_override = str(
            thinking_turn_instructions_prompt or ""
        ).strip()
        normalized_thinking_mode = str(thinking_mode or "").strip().lower()
        if normalized_thinking_mode not in _THINKING_MODES:
            supported = ", ".join(sorted(_THINKING_MODES))
            raise ValueError(
                f"unsupported thinking_mode={thinking_mode!r}; expected one of: {supported}"
            )
        self.thinking_mode = normalized_thinking_mode
        self._capability_boundary_header_override = str(capability_boundary_header or "").strip()
        self._fallback_answer_override = str(fallback_answer_prompt or "").strip()
        self._transaction_resolution_base_override = str(
            transaction_resolution_base_prompt or ""
        ).strip()
        self._transaction_resolution_instructions_override = str(
            transaction_resolution_instructions_prompt or ""
        ).strip()

    def resolve_transaction(
        self,
        stimulus: Any,
        candidates: List[Any],
        *,
        dialogue_history: Optional[List[dict]] = None,
        scene_context: str = "",
    ) -> Optional[str]:
        """Return a turn-local candidate ref / durable id, or ``None`` to create."""
        use_zh = self.prompt_language.startswith("zh")
        default_base = (
            "你是 runtime 的事务归属解析器。只根据当前 conversation 中按时间顺序"
            "排列、带事务来源标签的用户可见交互，判断 CURRENT 是否明确承接某个 "
            "candidate_N；不要仅凭主题、关键词或措辞相似强行匹配，也不要检索其他 "
            "conversation 的事务。"
            if use_zh
            else (
                "You are the runtime transaction resolver. Use only the chronological, "
                "transaction-labeled, user-visible interactions from the current "
                "conversation to decide whether CURRENT clearly continues one candidate_N. "
                "Do not force a match from topic, keyword, or wording similarity alone, and "
                "do not search transactions from another conversation."
            )
        )
        default_instructions = (
            "仅当 CURRENT 明确且唯一地承接某个 candidate_N 时输出 action='continue'，"
            "并将 transaction_id 设为该 candidate_N；否则输出 action='create' 且 "
            "transaction_id=null。context_N 和 unbound 不可选择。只判断归属，不执行任务。"
            if use_zh
            else (
                "Output action='continue' with that candidate_N as transaction_id only "
                "when CURRENT clearly and uniquely continues it. Otherwise output "
                "action='create' with transaction_id=null. context_N and unbound are not "
                "selectable. Route only; do not perform the task."
            )
        )
        base = self._transaction_resolution_base_override or default_base
        instructions = (
            self._transaction_resolution_instructions_override
            or default_instructions
        )
        candidate_lines = []
        candidate_ids: Dict[str, str] = {}
        for index, item in enumerate(candidates, start=1):
            # The model only needs a turn-local selector.  Keep durable runtime
            # transaction ids on the server and map an opaque ordinal back to
            # the selected record after structured output is returned.
            if isinstance(item, dict):
                candidate_label = str(item.get("ref") or f"candidate_{index}")
                candidate_ids[candidate_label] = candidate_label
                candidate_lines.append(f"- {candidate_label}")
                continue
            candidate_label = f"candidate_{index}"
            candidate_ids[candidate_label] = getattr(
                item,
                "transaction_id",
                candidate_label,
            )
            candidate_lines.append(f"- {candidate_label}")
        routing_context = str(scene_context or "").strip()
        has_transaction_timeline = routing_context.startswith(
            "[Transaction-aware user interaction"
        )
        system_sections = [
            base,
            instructions,
            (
                "[Selectable Candidate Transactions / 可选候选事务]\n"
                + "\n".join(candidate_lines)
            ),
        ]
        if has_transaction_timeline:
            # The matcher-specific view already appends CURRENT with tx=?, so
            # do not duplicate the stimulus or inject unlabelled dialogue.
            user_context = routing_context
        else:
            user_sections = [
                "[Current Stimulus / 当前刺激]\n"
                f"kind: {stimulus.kind.value}\ntext: {stimulus.text}"
            ]
            if routing_context:
                user_sections.append(
                    "[Scene Context / 场景上下文]\n" + routing_context
                )
            user_context = "\n\n".join(user_sections)
        system_prompt = "\n\n".join(system_sections)
        try:
            raw = self.model_provider.invoke_structured(
                TransactionResolution,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_context},
                ],
                call_name="thinking.resolve_transaction",
            )
        except Exception:
            logger.exception("semantic transaction resolution failed")
            # P4: matcher exception/timeout always falls back to create.
            return None
        if isinstance(raw, dict):
            action = str(raw.get("action", "") or "").strip().lower()
            transaction_id = str(raw.get("transaction_id", "") or "").strip()
        else:
            action = str(getattr(raw, "action", "") or "").strip().lower()
            transaction_id = str(getattr(raw, "transaction_id", "") or "").strip()
        if action == "continue" and transaction_id in candidate_ids:
            return candidate_ids[transaction_id]
        return None

    # ------------------------------------------------------------------
    # Public API used by the perception layer
    # ------------------------------------------------------------------

    def handle(
        self,
        perception: PerceptionInput,
        *,
        transaction_state: Optional[Any] = None,
        event_emitter: Optional[ThinkingEventEmitter] = None,
    ) -> ThinkingDecision:
        if not isinstance(perception, PerceptionInput):
            raise TypeError("ThinkingAgent.handle expects PerceptionInput")
        if (
            not str(perception.stimulus.text or "").strip()
            and perception.activation is None
        ):
            raise ValueError(
                "PerceptionInput requires stimulus text or a typed activation"
            )

        if transaction_state is not None:
            scratch = self._scratch_for(perception.conversation_id)
            state: Any = TransactionBoundState(
                record=transaction_state,
                scratch=scratch,
            )
        else:
            state = self.state_registry.get_or_create(
                perception.conversation_id,
                thread_id=perception.thread_id,
            )
        state.turn_count += 1
        previous_completion_status = state.task_progress.completion_status
        if perception.stimulus.kind == StimulusKind.USER_MESSAGE:
            # Transaction attribution has already selected the relevant task
            # (or created a new one). A user continuation re-opens that task
            # before the preprocessor evaluates the new evidence.
            state.task_progress.completion_status = TASK_COMPLETION_PROCESSING
        turn_meta = {
            "thread_id": perception.thread_id,
            "conversation_id": perception.conversation_id,
            "turn": state.turn_count,
            "source": perception.stimulus.kind.value,
        }

        emit = self._make_safe_emitter(event_emitter)
        emit(
            "thinking_started",
            {
                "thread_id": perception.thread_id,
                "conversation_id": perception.conversation_id,
                "turn": state.turn_count,
                "source": perception.stimulus.kind.value,
            },
        )

        if self.thinking_mode == THINKING_MODE_SINGLE_CALL:
            try:
                thinking_turn = self._think_once(perception, state)
            except Exception:
                # A failed/invalid joint response must not leak the speculative
                # user-message reopen into the live transaction record.
                state.task_progress.completion_status = previous_completion_status
                raise
            task_progress_update = self._task_update_from_turn_output(
                thinking_turn.task_state,
                state,
            )
            candidate_decision = self._decision_from_turn_output(thinking_turn)
        else:
            task_progress_update = self._pre_gen_task_state(perception, state)
            candidate_decision = None

        self._apply_task_progress_update(state, task_progress_update)
        if perception.stimulus.kind == StimulusKind.USER_MESSAGE:
            # The model cannot close a request in the same turn in which that
            # user request arrived; delivery feedback owns completion proof.
            state.task_progress.completion_status = TASK_COMPLETION_PROCESSING
        self._force_processing_on_param_gap(perception, state)
        self._coerce_awaiting_user_without_reply(state)
        emit(
            "thinking_task_state",
            self._task_state_event_payload(task_progress_update, perception, state),
        )

        if candidate_decision is None:
            candidate_decision = self._legacy_decision_for_state(perception, state)
        decision = self._normalize_decision_for_state(candidate_decision, state)
        emit("thinking_plan", self._decision_event_payload(decision, perception, state))

        mode = normalize_thinking_mode(decision.mode)
        if not is_silent_mode(mode) and mode != "execute":
            decision.answer = (
                str(decision.answer or "").strip() or self._fallback_answer(perception)
            )

        if transaction_state is None:
            # Runtime-owned notes become durable only when ``commit_thought``
            # writes their stable Scene marker after the UoW succeeds. Keeping
            # a second pre-commit scratch copy here would let a failed commit
            # leak speculative memory into a later flush. Standalone/direct
            # calls still use the compatibility buffer below.
            self.episode_recorder.append(
                state.episode_buffer,
                note=decision.episode_note,
                turn_meta={**turn_meta, "phase": "plan"},
            )
        emit(
            "thinking_completed",
            {
                "thread_id": perception.thread_id,
                "conversation_id": perception.conversation_id,
                "executed": False,
                "phases": ["plan"],
            },
        )
        return decision

    @staticmethod
    def _stable_unique_strings(values: List[Any]) -> List[str]:
        normalized: List[str] = []
        seen = set()
        for value in values:
            item = str(value or "").strip()
            if not item or item in seen:
                continue
            seen.add(item)
            normalized.append(item)
        return normalized

    @classmethod
    def _task_update_from_turn_output(
        cls,
        output: TaskStateOutput,
        state: ConversationState,
    ) -> TaskProgressUpdate:
        """Project a full model snapshot while preserving durable completions."""

        current = state.task_progress
        goal = str(output.goal or "").strip()
        if not goal:
            goal = str(current.goal or "").strip()

        completed = cls._stable_unique_strings(
            [*list(current.completed), *list(output.completed)]
        )
        completed_set = set(completed)
        remaining = [
            item
            for item in cls._stable_unique_strings(list(output.remaining))
            if item not in completed_set
        ]
        return TaskProgressUpdate(
            goal=goal,
            completion_status=output.completion_status,
            completed=completed,
            remaining=remaining,
        )

    @staticmethod
    def _decision_from_turn_output(output: ThinkingTurnOutput) -> ThinkingDecision:
        decision: DecisionOutput = output.decision
        return ThinkingDecision(
            mode=decision.mode,
            tool_name=str(decision.tool_name or "").strip() or None,
            instruction=str(decision.instruction or "").strip() or None,
            answer=str(decision.answer or "").strip() or None,
            episode_note=str(decision.episode_note or "").strip() or None,
            # Filled only after all deterministic task-state corrections.
            request_complete=None,
            # ``reason`` is deliberately turn-local: do not project it into
            # ThinkingDecision, SSE, graph checkpoints, Scene, or memory.
            reasoning=None,
        )

    def _legacy_decision_for_state(
        self,
        perception: PerceptionInput,
        state: ConversationState,
    ) -> ThinkingDecision:
        """Preserve the old two-pass short-circuit behavior for rollback mode."""

        completion_status = normalize_task_completion_status(
            state.task_progress.completion_status
        )
        tx_state = getattr(state, "state", None)
        tx_state_value = (
            tx_state.value if hasattr(tx_state, "value") else str(tx_state or "")
        ).strip().lower()
        if tx_state_value == "pause":
            return ThinkingDecision(
                mode="silent",
                request_complete=False,
                reasoning="Transaction is paused waiting for user collaboration.",
            )
        if completion_status == TASK_COMPLETION_COMPLETED:
            return ThinkingDecision(
                mode="silent",
                request_complete=True,
                reasoning="Task state confirms that the complete request is fulfilled.",
            )
        if completion_status == TASK_COMPLETION_AWAITING_USER:
            return ThinkingDecision(
                mode="silent",
                request_complete=False,
                reasoning="Task state is awaiting user collaboration.",
            )
        return self._plan(perception, state)

    @staticmethod
    def _normalize_decision_for_state(
        decision: ThinkingDecision,
        state: ConversationState,
    ) -> ThinkingDecision:
        """Apply server-owned lifecycle gates after accepting joint output."""

        completion_status = normalize_task_completion_status(
            state.task_progress.completion_status
        )
        tx_state = getattr(state, "state", None)
        tx_state_value = (
            tx_state.value if hasattr(tx_state, "value") else str(tx_state or "")
        ).strip().lower()
        if (
            tx_state_value == "pause"
            or completion_status == TASK_COMPLETION_AWAITING_USER
            or completion_status == TASK_COMPLETION_COMPLETED
        ):
            return ThinkingDecision(
                mode="silent",
                request_complete=(
                    completion_status == TASK_COMPLETION_COMPLETED
                    and tx_state_value != "pause"
                ),
                episode_note=decision.episode_note,
                reasoning=decision.reasoning,
            )

        decision.mode = normalize_thinking_mode(decision.mode)
        decision.request_complete = False
        return decision

    def _scratch_for(self, conversation_id: str) -> ThinkingScratch:
        key = str(conversation_id or "").strip()
        with self._scratch_lock:
            scratch = self._scratches.get(key)
            if scratch is None:
                scratch = ThinkingScratch()
                self._scratches[key] = scratch
            return scratch

    @staticmethod
    def _make_safe_emitter(
        emitter: Optional[ThinkingEventEmitter],
    ) -> ThinkingEventEmitter:
        """Return a callable that always works (no-op when ``emitter`` is None)
        and never raises out of the handler hot path."""
        if emitter is None:
            return lambda _event_type, _payload: None

        def _safe(event_type: str, payload: Dict[str, Any]) -> None:
            try:
                emitter(event_type, payload)
            except Exception:
                logger.exception("thinking event_emitter raised for %s", event_type)

        return _safe

    @staticmethod
    def _task_state_event_payload(
        update: Optional[TaskProgressUpdate],
        perception: PerceptionInput,
        state: ConversationState,
    ) -> Dict[str, Any]:
        return {
            "thread_id": perception.thread_id,
            "conversation_id": perception.conversation_id,
            "turn": state.turn_count,
            "source": perception.stimulus.kind.value,
            "task_progress_update": update.to_dict() if update is not None else None,
            "task_progress": state.task_progress.to_dict(),
        }

    @staticmethod
    def _decision_event_payload(
        decision: ThinkingDecision,
        perception: PerceptionInput,
        state: ConversationState,
    ) -> Dict[str, Any]:
        return {
            "thread_id": perception.thread_id,
            "conversation_id": perception.conversation_id,
            "turn": state.turn_count,
            "mode": decision.mode,
            "instruction": decision.instruction,
            "answer_excerpt": (str(decision.answer or "").strip()[:160] or None),
            "reasoning": decision.reasoning,
            "tool_name": decision.tool_name,
            "request_complete": decision.request_complete,
            "episode_note": decision.episode_note,
            "task_progress": state.task_progress.to_dict(),
        }

    @staticmethod
    def _unique_episode_notes(
        notes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        unique: List[Dict[str, Any]] = []
        seen = set()
        for item in notes:
            if not isinstance(item, dict):
                continue
            try:
                identity = json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
            except Exception:
                identity = repr(item)
            if identity in seen:
                continue
            seen.add(identity)
            unique.append(deepcopy(item))
        return unique

    def snapshot_episode_notes(self, conversation_id: str) -> List[Dict[str, Any]]:
        """Return a non-destructive snapshot across Runtime and direct modes."""

        key = str(conversation_id or "").strip()
        notes: List[Dict[str, Any]] = []
        with self._scratch_lock:
            scratch = self._scratches.get(key)
            if scratch is not None:
                notes.extend(deepcopy(scratch.episode_buffer))
        state = self.state_registry.snapshot(key)
        if state is not None:
            notes.extend(deepcopy(state.episode_buffer))
        return self._unique_episode_notes(notes)

    def acknowledge_flush(
        self,
        conversation_id: str,
        *,
        thread_id: str,
    ) -> List[Dict[str, Any]]:
        """Drain flushed notes and release both state ownership paths."""

        key = str(conversation_id or "").strip()
        buffers: List[List[Dict[str, Any]]] = []
        with self._scratch_lock:
            scratch = self._scratches.pop(key, None)
            if scratch is not None:
                buffers.append(scratch.episode_buffer)
        state = self.state_registry.drop(key)
        if state is not None:
            buffers.append(state.episode_buffer)

        drained: List[Dict[str, Any]] = []
        for buffer in buffers:
            try:
                self.episode_recorder.flush(
                    buffer,
                    thread_id=thread_id,
                    conversation_id=key,
                )
            except Exception:
                logger.exception(
                    "EpisodeRecorder.flush failed for conversation_id=%s thread_id=%s",
                    key,
                    thread_id,
                )
            if isinstance(self.episode_recorder, DefaultEpisodeRecorder):
                drained.extend(DefaultEpisodeRecorder.drain(buffer))
            else:
                drained.extend(deepcopy(buffer))
                buffer.clear()
        if state is not None:
            state.wm_entries.clear()
        return self._unique_episode_notes(drained)

    def on_flush(self, conversation_id: str, *, thread_id: str) -> List[Dict[str, Any]]:
        """Compatibility alias for acknowledging a committed flush."""

        return self.acknowledge_flush(
            conversation_id,
            thread_id=thread_id,
        )

    def snapshot_conversation(self, conversation_id: str) -> Optional[ConversationState]:
        """Read-only snapshot for the ``thread_state`` API."""
        return self.state_registry.snapshot(conversation_id)

    def _render_working_memory(self, state: ConversationState) -> str:
        if self.wm_reader is None:
            return ""
        try:
            return self.wm_reader.render(
                state.wm_entries,
                language=self.prompt_language,
                task_progress=None,
            )
        except TypeError:
            return self.wm_reader.render(state.wm_entries, language=self.prompt_language)

    @staticmethod
    def _render_task_state(state: Any, *, header: str = "Task State") -> str:
        task_state = getattr(state, "task_state", None) or getattr(
            state, "task_progress", TaskProgress()
        )
        lines = [f"[{header}]", f"goal: {str(task_state.goal or '').strip() or '(empty)'}"]
        lines.append(
            "completion_status: "
            f"{normalize_task_completion_status(task_state.completion_status)}"
        )
        lines.append("completed:")
        lines.extend(f"- {item}" for item in task_state.completed) if task_state.completed else lines.append("- (none)")
        lines.append("remaining:")
        lines.extend(f"- {item}" for item in task_state.remaining) if task_state.remaining else lines.append("- (none)")
        return "\n".join(lines)

    @staticmethod
    def _render_runtime_guard_facts(state: Any) -> str:
        tx_state = getattr(state, "state", None)
        tx_state_value = (
            tx_state.value if hasattr(tx_state, "value") else str(tx_state or "")
        ).strip().lower()
        return (
            "[Deterministic Runtime Facts]\n"
            "reply_finalized_in_activation: "
            f"{str(bool(getattr(state, 'reply_finalized_in_activation', False))).lower()}\n"
            f"transaction_paused: {str(tx_state_value == 'pause').lower()}"
        )

    @staticmethod
    def _apply_task_progress_update(
        state: ConversationState,
        update: Optional[TaskProgressUpdate],
    ) -> None:
        if update is None or update.is_empty():
            return
        if update.goal is not None:
            state.task_progress.goal = str(update.goal or "").strip()
        if update.completion_status is not None:
            state.task_progress.completion_status = normalize_task_completion_status(
                update.completion_status
            )
        if update.completed is not None:
            state.task_progress.completed = [
                str(item or "").strip() for item in update.completed if str(item or "").strip()
            ]
        if update.remaining is not None:
            state.task_progress.remaining = [
                str(item or "").strip() for item in update.remaining if str(item or "").strip()
            ]

    @staticmethod
    def _is_param_gap(perception: PerceptionInput) -> bool:
        if perception.stimulus.kind != StimulusKind.EXECUTION_FEEDBACK:
            return False
        payload = perception.stimulus.payload if isinstance(
            perception.stimulus.payload, dict
        ) else {}
        history = payload.get("tool_history")
        if not isinstance(history, list) or not history:
            return False
        step = history[-1] if isinstance(history[-1], dict) else {}
        result = step.get("result") if isinstance(step.get("result"), dict) else {}
        return (
            bool(result.get("needs_clarification"))
            or str(result.get("stage", "") or "").strip() == "param_fill"
            or result.get("tool_invoked") is False
        )

    @classmethod
    def _force_processing_on_param_gap(
        cls,
        perception: PerceptionInput,
        state: ConversationState,
    ) -> None:
        """Param-fill short-circuits must not enter wait-for-user semantics."""

        if cls._is_param_gap(perception):
            state.task_progress.completion_status = TASK_COMPLETION_PROCESSING

    @staticmethod
    def _coerce_awaiting_user_without_reply(state: ConversationState) -> None:
        """Awaiting user requires a prior visible reply in this activation."""

        if (
            normalize_task_completion_status(state.task_progress.completion_status)
            != TASK_COMPLETION_AWAITING_USER
        ):
            return
        if bool(getattr(state, "reply_finalized_in_activation", False)):
            return
        state.task_progress.completion_status = TASK_COMPLETION_PROCESSING

    # ------------------------------------------------------------------
    # Joint task-state + decision pass (single LLM call)
    # ------------------------------------------------------------------

    def _validate_turn_output_against_runtime(
        self,
        output: ThinkingTurnOutput,
        perception: PerceptionInput,
        state: ConversationState,
    ) -> None:
        """Reject joint states that deterministic post-processing cannot repair."""

        status = output.task_state.completion_status
        must_be_processing = (
            perception.stimulus.kind == StimulusKind.USER_MESSAGE
            or self._is_param_gap(perception)
            or (
                status == TASK_COMPLETION_AWAITING_USER
                and not bool(
                    getattr(state, "reply_finalized_in_activation", False)
                )
            )
        )
        if (
            must_be_processing
            and status != TASK_COMPLETION_PROCESSING
            and output.decision.mode == "silent"
        ):
            # The state can be corrected deterministically, but a silent action
            # cannot be repaired into the execute/reply that legacy planning
            # would have produced after that correction.
            raise ValueError(
                "joint output becomes processing but contains an irreparable silent decision"
            )
        if output.decision.mode == "execute":
            enabled_names = getattr(
                self.execution_agent,
                "enabled_capability_names",
                None,
            )
            if enabled_names is not None:
                enabled = {
                    str(name or "").strip()
                    for name in enabled_names
                    if str(name or "").strip()
                }
                tool_name = str(output.decision.tool_name or "").strip()
                if tool_name not in enabled:
                    supported = ", ".join(sorted(enabled)) or "(none)"
                    raise ValueError(
                        f"unknown or disabled tool {tool_name!r}; enabled: {supported}"
                    )

    def _think_once(
        self,
        perception: PerceptionInput,
        state: ConversationState,
    ) -> ThinkingTurnOutput:
        prompt_messages = self._build_thinking_turn_messages(perception, state)

        def _validate(result: Any) -> ThinkingTurnOutput:
            output = (
                result
                if isinstance(result, ThinkingTurnOutput)
                else ThinkingTurnOutput.model_validate(result)
            )
            self._validate_turn_output_against_runtime(
                output,
                perception,
                state,
            )
            return output

        return self.model_provider.invoke_structured(
            ThinkingTurnOutput,
            messages=prompt_messages,
            call_name="thinking.turn",
            validator=_validate,
        )

    def _build_thinking_turn_messages(
        self,
        perception: PerceptionInput,
        state: ConversationState,
    ) -> List[Dict[str, str]]:
        sections: List[str] = []
        if self.system_prompt:
            sections.append(self.system_prompt)
        if self.persona_prompt:
            sections.append(self.persona_prompt)

        capability_block = self.execution_agent.describe_capabilities_block()
        capability_section = build_capability_boundary_block(
            capability_block,
            language=self.prompt_language,
            header_template=self._capability_boundary_header_override,
        )
        if capability_section:
            sections.append(capability_section)

        input_block = self._render_perception_input_block(perception)
        if input_block:
            sections.append(input_block)
        dialogue_block = self._render_dialogue_history_block(perception)
        if dialogue_block:
            sections.append(dialogue_block)
        if perception.scene_context:
            sections.append(f"[Scene Context]\n{perception.scene_context}")

        sections.append(
            self._render_task_state(state, header="Previous Task State")
        )
        sections.append(self._render_runtime_guard_facts(state))

        if self.wm_reader is not None:
            wm_block = self._render_working_memory(state)
            if wm_block:
                sections.append(wm_block)

        sections.append(self._thinking_turn_instructions_block())
        system_text = "\n\n".join(section for section in sections if section).strip()
        return [
            {"role": "system", "content": system_text},
            self._model_turn_message(perception),
        ]

    def _thinking_turn_instructions_block(self) -> str:
        if self._thinking_turn_instructions_override:
            return self._thinking_turn_instructions_override
        if self.prompt_language.startswith("zh"):
            return (
                "[Thinking Turn 要求]\n"
                "只输出 ThinkingTurnOutput 对应的结构化内容，并按 reason → task_state → decision 的顺序生成。\n"
                "1. reason：用一至三句话分析当前刺激、Previous Task State、Observed Evidence 与可委托能力；"
                "Scene Context 只帮助理解，不能单独证明任务完成。\n"
                "2. task_state：输出完整新快照。目标未变化时保留 goal；不得无依据删除、改写或重复 completed；"
                "remaining[0] 是当前步骤，之后的元素是后续步骤；只有可信证据能推进 completed；不要复制原始工具结果。\n"
                "新的用户请求必须保持 processing。仍需发送结果时不能 completed。仅当完整请求及必要回复均已完成时 completed。"
                "只有澄清回复已经送达且仍需用户输入时才能 awaiting_user。\n"
                "3. decision：必须依据刚生成的 task_state。execute 只选一个已启用 tool_name 并给出详细 instruction；"
                "answer_directly 给出完整 answer；silent 不执行也不回复。"
                "不要输出 request_complete；reason 不得复制到 answer 或 episode_note。\n"
                "- episode_note 只记录后续真正值得记住的一两句话，不要写原始工具结果或临时分析。\n"
                "- 多步任务每轮只委托当前一步，并以 execution feedback 的 Structured tool result/count 为准。\n"
                "- 闲聊、致谢或与可委托能力无关且需要回应的请求用 answer_directly；无需回复时用 silent。\n"
                "- 若 feedback 显示 stage=param_fill 或 tool_invoked=false，目标工具尚未执行；优先用其他已启用工具补参，无法补参再直接追问。\n"
                "- 邮件/日程必须选择对应能力；schedule_create 每次只创建一条，删除使用 schedule_delete(schedule_id)。\n"
                "字段组合必须严格：execute 的 answer 为空；answer_directly 的 tool_name/instruction 为空；"
                "silent 的 tool_name/instruction/answer 均为空。\n"
                "[输出示例：仅演示结构与状态语义]\n"
                "不得复制示例内容；实际字段必须依据当前上下文生成。\n"
                '{"reason":"这是普通知识问答，不需要外部能力；回复送达前任务仍保持 processing。",'
                '"task_state":{"goal":"向用户解释 DNS 的作用","completion_status":"processing",'
                '"completed":[],"remaining":["形成并发送简明解释"]},'
                '"decision":{"mode":"answer_directly","tool_name":null,"instruction":null,'
                '"answer":"DNS 可以理解为互联网的电话簿，它把域名转换成服务器的 IP 地址。",'
                '"episode_note":null}}'
            )
        return (
            "[Thinking Turn Requirements]\n"
            "Output only the structured ThinkingTurnOutput and generate it in reason → task_state → decision order.\n"
            "1. reason: use one to three sentences to assess the stimulus, Previous Task State, Observed Evidence, and delegated capabilities. "
            "Scene Context aids understanding but cannot prove completion by itself.\n"
            "2. task_state: emit a complete new snapshot. Preserve goal when unchanged; never delete, rewrite, or duplicate completed items without evidence; "
            "remaining[0] is the current step and later items are future steps; advance completed only from trustworthy evidence; never copy raw tool output.\n"
            "A new user request stays processing. Do not mark completed while a result still needs delivery. Use completed only when the full request and required replies are done. "
            "Use awaiting_user only after a clarification reply was delivered and user input is still required.\n"
            "3. decision: base it on the task_state just generated. execute selects exactly one enabled tool_name with a detailed instruction; "
            "answer_directly supplies the complete answer; silent neither delegates nor replies. "
            "Do not output request_complete, and never copy reason into answer or episode_note.\n"
            "- episode_note contains only one or two facts genuinely worth remembering; never raw tool output or temporary analysis.\n"
            "- Delegate only the current step of a multi-step task and trust Structured tool result/count in execution feedback.\n"
            "- Use answer_directly for small talk, thanks, or requests unrelated to delegated capabilities that need a response; use silent when no reply is needed.\n"
            "- If feedback says stage=param_fill or tool_invoked=false, the target tool did not run; prefer another enabled tool to fill the gap, then ask the user directly if still blocked.\n"
            "- Email and schedule work must select the matching capability; schedule_create creates one item per call and deletion uses schedule_delete(schedule_id).\n"
            "Field combinations are strict: execute leaves answer empty; answer_directly leaves tool_name/instruction empty; "
            "silent leaves tool_name/instruction/answer empty.\n"
            "[Output Example: structure and state semantics only]\n"
            "Do not copy the example content; derive every actual field from the current context.\n"
            '{"reason":"This is a general-knowledge question that needs no external capability; '
            'the task stays processing until the reply is delivered.",'
            '"task_state":{"goal":"Explain what DNS does","completion_status":"processing",'
            '"completed":[],"remaining":["compose and deliver a concise explanation"]},'
            '"decision":{"mode":"answer_directly","tool_name":null,"instruction":null,'
            '"answer":"DNS works like the internet\'s phone book, translating domain names into server IP addresses.",'
            '"episode_note":null}}'
        )

    # ------------------------------------------------------------------
    # Task-state pre-generation pass (LLM #1 in runtime)
    # ------------------------------------------------------------------

    def _pre_gen_task_state(
        self,
        perception: PerceptionInput,
        state: ConversationState,
    ) -> Optional[TaskProgressUpdate]:
        prompt_messages = self._build_task_state_messages(perception, state)
        result = self.model_provider.invoke_structured(
            TaskProgressUpdate,
            messages=prompt_messages,
            call_name="thinking.pre_gen_task_state",
        )
        return self._coerce_task_progress_update(result)

    def _build_task_state_messages(
        self,
        perception: PerceptionInput,
        state: ConversationState,
    ) -> List[Dict[str, str]]:
        sections: List[str] = []
        base = self._task_state_base_block()
        if base:
            sections.append(base)
        instructions = self._task_state_instructions_block()
        if instructions:
            sections.append(instructions)
        input_block = self._render_perception_input_block(perception)
        if input_block:
            sections.append(input_block)
        dialogue_block = self._render_dialogue_history_block(perception)
        if dialogue_block:
            sections.append(dialogue_block)
        if perception.scene_context:
            sections.append(f"[Scene Context]\n{perception.scene_context}")

        sections.append(self._render_task_state(state))

        if self.wm_reader is not None:
            wm_block = self._render_working_memory(state)
            if wm_block:
                sections.append(wm_block)

        system_text = "\n\n".join(section for section in sections if section).strip()
        messages: List[Dict[str, str]] = [{"role": "system", "content": system_text}]
        messages.append(self._model_turn_message(perception))
        return messages

    def _task_state_base_block(self) -> str:
        if self._task_state_base_override:
            return self._task_state_base_override
        if self.prompt_language == "zh":
            return (
                "[任务状态预处理]\n"
                "你是 runtime 思考层的任务状态预处理器。你的工作是先阅读当前输入、隐藏运行时上下文、"
                "场景片段和工作记忆，然后只更新可读的任务进度状态。不要决定是否调用工具，不要回复用户。"
            )
        return (
            "[Task-State Preprocessor]\n"
            "You are the task-state preprocessor for the runtime thinking layer. Read the current stimulus, "
            "dialogue history, conversation scene, and selected transaction working memory, then update only "
            "that transaction's readable task state. Do not choose tools and do not reply to the user."
        )

    def _task_state_instructions_block(self) -> str:
        if self._task_state_instructions_override:
            return self._task_state_instructions_override
        if self.prompt_language == "zh":
            return (
                "[输出字段]\n"
                "- goal: 可选；用户当前整体任务，保持稳定，除非任务真的变化。\n"
                "- completion_status: 只能是 processing、awaiting_user 或 completed。\n"
                "- completed: 可选；已经由对话或执行反馈确认完成的步骤。省略表示不变，空数组表示清空。\n"
                "- remaining: 可选；仍未完成的步骤。省略表示不变，空数组表示已无剩余步骤。\n"
                "[约束]\n"
                "- 只输出任务状态更新，不要输出 mode/tool_name/instruction/answer。\n"
                "- 不要把原始工具结果复制进状态；只写短、可读、可执行的任务步骤。\n"
                "- 新的用户请求必须是 processing，即使它可以直接回答。\n"
                "- [Activation Event] 只说明本轮为何被激活，不代表激活后的工作已经完成。\n"
                "- [Current Objective] 是仍待评估或执行的目标，即使它的表面措辞像完成态。\n"
                "- 只有 [Observed Evidence] 可以证明下游工作完成；[Scene Context] 仅供理解。\n"
                "- 只有当目标本身明确是观察某个事件时，该激活事件才可直接满足目标。\n"
                "- 当前刺激为 execution_feedback 时，根据可读反馈和证据更新 completion_status/completed/remaining。\n"
                "- 工具结果尚需告知用户、参数缺口可改用其他工具补参时，一律保持 processing。\n"
                "- 澄清问题已通过 reply 发给用户、仍需用户协作时，completion_status 用 awaiting_user（宏观决策）；运行时会 pause，本轮不再做动作规划。\n"
                "- 只有整个请求及必要的用户回复均已完成时，才使用 completed。"
            )
        return (
            "[Output Fields]\n"
            "- goal: optional; the user's overall current task. Keep stable unless the task really changes.\n"
            "- completion_status: processing, awaiting_user, or completed.\n"
            "- completed: optional; steps confirmed complete by dialogue or execution feedback. Omit to keep unchanged; [] clears it.\n"
            "- remaining: optional; unfinished steps. Omit to keep unchanged; [] means no remaining steps.\n"
            "[Constraints]\n"
            "- Output only task-state updates; do not output mode/tool_name/instruction/answer.\n"
            "- Do not copy raw tool results into state; write short, readable, actionable steps.\n"
            "- A new user request is processing even when it can be answered directly.\n"
            "- [Activation Event] states why this turn exists; it is not an execution result.\n"
            "- [Current Objective] is work to assess or carry out, even when its wording sounds result-like.\n"
            "- Only [Observed Evidence] may prove downstream work complete; [Scene Context] is context only.\n"
            "- An activation event may satisfy an objective only when that objective is explicitly to observe that event.\n"
            "- For execution_feedback, update completion_status/completed/remaining from readable feedback and visible evidence.\n"
            "- Keep processing when tool results still need a user reply, or when a param gap can be filled by another tool.\n"
            "- After a clarification reply is delivered and you still need the user, set completion_status=awaiting_user (macro decision); runtime will pause and skip action planning this turn.\n"
            "- Mark completed only after the full request, including required user communication, is fulfilled."
        )

    @staticmethod
    def _truncate_prompt_value(value: Any, limit: int = 2000) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."

    def _render_perception_input_block(self, perception: PerceptionInput) -> str:
        activation = perception.activation
        if activation is not None:
            event = activation.event
            lines = [
                "[Current Stimulus]",
                f"kind: {perception.stimulus.kind.value}",
                "semantic_role: runtime_activation",
                "",
                "[Activation Event]",
                "role: activation_event",
                f"type: {str(event.event_type or '').strip() or '(unknown)'}",
                f"source: {str(event.source or '').strip() or '(unknown)'}",
                f"occurred_at: {str(event.occurred_at or '').strip() or '(unknown)'}",
                "facts:",
                self._safe_prompt_json(event.facts, limit=2000) or "{}",
                "",
                "[Current Objective]",
            ]
            if activation.objective is None:
                lines.append("(none)")
            else:
                lines.extend(
                    [
                        "role: deferred_objective",
                        f"encoding: {activation.objective.encoding}",
                        "description:",
                        self._truncate_prompt_value(
                            activation.objective.description,
                            2000,
                        )
                        or "(empty)",
                    ]
                )
            lines.extend(["", "[Observed Evidence]"])
            if not activation.evidence:
                lines.append("(none)")
            else:
                for index, item in enumerate(activation.evidence, start=1):
                    lines.extend(
                        [
                            f"- evidence_{index}:",
                            f"  type: {item.evidence_type}",
                            f"  source: {item.source}",
                            f"  summary: {self._truncate_prompt_value(item.summary, 800) or '(empty)'}",
                            "  facts: "
                            + self._safe_prompt_json(item.facts, limit=1200),
                        ]
                    )
            return "\n".join(lines).strip()
        if perception.stimulus.kind == StimulusKind.USER_MESSAGE:
            # The utterance itself is supplied exactly once as the current
            # user-role message. Repeating it in the system prompt biases the
            # model and makes context-budget accounting incorrect.
            return "\n".join(
                [
                    "[Current Stimulus]",
                    f"kind: {perception.stimulus.kind.value}",
                    "semantic_role: user_utterance",
                    "content_source: current_user_message",
                ]
            )
        lines = [
            "[Current Stimulus]",
            f"kind: {perception.stimulus.kind.value}",
            "semantic_role: runtime_event",
            "text:",
            self._truncate_prompt_value(perception.stimulus.text, 2000) or "(empty)",
        ]
        return "\n".join(lines).strip()

    @staticmethod
    def _model_turn_message(perception: PerceptionInput) -> Dict[str, str]:
        if perception.stimulus.kind == StimulusKind.USER_MESSAGE:
            return {
                "role": "user",
                "content": str(perception.stimulus.text or "").strip(),
            }
        return {
            "role": "user",
            "content": (
                "[Runtime semantic input — not a user utterance]\n"
                "Process the typed event, objective, evidence, and context "
                "provided above according to their declared roles."
            ),
        }

    def _render_dialogue_history_block(self, perception: PerceptionInput) -> str:
        history = list(perception.dialogue_history or [])
        lines = ["[Dialogue History]"]
        if history:
            for item in history[-6:]:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role", "") or "").strip() or "unknown"
                content = self._truncate_prompt_value(item.get("content", ""), 500)
                if content:
                    lines.append(f"- {role}: {content}")
        return "\n".join(lines).strip() if len(lines) > 1 else ""

    @staticmethod
    def _safe_prompt_json(value: Any, *, limit: int) -> str:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        except Exception:
            text = str(value or "")
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."

    # ------------------------------------------------------------------
    # Decision pass (LLM #2 in runtime)
    # ------------------------------------------------------------------

    def _plan(self, perception: PerceptionInput, state: ConversationState) -> ThinkingDecision:
        prompt_messages = self._build_plan_messages(perception, state)
        result = self.model_provider.invoke_structured(
            ThinkingDecision,
            messages=prompt_messages,
            call_name="thinking.plan",
        )
        return self._coerce_decision(result)

    def _build_plan_messages(
        self,
        perception: PerceptionInput,
        state: ConversationState,
    ) -> List[Dict[str, str]]:
        sections: List[str] = []
        if self.system_prompt:
            sections.append(self.system_prompt)
        if self.persona_prompt:
            sections.append(self.persona_prompt)

        capability_block = self.execution_agent.describe_capabilities_block()
        capability_section = build_capability_boundary_block(
            capability_block,
            language=self.prompt_language,
            header_template=self._capability_boundary_header_override,
        )
        if capability_section:
            sections.append(capability_section)

        sections.append(self._plan_instructions_block())

        input_block = self._render_perception_input_block(perception)
        if input_block:
            sections.append(input_block)

        dialogue_block = self._render_dialogue_history_block(perception)
        if dialogue_block:
            sections.append(dialogue_block)
        if perception.scene_context:
            sections.append(f"[Scene Context]\n{perception.scene_context}")

        sections.append(self._render_task_state(state))

        if self.wm_reader is not None:
            wm_block = self._render_working_memory(state)
            if wm_block:
                sections.append(wm_block)

        system_text = "\n\n".join(section for section in sections if section).strip()
        messages: List[Dict[str, str]] = [{"role": "system", "content": system_text}]
        messages.append(self._model_turn_message(perception))
        return messages

    def _plan_instructions_block(self) -> str:
        if self._plan_instructions_override:
            return self._plan_instructions_override
        if self.prompt_language == "zh":
            return (
                "[规划要求]\n"
                "请按以下结构化字段输出本轮的决策：\n"
                "- mode: 仅可填 \"execute\"、\"answer_directly\" 或 \"silent\"（动作决策，不写 transaction 生命周期）。\n"
                "- instruction: 当 mode==execute 时，写一条自然语言指令交给执行层；否则留空或填 null。\n"
                "- answer: 当 mode==answer_directly 时直接给出最终回复；silent/execute 时留空或填 null。\n"
                "- episode_note: 可选；写下你认为以后值得记住的一两句话，不要把工具结果原样塞进去。\n"
                "- tool_name: 当 mode==execute 时必填，且只能填一个已启用能力名（本轮只执行这一个工具）。\n"
                "- request_complete: 由任务状态决定；仅当 completion_status==completed 时为 true。\n"
                "- reasoning: 可选；简要说明本轮选择 mode 的理由，便于审计。\n"
                "[硬约束]\n"
                "- 对运行时激活，以 [Current Objective] 为行动目标；不要把 [Activation Event] 当成执行结果，也不要把 [Scene Context] 当成 [Observed Evidence]。\n"
                "- 你本身没有工具权限，所有外部动作只能通过 execute 委托，且每轮最多一个 tool_name。\n"
                "- 多步任务：以 feedback 中 Structured tool result 的 count 为准；未完成时 request_complete=false。\n"
                "- 当 mode==execute 时，不要在 answer 中给出最终回复，让执行层先工作。\n"
                "- completion_status==processing 时 request_complete=false；answer_directly 后等待 execution_feedback。\n"
                "- 等用户协作由宏观任务状态 awaiting_user 表达，不在 mode 里表达 pause。\n"
                "- completion_status==completed 时用 silent 且 request_complete=true，不要继续行动。\n"
                "- 闲聊、致谢、与可委托能力无关的请求，用 answer_directly；纯附和/无需回复时用 silent。\n"
                "- mode==silent：不 delegate、不 reply；仅记录 reasoning/episode_note，等待后续刺激。\n"
                "- 不要在指令中重复用户原话，要写明你希望执行层做什么。"
            )
        return (
            "[Planning Requirements]\n"
            "Emit the structured decision for this turn:\n"
            "- mode: must be \"execute\", \"answer_directly\", or \"silent\" (action decision only; not transaction lifecycle).\n"
            "- instruction: required when mode==execute; a single natural-language directive for the execution layer.\n"
            "- answer: required when mode==answer_directly; leave empty for silent/execute.\n"
            "- episode_note: optional short text worth remembering; do not dump raw tool output here.\n"
            "- tool_name: required when mode==execute; exactly one enabled capability (one tool this round).\n"
            "- request_complete: derived from task state; true only when completion_status==completed.\n"
            "- reasoning: optional short rationale for the chosen mode (for auditing).\n"
            "[Hard Constraints]\n"
            "- You hold no tools yourself; delegate via execute with at most one tool_name per round.\n"
            "- Multi-step tasks: trust Structured tool result count on feedback; request_complete=false until done.\n"
            "- Read [Current Stimulus], [Scene Context], [Task State], and [Working Memory] before deciding.\n"
            "- For runtime activations, act on [Current Objective]; do not treat [Activation Event] as an executed outcome or [Scene Context] as [Observed Evidence].\n"
            "- When mode==execute, leave answer empty and let the execution layer work first.\n"
            "- When completion_status==processing, request_complete=false; answer_directly must be followed by execution feedback.\n"
            "- Waiting for the user is expressed by macro task status awaiting_user, not by a pause mode.\n"
            "- When completion_status==completed, use silent with request_complete=true and take no further action.\n"
            "- For small talk or delegable-unrelated requests, choose answer_directly; use silent for acks that need no reply.\n"
            "- mode==silent: no delegate, no reply; record reasoning/episode_note and wait for further stimulus.\n"
            "- Don't echo the user; in the instruction state explicitly what you want the execution layer to do."
        )

    # ------------------------------------------------------------------
    # Result coercion / fallbacks
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_string_list(value: Any) -> Optional[List[str]]:
        if value is None:
            return None
        if not isinstance(value, list):
            return None
        return [str(item or "").strip() for item in value if str(item or "").strip()]

    @classmethod
    def _coerce_task_progress_update(cls, raw: Any) -> Optional[TaskProgressUpdate]:
        if raw is None:
            return None
        if isinstance(raw, TaskProgressUpdate):
            return raw if not raw.is_empty() else None
        if isinstance(raw, TaskProgress):
            update = TaskProgressUpdate(
                goal=raw.goal,
                completion_status=raw.completion_status,
                completed=list(raw.completed),
                remaining=list(raw.remaining),
            )
            return update if not update.is_empty() else None
        if isinstance(raw, dict):
            update = TaskProgressUpdate(
                goal=str(raw.get("goal", "") or "").strip() if "goal" in raw else None,
                completion_status=(
                    normalize_task_completion_status(raw.get("completion_status"))
                    if "completion_status" in raw
                    else None
                ),
                completed=cls._coerce_string_list(raw.get("completed")) if "completed" in raw else None,
                remaining=cls._coerce_string_list(raw.get("remaining")) if "remaining" in raw else None,
            )
            return update if not update.is_empty() else None
        if any(
            hasattr(raw, name)
            for name in ("goal", "completion_status", "completed", "remaining")
        ):
            update = TaskProgressUpdate(
                goal=(
                    str(getattr(raw, "goal", "") or "").strip()
                    if hasattr(raw, "goal")
                    else None
                ),
                completion_status=(
                    normalize_task_completion_status(
                        getattr(raw, "completion_status", None)
                    )
                    if hasattr(raw, "completion_status")
                    else None
                ),
                completed=(
                    cls._coerce_string_list(getattr(raw, "completed", None))
                    if hasattr(raw, "completed")
                    else None
                ),
                remaining=(
                    cls._coerce_string_list(getattr(raw, "remaining", None))
                    if hasattr(raw, "remaining")
                    else None
                ),
            )
            return update if not update.is_empty() else None
        return None

    @classmethod
    def _coerce_decision(cls, raw: Any) -> ThinkingDecision:
        if isinstance(raw, ThinkingDecision):
            return raw
        if isinstance(raw, dict):
            return ThinkingDecision(
                mode=normalize_thinking_mode(raw.get("mode", "answer_directly")),
                tool_name=raw.get("tool_name"),
                instruction=raw.get("instruction"),
                answer=raw.get("answer"),
                episode_note=raw.get("episode_note"),
                request_complete=raw.get("request_complete"),
                reasoning=raw.get("reasoning"),
            )
        if hasattr(raw, "mode"):
            return ThinkingDecision(
                mode=normalize_thinking_mode(getattr(raw, "mode", "answer_directly")),
                tool_name=getattr(raw, "tool_name", None),
                instruction=getattr(raw, "instruction", None),
                answer=getattr(raw, "answer", None),
                episode_note=getattr(raw, "episode_note", None),
                request_complete=getattr(raw, "request_complete", None),
                reasoning=getattr(raw, "reasoning", None),
            )
        # Fallback: treat as direct answer string.
        return ThinkingDecision(
            mode="answer_directly",
            answer=str(raw or "").strip() or None,
        )

    def _fallback_answer(self, perception: PerceptionInput) -> str:
        if self._fallback_answer_override:
            return self._fallback_answer_override
        if self.prompt_language == "zh":
            return "我已经收到你的请求，但暂时没有更多可以补充的内容。"
        return "I received your message; there is nothing further I can add right now."
