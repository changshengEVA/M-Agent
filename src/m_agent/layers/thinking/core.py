"""Persona-owning, plan-only thinking layer for the Runtime runtime.

Each stimulus updates transaction task state and produces one structured
decision. Tool execution and user-visible replies are delegated by the
Runtime scheduler, so this layer never invokes capabilities directly.
"""
from __future__ import annotations

import logging
import json
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
    TASK_COMPLETION_AWAITING_USER,
    TASK_COMPLETION_COMPLETED,
    TASK_COMPLETION_PROCESSING,
    TaskProgress,
    TaskProgressUpdate,
    TransactionResolution,
    ThinkingDecision,
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
from m_agent.utils.api_error_utils import is_network_api_error


logger = logging.getLogger(__name__)

#: Callback signature for streaming planning state to the perception layer,
#: which forwards it to SSE. Implementations must be cheap and must not raise.
ThinkingEventEmitter = Callable[[str, Dict[str, Any]], None]


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
        self.prompt_language = str(prompt_language or "zh").strip().lower() or "zh"

        # YAML-overridable prompt fragments. Empty/None means "use built-in default".
        self._task_state_base_override = str(task_state_base_prompt or "").strip()
        self._task_state_instructions_override = str(task_state_instructions_prompt or "").strip()
        self._plan_instructions_override = str(plan_instructions_prompt or "").strip()
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
            model = self.model_provider.model.with_structured_output(
                TransactionResolution, include_raw=False
            )
        except Exception:
            model = self.model_provider.model.with_structured_output(TransactionResolution)
        try:
            raw = self._invoke_structured(
                model,
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

        task_progress_update = self._pre_gen_task_state(perception, state)
        self._apply_task_progress_update(state, task_progress_update)
        self._force_processing_on_param_gap(perception, state)
        self._coerce_awaiting_user_without_reply(state)
        emit(
            "thinking_task_state",
            self._task_state_event_payload(task_progress_update, perception, state),
        )

        completion_status = normalize_task_completion_status(
            state.task_progress.completion_status
        )
        tx_state = getattr(state, "state", None)
        tx_state_value = (
            tx_state.value if hasattr(tx_state, "value") else str(tx_state or "")
        ).strip().lower()
        # Action planning is gated by domain lifecycle / macro status, not by
        # stuffing pause into ThinkingDecision.mode.
        if tx_state_value == "pause":
            decision = ThinkingDecision(
                mode="silent",
                request_complete=False,
                reasoning="Transaction is paused waiting for user collaboration.",
            )
        elif completion_status == TASK_COMPLETION_COMPLETED:
            decision = ThinkingDecision(
                mode="silent",
                request_complete=True,
                reasoning="Task state confirms that the complete request is fulfilled.",
            )
        elif completion_status == TASK_COMPLETION_AWAITING_USER:
            # Macro wait-for-user: skip action plan; runtime maps this to pause.
            decision = ThinkingDecision(
                mode="silent",
                request_complete=False,
                reasoning="Task state is awaiting user collaboration.",
            )
        else:
            decision = self._plan(perception, state)
            # Completion is an evidence-backed task-state fact, not an
            # implication of the action selected by the planning model.
            decision.request_complete = False
        emit("thinking_plan", self._decision_event_payload(decision, perception, state))

        mode = normalize_thinking_mode(decision.mode)
        if not is_silent_mode(mode) and mode != "execute":
            decision.answer = (
                str(decision.answer or "").strip() or self._fallback_answer(perception)
            )

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

    def _scratch_for(self, conversation_id: str) -> ThinkingScratch:
        key = str(conversation_id or "").strip()
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
    def _force_processing_on_param_gap(
        perception: PerceptionInput,
        state: ConversationState,
    ) -> None:
        """Param-fill short-circuits must not enter wait-for-user semantics."""

        if perception.stimulus.kind != StimulusKind.EXECUTION_FEEDBACK:
            return
        payload = perception.stimulus.payload if isinstance(
            perception.stimulus.payload, dict
        ) else {}
        history = payload.get("tool_history")
        if not isinstance(history, list) or not history:
            return
        step = history[-1] if isinstance(history[-1], dict) else {}
        result = step.get("result") if isinstance(step.get("result"), dict) else {}
        is_param_gap = (
            bool(result.get("needs_clarification"))
            or str(result.get("stage", "") or "").strip() == "param_fill"
            or result.get("tool_invoked") is False
        )
        if is_param_gap:
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
    # Task-state pre-generation pass (LLM #1 in runtime)
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
        lines = [
            "[Current Stimulus]",
            f"kind: {perception.stimulus.kind.value}",
            "semantic_role: user_utterance"
            if perception.stimulus.kind == StimulusKind.USER_MESSAGE
            else "semantic_role: runtime_event",
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
