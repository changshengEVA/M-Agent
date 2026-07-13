"""ThinkingAgent: persona-owning planning + summarizing layer (Form A).

Flow per turn::

    PerceptionInput
        │
        ▼  plan_call (LLM #1, structured -> ThinkingDecision)
        │
        ├─── mode == "answer_directly" ──► return answer  (1 LLM call total)
        │
        └─── mode == "execute"
                │
                ▼ ExecutionAgent.execute(NL instruction)
                │
                ▼ WMWriter.write(wm_entries, execution.tool_history)
                │
                ▼ summarize_call (LLM #2, structured -> ThinkingSummary)
                │
                ▼ return answer  (2 LLM calls + 1 execution call total)

After each LLM pass, the optional ``episode_note`` is appended to the
per-conversation episode buffer via :class:`EpisodeRecorder`. The thinking
layer never invokes external tools directly — capability use is always
mediated through the execution layer.
"""
from __future__ import annotations

import logging
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from m_agent.layers.execution.contracts import ExecutionRequest, ExecutionResult
from m_agent.layers.execution.core import ExecutionAgent
from m_agent.layers.execution.model_provider import ModelProvider


#: Callback signature for streaming intermediate thinking-layer + execution-layer
#: state out to the perception layer (which forwards to SSE). Implementations
#: should be cheap and side-effect-only; they must not raise.
ThinkingEventEmitter = Callable[[str, Dict[str, Any]], None]
from m_agent.systems.episodic import DefaultEpisodeRecorder, EpisodeRecorder
from m_agent.systems.wm import WMReader, WMWriter
from m_agent.layers.perception.contracts import PerceptionInput
from m_agent.layers.thinking.persona import (
    build_capability_boundary_block,
)
from m_agent.layers.thinking.contracts import (
    TaskProgress,
    TaskProgressUpdate,
    TransactionResolution,
    ThinkingDecision,
    ThinkingSummary,
    is_execute_mode,
    is_silent_mode,
    normalize_thinking_mode,
)
from m_agent.layers.thinking.state import ConversationState, ConversationStateRegistry
from m_agent.utils.api_error_utils import is_network_api_error


logger = logging.getLogger(__name__)


@dataclass
class ThinkingTurnResult:
    """Aggregate outcome of a single thinking-layer turn.

    Returned to the perception layer (``ChatServiceRuntime``). It carries the
    final user-facing answer plus enough metadata to populate the legacy
    ``agent_result`` shape so the rest of the API surface keeps working.
    """

    answer: str
    conversation_id: str
    decision: ThinkingDecision
    execution_result: Optional[ExecutionResult] = None
    summary: Optional[ThinkingSummary] = None
    wm_entries_snapshot: List[Dict[str, Any]] = field(default_factory=list)


