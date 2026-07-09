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
    build_runtime_context_block,
)
from m_agent.layers.thinking.state import (
    ConversationState,
    ConversationStateRegistry,
    ThinkingDecision,
    ThinkingSummary,
    is_execute_mode,
    is_silent_mode,
    normalize_thinking_mode,
)
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
        plan_instructions_prompt: str = "",
        summarize_instructions_prompt: str = "",
        capability_boundary_header: str = "",
        runtime_context_schedule_template: str = "",
        runtime_context_generic_template: str = "",
        fallback_answer_prompt: str = "",
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
        self._plan_instructions_override = str(plan_instructions_prompt or "").strip()
        self._summarize_instructions_override = str(summarize_instructions_prompt or "").strip()
        self._capability_boundary_header_override = str(capability_boundary_header or "").strip()
        self._runtime_ctx_schedule_override = str(runtime_context_schedule_template or "").strip()
        self._runtime_ctx_generic_override = str(runtime_context_generic_template or "").strip()
        self._fallback_answer_override = str(fallback_answer_prompt or "").strip()

    # ------------------------------------------------------------------
    # Public API used by the perception layer
    # ------------------------------------------------------------------

    def handle(
        self,
        perception: PerceptionInput,
        *,
        event_emitter: Optional[ThinkingEventEmitter] = None,
    ) -> ThinkingTurnResult:
        if not isinstance(perception, PerceptionInput):
            raise TypeError("ThinkingAgent.handle expects PerceptionInput")
        if not str(perception.user_message or "").strip():
            raise ValueError("PerceptionInput.user_message must be a non-empty string")

        state = self.state_registry.get_or_create(
            perception.conversation_id,
            thread_id=perception.thread_id,
        )
        state.turn_count += 1
        turn_meta = {
            "thread_id": perception.thread_id,
            "conversation_id": perception.conversation_id,
            "turn": state.turn_count,
            "source": perception.source,
        }

        emit = self._make_safe_emitter(event_emitter)
        emit(
            "thinking_started",
            {
                "thread_id": perception.thread_id,
                "conversation_id": perception.conversation_id,
                "turn": state.turn_count,
                "source": perception.source,
            },
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

    # ------------------------------------------------------------------
    # Planning pass (LLM #1)
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

        runtime_block = build_runtime_context_block(
            source=perception.source,
            system_context=perception.system_context,
            language=self.prompt_language,
            schedule_template=self._runtime_ctx_schedule_override,
            generic_template=self._runtime_ctx_generic_override,
        )
        if runtime_block:
            sections.append(runtime_block)

        scene_tail = ""
        if isinstance(perception.system_context, dict):
            scene_tail = str(perception.system_context.get("scene_tail_text", "") or "").strip()
        if scene_tail:
            sections.append(scene_tail)

        if self.wm_reader is not None:
            wm_block = self.wm_reader.render(state.wm_entries, language=self.prompt_language)
            if wm_block:
                sections.append(wm_block)

        system_text = "\n\n".join(section for section in sections if section).strip()
        messages: List[Dict[str, str]] = [{"role": "system", "content": system_text}]
        for item in perception.history_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "") or "").strip().lower()
            content = str(item.get("content", "") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": str(perception.user_message or "").strip()})
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

        runtime_block = build_runtime_context_block(
            source=perception.source,
            system_context=perception.system_context,
            language=self.prompt_language,
            schedule_template=self._runtime_ctx_schedule_override,
            generic_template=self._runtime_ctx_generic_override,
        )
        if runtime_block:
            sections.append(runtime_block)

        if self.wm_reader is not None:
            wm_block = self.wm_reader.render(state.wm_entries, language=self.prompt_language)
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
        for item in perception.history_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "") or "").strip().lower()
            content = str(item.get("content", "") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": str(perception.user_message or "").strip()})
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
            "[Hard Constraints]\n"
            "- Do not echo raw execution output; summarize only what matters to the user.\n"
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
    def _coerce_decision(raw: Any) -> ThinkingDecision:
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

    @staticmethod
    def _coerce_summary(raw: Any, execution_result: ExecutionResult) -> ThinkingSummary:
        if isinstance(raw, ThinkingSummary):
            answer = str(raw.answer or "").strip() or execution_result.summary
            return ThinkingSummary(answer=answer, episode_note=raw.episode_note)
        if isinstance(raw, dict):
            answer = str(raw.get("answer", "") or "").strip() or execution_result.summary
            return ThinkingSummary(answer=answer, episode_note=raw.get("episode_note"))
        if hasattr(raw, "answer"):
            answer = str(getattr(raw, "answer", "") or "").strip() or execution_result.summary
            return ThinkingSummary(answer=answer, episode_note=getattr(raw, "episode_note", None))
        return ThinkingSummary(answer=str(raw or "").strip() or execution_result.summary)

    def _fallback_answer(self, perception: PerceptionInput) -> str:
        if self._fallback_answer_override:
            return self._fallback_answer_override
        if self.prompt_language == "zh":
            return "我已经收到你的请求，但暂时没有更多可以补充的内容。"
        return "I received your message; there is nothing further I can add right now."