class ThinkingAgent:
    """Persona-owning planning + summarizing agent (Form A two-call flow)."""

    def __init__(
        self,
        *,
        execution_agent: ExecutionAgent,
        model_provider: ModelProvider,
        system_prompt: str,
        persona_prompt: str = "",
        wm_reader: Optional[WMReader] = None,
        wm_writer: Optional[WMWriter] = None,
        episode_recorder: Optional[EpisodeRecorder] = None,
        state_registry: Optional[ConversationStateRegistry] = None,
        prompt_language: str = "zh",
        max_executions_per_turn: int = 1,
        skip_summarize_on_direct_answer: bool = True,
        task_state_base_prompt: str = "",
        task_state_instructions_prompt: str = "",
        plan_instructions_prompt: str = "",
        summarize_instructions_prompt: str = "",
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
        self.wm_writer = wm_writer
        self.episode_recorder: EpisodeRecorder = episode_recorder or DefaultEpisodeRecorder()
        self.state_registry = state_registry or ConversationStateRegistry()
        self.prompt_language = str(prompt_language or "zh").strip().lower() or "zh"
        self.max_executions_per_turn = max(0, int(max_executions_per_turn))
        self.skip_summarize_on_direct_answer = bool(skip_summarize_on_direct_answer)

        # YAML-overridable prompt fragments. Empty/None means "use built-in default".
        self._task_state_base_override = str(task_state_base_prompt or "").strip()
        self._task_state_instructions_override = str(task_state_instructions_prompt or "").strip()
        self._plan_instructions_override = str(plan_instructions_prompt or "").strip()
        self._summarize_instructions_override = str(summarize_instructions_prompt or "").strip()
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
        """Return an existing transaction id, or ``None`` to create a new one."""
        if not candidates:
            return None
        base = self._transaction_resolution_base_override or (
            "You route one incoming stimulus to an existing task transaction only when it "
            "clearly continues that task. Otherwise choose create."
        )
        instructions = self._transaction_resolution_instructions_override or (
            "Output action='continue' with one listed transaction_id, or action='create' "
            "with transaction_id=null. Do not merge unrelated tasks."
        )
        candidate_lines = []
        for record in candidates:
            state = record.task_state
            candidate_lines.append(
                f"- {record.transaction_id}: status={record.status.value}; "
                f"goal={state.goal or '(empty)'}; remaining={state.remaining}"
            )
        sections = [
                base,
                instructions,
                f"[Current Stimulus]\nkind: {stimulus.kind.value}\ntext: {stimulus.text}",
                "[Candidate Transactions]\n" + "\n".join(candidate_lines),
        ]
        history = list(dialogue_history or [])[-6:]
        if history:
            lines = ["[Dialogue History]"]
            for item in history:
                if isinstance(item, dict):
                    lines.append(
                        f"- {str(item.get('role', '') or 'unknown')}: "
                        f"{self._truncate_prompt_value(item.get('content', ''), 500)}"
                    )
            sections.append("\n".join(lines))
        if str(scene_context or "").strip():
            sections.append(f"[Scene Context]\n{str(scene_context).strip()}")
        prompt = "\n\n".join(sections)
        try:
            model = self.model_provider.model.with_structured_output(
                TransactionResolution, include_raw=False
            )
        except Exception:
            model = self.model_provider.model.with_structured_output(TransactionResolution)
        try:
            raw = self._invoke_structured(
                model,
                messages=[{"role": "system", "content": prompt}],
                call_name="thinking.resolve_transaction",
            )
        except Exception:
            logger.exception("semantic transaction resolution failed")
            return candidates[-1].transaction_id if len(candidates) == 1 else None
        if isinstance(raw, dict):
            action = str(raw.get("action", "") or "").strip().lower()
            transaction_id = str(raw.get("transaction_id", "") or "").strip()
        else:
            action = str(getattr(raw, "action", "") or "").strip().lower()
            transaction_id = str(getattr(raw, "transaction_id", "") or "").strip()
        valid_ids = {record.transaction_id for record in candidates}
        if action == "continue" and transaction_id in valid_ids:
            return transaction_id
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
    ) -> ThinkingTurnResult:
        if not isinstance(perception, PerceptionInput):
            raise TypeError("ThinkingAgent.handle expects PerceptionInput")
        if not str(perception.stimulus.text or "").strip():
            raise ValueError("PerceptionInput.stimulus.text must be a non-empty string")

        state = transaction_state
        if state is None:
            state = self.state_registry.get_or_create(
                perception.conversation_id,
                thread_id=perception.thread_id,
            )
        state.turn_count += 1
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

        task_progress_update = self._pre_gen_task_state(perception, state)
        self._apply_task_progress_update(state, task_progress_update)
        emit(
            "thinking_task_state",
            self._task_state_event_payload(task_progress_update, perception, state),
        )

        decision = self._plan(perception, state)
        emit("thinking_plan", self._decision_event_payload(decision, perception, state))

        if not is_execute_mode(decision.mode) or self.max_executions_per_turn <= 0:
            if is_silent_mode(decision.mode):
                answer = ""
            else:
                answer = str(decision.answer or "").strip()
                if not answer:
                    answer = self._fallback_answer(perception)
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
            return ThinkingTurnResult(
                answer=answer,
                conversation_id=perception.conversation_id,
                decision=decision,
                wm_entries_snapshot=list(state.wm_entries),
            )

        instruction = str(decision.instruction or "").strip()
        if not instruction:
            logger.warning(
                "ThinkingDecision.mode=='execute' but instruction is empty; falling back to direct answer"
            )
            answer = str(decision.answer or self._fallback_answer(perception)).strip()
            emit(
                "thinking_completed",
                {
                    "thread_id": perception.thread_id,
                    "conversation_id": perception.conversation_id,
                    "executed": False,
                    "phases": ["plan"],
                    "reason": "execute_with_empty_instruction",
                },
            )
            return ThinkingTurnResult(
                answer=answer,
                conversation_id=perception.conversation_id,
                decision=decision,
                wm_entries_snapshot=list(state.wm_entries),
            )

        # Record the plan-phase episode note before executing so the summarize
        # pass can see the "what I intended to do" log in the buffer.
        self.episode_recorder.append(
            state.episode_buffer,
            note=decision.episode_note,
            turn_meta={**turn_meta, "phase": "plan"},
        )

        emit(
            "execution_started",
            {
                "thread_id": perception.thread_id,
                "conversation_id": perception.conversation_id,
                "instruction": instruction,
                "capability_hint": list(decision.capability_hint or []),
            },
        )
        execution_result = self._execute(
            decision=decision,
            perception=perception,
            state=state,
        )
        emit("execution_completed", self._execution_event_payload(execution_result, perception))

        summary = self._summarize(
            perception=perception,
            decision=decision,
            execution_result=execution_result,
            state=state,
        )
        self._apply_task_progress_update(state, summary.task_progress_update)
        emit("thinking_summary", self._summary_event_payload(summary, perception))

        self.episode_recorder.append(
            state.episode_buffer,
            note=summary.episode_note,
            turn_meta={**turn_meta, "phase": "summarize"},
        )

        emit(
            "thinking_completed",
            {
                "thread_id": perception.thread_id,
                "conversation_id": perception.conversation_id,
                "executed": True,
                "phases": ["plan", "execute", "summarize"],
            },
        )

        return ThinkingTurnResult(
            answer=str(summary.answer or "").strip() or execution_result.summary,
            conversation_id=perception.conversation_id,
            decision=decision,
            execution_result=execution_result,
            summary=summary,
            wm_entries_snapshot=list(state.wm_entries),
        )

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
            "capability_hint": list(decision.capability_hint or []),
            "episode_note": decision.episode_note,
            "task_progress": state.task_progress.to_dict(),
        }

    @staticmethod
    def _execution_event_payload(
        execution_result: ExecutionResult,
        perception: PerceptionInput,
    ) -> Dict[str, Any]:
        return {
            "thread_id": perception.thread_id,
            "conversation_id": perception.conversation_id,
            "summary_excerpt": str(execution_result.summary or "")[:240],
            "tool_call_count": execution_result.tool_call_count,
            "tool_names": list(execution_result.tool_names),
            "insufficient": execution_result.insufficient,
            "limit_reached": execution_result.limit_reached,
            "success": execution_result.success,
        }

    @staticmethod
    def _summary_event_payload(
        summary: ThinkingSummary,
        perception: PerceptionInput,
    ) -> Dict[str, Any]:
        return {
            "thread_id": perception.thread_id,
            "conversation_id": perception.conversation_id,
            "answer_excerpt": str(summary.answer or "")[:240],
            "episode_note": summary.episode_note,
            "task_progress_update": (
                summary.task_progress_update.to_dict()
                if summary.task_progress_update is not None
                else None
            ),
        }

    def on_flush(self, conversation_id: str, *, thread_id: str) -> List[Dict[str, Any]]:
        """Drop the conversation state and return drained episode buffer + WM entries.

        Returns a flat list of episode-note records the caller can inline into
        the persistence pipeline. The state is removed from the registry so
        the next perception turn starts a fresh conversation.
        """
        state = self.state_registry.drop(conversation_id)
        if state is None:
            return []
        try:
            self.episode_recorder.flush(
                state.episode_buffer,
                thread_id=thread_id,
                conversation_id=conversation_id,
            )
        except Exception:
            logger.exception(
                "EpisodeRecorder.flush failed for conversation_id=%s thread_id=%s",
                conversation_id,
                thread_id,
            )
        if isinstance(self.episode_recorder, DefaultEpisodeRecorder):
            drained = DefaultEpisodeRecorder.drain(state.episode_buffer)
        else:
            drained = list(state.episode_buffer)
            state.episode_buffer.clear()
        state.wm_entries.clear()
        return drained

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
    def _render_task_state(state: Any) -> str:
        task_state = getattr(state, "task_state", None) or getattr(
            state, "task_progress", TaskProgress()
        )
        lines = ["[Task State]", f"goal: {str(task_state.goal or '').strip() or '(empty)'}"]
        lines.append("completed:")
        lines.extend(f"- {item}" for item in task_state.completed) if task_state.completed else lines.append("- (none)")
        lines.append("remaining:")
        lines.extend(f"- {item}" for item in task_state.remaining) if task_state.remaining else lines.append("- (none)")
        return "\n".join(lines)

    @staticmethod
    def _apply_task_progress_update(
        state: ConversationState,
        update: Optional[TaskProgressUpdate],
    ) -> None:
        if update is None or update.is_empty():
            return
        if update.goal is not None:
            state.task_progress.goal = str(update.goal or "").strip()
        if update.completed is not None:
            state.task_progress.completed = [
                str(item or "").strip() for item in update.completed if str(item or "").strip()
            ]
        if update.remaining is not None:
            state.task_progress.remaining = [
                str(item or "").strip() for item in update.remaining if str(item or "").strip()
            ]

    # ------------------------------------------------------------------
    # Task-state pre-generation pass (LLM #1 in think-life)
    # ------------------------------------------------------------------

    def _pre_gen_task_state(
        self,
        perception: PerceptionInput,
        state: ConversationState,
    ) -> Optional[TaskProgressUpdate]:
        prompt_messages = self._build_task_state_messages(perception, state)
        try:
            structured_model = self.model_provider.model.with_structured_output(
                TaskProgressUpdate,
                include_raw=False,
            )
        except Exception:
            structured_model = self.model_provider.model.with_structured_output(TaskProgressUpdate)

        result = self._invoke_structured(
            structured_model,
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
        messages.append({"role": "user", "content": str(perception.stimulus.text or "").strip()})
        return messages

    def _task_state_base_block(self) -> str:
        if self._task_state_base_override:
            return self._task_state_base_override
        if self.prompt_language == "zh":
            return (
                "[任务状态预处理]\n"
                "你是 think-life 思考层的任务状态预处理器。你的工作是先阅读当前输入、隐藏运行时上下文、"
                "场景片段和工作记忆，然后只更新可读的任务进度状态。不要决定是否调用工具，不要回复用户。"
            )
        return (
            "[Task-State Preprocessor]\n"
            "You are the task-state preprocessor for the think-life thinking layer. Read the current stimulus, "
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
                "- completed: 可选；已经由对话或执行反馈确认完成的步骤。省略表示不变，空数组表示清空。\n"
                "- remaining: 可选；仍未完成的步骤。省略表示不变，空数组表示已无剩余步骤。\n"
                "[约束]\n"
                "- 只输出任务状态更新，不要输出 mode/tool_name/instruction/answer。\n"
                "- 不要把原始工具结果复制进状态；只写短、可读、可执行的任务步骤。\n"
                "- 当前刺激为 execution_feedback 时，优先根据可读反馈和可见工具证据更新 completed/remaining。"
            )
        return (
            "[Output Fields]\n"
            "- goal: optional; the user's overall current task. Keep stable unless the task really changes.\n"
            "- completed: optional; steps confirmed complete by dialogue or execution feedback. Omit to keep unchanged; [] clears it.\n"
            "- remaining: optional; unfinished steps. Omit to keep unchanged; [] means no remaining steps.\n"
            "[Constraints]\n"
            "- Output only task-state updates; do not output mode/tool_name/instruction/answer.\n"
            "- Do not copy raw tool results into state; write short, readable, actionable steps.\n"
            "- For execution_feedback, update completed/remaining from readable feedback and visible tool evidence."
        )

    @staticmethod
    def _truncate_prompt_value(value: Any, limit: int = 2000) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."

    def _render_perception_input_block(self, perception: PerceptionInput) -> str:
        lines = [
            "[Current Stimulus]",
            f"kind: {perception.stimulus.kind.value}",
            f"thread_id: {perception.thread_id}",
            f"conversation_id: {perception.conversation_id}",
            f"transaction_id: {perception.transaction_id or '(unresolved)'}",
            "text:",
            self._truncate_prompt_value(perception.stimulus.text, 2000) or "(empty)",
        ]
        return "\n".join(lines).strip()

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
    # Decision pass (LLM #2 in think-life)
    # ------------------------------------------------------------------

    def _plan(self, perception: PerceptionInput, state: ConversationState) -> ThinkingDecision:
        prompt_messages = self._build_plan_messages(perception, state)
        try:
            structured_model = self.model_provider.model.with_structured_output(
                ThinkingDecision,
                include_raw=False,
            )
        except Exception:
            # Fallback: try the OpenAI-style structured tools schema name
            structured_model = self.model_provider.model.with_structured_output(ThinkingDecision)

        result = self._invoke_structured(
            structured_model,
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
        messages.append({"role": "user", "content": str(perception.stimulus.text or "").strip()})
        return messages

    def _plan_instructions_block(self) -> str:
        if self._plan_instructions_override:
            return self._plan_instructions_override
        if self.prompt_language == "zh":
            return (
                "[规划要求]\n"
                "请按以下结构化字段输出本轮的决策：\n"
                "- mode: 仅可填 \"execute\"、\"answer_directly\" 或 \"silent\"。\n"
                "- instruction: 当 mode==execute 时，写一条自然语言指令交给执行层；否则留空或填 null。\n"
                "- answer: 当 mode==answer_directly 时直接给出最终回复；silent/execute 时留空或填 null。\n"
                "- episode_note: 可选；写下你认为以后值得记住的一两句话，不要把工具结果原样塞进去。\n"
                "- tool_name: 当 mode==execute 时必填，且只能填一个已启用能力名（本轮只执行这一个工具）。\n"
                "- request_complete: 仅当用户原始请求已全部完成时填 true；否则 false 并继续 execute。\n"
                "- capability_hint: 可选；已废弃，请优先使用 tool_name。\n"
                "- reasoning: 可选；简要说明本轮选择 mode 的理由，便于审计。\n"
                "[硬约束]\n"
                "- 你本身没有工具权限，所有外部动作只能通过 execute 委托，且每轮最多一个 tool_name。\n"
                "- 多步任务：以 feedback 中 Structured tool result 的 count 为准；未完成时 request_complete=false。\n"
                "- 当 mode==execute 时，不要在 answer 中给出最终回复，让执行层先工作。\n"
                "- 闲聊、致谢、与可委托能力无关的请求，用 answer_directly；纯附和/无需回复时用 silent。\n"
                "- mode==silent：不 delegate、不 reply，仅记录 reasoning/episode_note，等待后续刺激。\n"
                "- 不要在指令中重复用户原话，要写明你希望执行层做什么。"
            )
        return (
            "[Planning Requirements]\n"
            "Emit the structured decision for this turn:\n"
            "- mode: must be \"execute\", \"answer_directly\", or \"silent\".\n"
            "- instruction: required when mode==execute; a single natural-language directive for the execution layer.\n"
            "- answer: required when mode==answer_directly; leave empty for silent/execute.\n"
            "- episode_note: optional short text worth remembering; do not dump raw tool output here.\n"
            "- tool_name: required when mode==execute; exactly one enabled capability (one tool this round).\n"
            "- request_complete: true only when the original user request is fully done; else false and continue execute.\n"
            "- capability_hint: optional legacy field; prefer tool_name.\n"
            "- reasoning: optional short rationale for the chosen mode (for auditing).\n"
            "[Hard Constraints]\n"
            "- You hold no tools yourself; delegate via execute with at most one tool_name per round.\n"
            "- Multi-step tasks: trust Structured tool result count on feedback; request_complete=false until done.\n"
            "- Read [Current Stimulus], [Scene Context], [Task State], and [Working Memory] before deciding.\n"
            "- When mode==execute, leave answer empty and let the execution layer work first.\n"
            "- For small talk or delegable-unrelated requests, choose answer_directly; use silent for acks that need no reply.\n"
            "- mode==silent: no delegate, no reply; record reasoning/episode_note and wait for further stimulus.\n"
            "- Don't echo the user; in the instruction state explicitly what you want the execution layer to do."
        )

    # ------------------------------------------------------------------
    # Execution call
    # ------------------------------------------------------------------

    @staticmethod
    def _allowed_tools_for_decision(decision: ThinkingDecision) -> Optional[List[str]]:
        name = str(decision.tool_name or "").strip()
        if name:
            return [name]
        hints = decision.capability_hint or []
        if isinstance(hints, list) and len(hints) == 1:
            only = str(hints[0] or "").strip()
            if only:
                return [only]
        return None

    def _execute(
        self,
        *,
        decision: ThinkingDecision,
        perception: PerceptionInput,
        state: ConversationState,
    ) -> ExecutionResult:
        allowed = self._allowed_tools_for_decision(decision)
        request = ExecutionRequest(
            instruction=str(decision.instruction or "").strip(),
            thread_id=perception.thread_id,
            correlation_id=uuid4().hex,
            allowed_tool_names=allowed,
            capability_hint=list(decision.capability_hint or []) or None,
        )

        def _wm_write(result: ExecutionResult) -> None:
            if self.wm_writer is None:
                return
            try:
                self.wm_writer.write(state.wm_entries, result.tool_history)
            except Exception:
                logger.exception(
                    "WMWriter.write failed for conversation_id=%s",
                    perception.conversation_id,
                )

        return self.execution_agent.execute(
            request,
            wm_writer_callback=_wm_write,
        )

    # ------------------------------------------------------------------
    # Summarize pass (LLM #2)
    # ------------------------------------------------------------------

    def _summarize(
        self,
        *,
        perception: PerceptionInput,
        decision: ThinkingDecision,
        execution_result: ExecutionResult,
        state: ConversationState,
    ) -> ThinkingSummary:
        messages = self._build_summarize_messages(
            perception=perception,
            decision=decision,
            execution_result=execution_result,
            state=state,
        )
        try:
            structured_model = self.model_provider.model.with_structured_output(
                ThinkingSummary,
                include_raw=False,
            )
        except Exception:
            structured_model = self.model_provider.model.with_structured_output(ThinkingSummary)

        result = self._invoke_structured(
            structured_model,
            messages=messages,
            call_name="thinking.summarize",
        )
        return self._coerce_summary(result, execution_result)

    def _build_summarize_messages(
        self,
        *,
        perception: PerceptionInput,
        decision: ThinkingDecision,
        execution_result: ExecutionResult,
        state: ConversationState,
    ) -> List[Dict[str, str]]:
        sections: List[str] = []
        if self.system_prompt:
            sections.append(self.system_prompt)
        if self.persona_prompt:
            sections.append(self.persona_prompt)
        sections.append(self._summarize_instructions_block(execution_result))

        sections.append(self._render_perception_input_block(perception))
        if perception.scene_context:
            sections.append(f"[Scene Context]\n{perception.scene_context}")
        sections.append(self._render_task_state(state))

        if self.wm_reader is not None:
            wm_block = self._render_working_memory(state)
            if wm_block:
                sections.append(wm_block)

        # Execution-layer report.
        if self.prompt_language == "zh":
            exec_block = [
                "[执行层报告]",
                f"上一步指令: {decision.instruction or '(空)'}",
                f"执行摘要: {execution_result.summary or '(空)'}",
                f"是否充分: {'否' if execution_result.insufficient else '是'}",
                f"是否触限: {'是' if execution_result.limit_reached else '否'}",
                f"工具调用数: {execution_result.tool_call_count}",
            ]
        else:
            exec_block = [
                "[Execution Report]",
                f"Instruction: {decision.instruction or '(empty)'}",
                f"Summary: {execution_result.summary or '(empty)'}",
                f"Sufficient: {'no' if execution_result.insufficient else 'yes'}",
                f"Limit reached: {'yes' if execution_result.limit_reached else 'no'}",
                f"Tool calls: {execution_result.tool_call_count}",
            ]
        sections.append("\n".join(exec_block))

        system_text = "\n\n".join(section for section in sections if section).strip()
        messages: List[Dict[str, str]] = [{"role": "system", "content": system_text}]
        messages.append({"role": "user", "content": str(perception.stimulus.text or "").strip()})
        return messages

    def _summarize_instructions_block(self, execution_result: ExecutionResult) -> str:
        if self._summarize_instructions_override:
            return self._summarize_instructions_override
        if self.prompt_language == "zh":
            return (
                "[总结要求]\n"
                "请基于执行层报告，向用户输出最终回复，并按结构化字段返回：\n"
                "- answer: 给用户的自然语言回复（必填，使用与用户相同的语言）。\n"
                "- episode_note: 可选，简短记下值得长期记忆的事实或承诺。\n"
                "[硬约束]\n"
                "- 不要复述执行层的原始结果，只总结对用户有意义的部分。\n"
                "- 若执行层报告 insufficient 或 limit_reached，请如实说明，不要编造证据。"
            )
        return (
            "[Summarize Requirements]\n"
            "Using the execution report, produce the final reply for the user, in structured form:\n"
            "- answer: required, the natural-language reply (match the user's language).\n"
            "- episode_note: optional short note worth remembering long-term.\n"
            "- task_progress_update: optional partial update after this execution: goal, completed, remaining. Omit unchanged fields.\n"
            "[Hard Constraints]\n"
            "- Do not echo raw execution output; summarize only what matters to the user.\n"
            "- Update task_progress_update only from the execution report and visible tool evidence.\n"
            "- If the execution report is insufficient or limit_reached, say so plainly; do not fabricate evidence."
        )

    # ------------------------------------------------------------------
    # LLM invocation with retries
    # ------------------------------------------------------------------

    def _invoke_structured(
        self,
        structured_model: Any,
        *,
        messages: List[Dict[str, str]],
        call_name: str,
    ) -> Any:
        def _attempt(_: int) -> Any:
            return structured_model.invoke(messages)

        return self.model_provider.invoke_with_network_retry(_attempt, call_name=call_name)

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
                completed=list(raw.completed),
                remaining=list(raw.remaining),
            )
            return update if not update.is_empty() else None
        if isinstance(raw, dict):
            update = TaskProgressUpdate(
                goal=str(raw.get("goal", "") or "").strip() if "goal" in raw else None,
                completed=cls._coerce_string_list(raw.get("completed")) if "completed" in raw else None,
                remaining=cls._coerce_string_list(raw.get("remaining")) if "remaining" in raw else None,
            )
            return update if not update.is_empty() else None
        if any(hasattr(raw, name) for name in ("goal", "completed", "remaining")):
            update = TaskProgressUpdate(
                goal=(
                    str(getattr(raw, "goal", "") or "").strip()
                    if hasattr(raw, "goal")
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
                capability_hint=list(raw["capability_hint"]) if isinstance(raw.get("capability_hint"), list) else None,
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
                capability_hint=getattr(raw, "capability_hint", None),
                request_complete=getattr(raw, "request_complete", None),
                reasoning=getattr(raw, "reasoning", None),
            )
        # Fallback: treat as direct answer string.
        return ThinkingDecision(
            mode="answer_directly",
            answer=str(raw or "").strip() or None,
        )

    @classmethod
    def _coerce_summary(cls, raw: Any, execution_result: ExecutionResult) -> ThinkingSummary:
        if isinstance(raw, ThinkingSummary):
            answer = str(raw.answer or "").strip() or execution_result.summary
            return ThinkingSummary(
                answer=answer,
                episode_note=raw.episode_note,
                task_progress_update=raw.task_progress_update,
            )
        if isinstance(raw, dict):
            answer = str(raw.get("answer", "") or "").strip() or execution_result.summary
            return ThinkingSummary(
                answer=answer,
                episode_note=raw.get("episode_note"),
                task_progress_update=cls._coerce_task_progress_update(
                    raw.get("task_progress_update")
                ),
            )
        if hasattr(raw, "answer"):
            answer = str(getattr(raw, "answer", "") or "").strip() or execution_result.summary
            return ThinkingSummary(
                answer=answer,
                episode_note=getattr(raw, "episode_note", None),
                task_progress_update=cls._coerce_task_progress_update(
                    getattr(raw, "task_progress_update", None)
                ),
            )
        return ThinkingSummary(answer=str(raw or "").strip() or execution_result.summary)

    def _fallback_answer(self, perception: PerceptionInput) -> str:
        if self._fallback_answer_override:
            return self._fallback_answer_override
        if self.prompt_language == "zh":
            return "我已经收到你的请求，但暂时没有更多可以补充的内容。"
        return "I received your message; there is nothing further I can add right now."
