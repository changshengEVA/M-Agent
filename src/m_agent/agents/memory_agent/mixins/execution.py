from __future__ import annotations

import logging
import time
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional

from langgraph.errors import GraphRecursionError

from m_agent.utils.api_error_utils import is_network_api_error

logger = logging.getLogger(__name__)


class MemoryAgentExecutionMixin:
    def _compute_network_retry_delay(self, attempt: int) -> float:
        """计算网络重试的退避等待时间。"""
        exponent = max(attempt - 1, 0)
        delay = self.network_retry_backoff_seconds * (
            self.network_retry_backoff_multiplier ** exponent
        )
        return min(delay, self.network_retry_max_backoff_seconds)
    def _invoke_model_with_network_retry(self, prompt_text: str, call_name: str) -> Any:
        """调用基础模型，并处理网络重试。"""
        total_attempts = max(self.network_retry_attempts, 1)
        for attempt in range(1, total_attempts + 1):
            invoke_start = time.perf_counter()
            try:
                logger.info(
                    "API call: %s(attempt=%d/%d, prompt_len=%d)",
                    call_name,
                    attempt,
                    total_attempts,
                    len(prompt_text or ""),
                )
                response = self.model.invoke(prompt_text)
                logger.info(
                    "API response: %s(attempt=%d/%d, elapsed_ms=%.2f)",
                    call_name,
                    attempt,
                    total_attempts,
                    (time.perf_counter() - invoke_start) * 1000.0,
                )
                return response
            except Exception as exc:
                if not is_network_api_error(exc) or attempt >= total_attempts:
                    raise
                delay = self._compute_network_retry_delay(attempt)
                logger.warning(
                    "%s hit network/API error on attempt %d/%d: %s; retrying in %.2fs",
                    call_name,
                    attempt,
                    total_attempts,
                    exc,
                    delay,
                )
                if delay > 0:
                    time.sleep(delay)
        raise RuntimeError(f"{call_name} exhausted retry attempts unexpectedly")
    def _invoke_tool_agent_once(self, prompt_text: str, thread_id: str) -> Dict[str, Any]:
        """单次调用工具 agent，含递归上限回退。"""
        invoke_config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self.recursion_limit,
        }
        try:
            invoke_start = time.perf_counter()
            logger.info(
                "API call: agent.invoke(thread_id=%s, recursion_limit=%s, prompt_len=%d)",
                thread_id,
                self.recursion_limit,
                len(prompt_text or ""),
            )
            response = self.agent.invoke(
                {"messages": [{"role": "user", "content": prompt_text}]},
                config=invoke_config,
            )
            logger.info(
                "API response: agent.invoke(thread_id=%s, elapsed_ms=%.2f)",
                thread_id,
                (time.perf_counter() - invoke_start) * 1000.0,
            )
            return response
        except GraphRecursionError:
            logger.warning(
                "GraphRecursionError on thread_id=%s with recursion_limit=%s; retrying with recursion_limit=%s",
                thread_id,
                self.recursion_limit,
                self.retry_recursion_limit,
            )
            retry_config = {
                "configurable": {"thread_id": thread_id},
                "recursion_limit": self.retry_recursion_limit,
            }
            retry_start = time.perf_counter()
            logger.info(
                "API call: agent.invoke.retry(thread_id=%s, recursion_limit=%s, prompt_len=%d)",
                thread_id,
                self.retry_recursion_limit,
                len(prompt_text or ""),
            )
            response = self.agent.invoke(
                {"messages": [{"role": "user", "content": prompt_text}]},
                config=retry_config,
            )
            logger.info(
                "API response: agent.invoke.retry(thread_id=%s, elapsed_ms=%.2f)",
                thread_id,
                (time.perf_counter() - retry_start) * 1000.0,
            )
            return response
    def _invoke_tool_agent(self, prompt_text: str, thread_id: str) -> Dict[str, Any]:
        """调用工具 agent，并处理网络重试。"""
        total_attempts = max(self.network_retry_attempts, 1)
        for attempt in range(1, total_attempts + 1):
            attempt_thread_id = thread_id if attempt == 1 else f"{thread_id}:netretry:{attempt}"
            try:
                return self._invoke_tool_agent_once(
                    prompt_text=prompt_text,
                    thread_id=attempt_thread_id,
                )
            except Exception as exc:
                if not is_network_api_error(exc) or attempt >= total_attempts:
                    raise
                delay = self._compute_network_retry_delay(attempt)
                next_thread_id = f"{thread_id}:netretry:{attempt + 1}"
                logger.warning(
                    "agent.invoke(thread_id=%s) hit network/API error on attempt %d/%d: %s; retrying with fresh thread_id=%s in %.2fs",
                    attempt_thread_id,
                    attempt,
                    total_attempts,
                    exc,
                    next_thread_id,
                    delay,
                )
                if delay > 0:
                    time.sleep(delay)
        raise RuntimeError("agent.invoke exhausted retry attempts unexpectedly")
    def _normalize_agent_structured_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """将 agent 结构化响应归一化为 payload。"""
        structured = response.get("structured_response")
        if is_dataclass(structured):
            return self._normalize_output(asdict(structured))
        if isinstance(structured, dict):
            return self._normalize_output(structured)
        return self._normalize_output(
            {
                "answer": str(structured) if structured is not None else str(response),
                "gold_answer": None,
                "evidence": None,
            }
        )
    def _solve_sub_questions(
        self,
        question_text: str,
        question_plan: Dict[str, Any],
        active_thread_id: str,
    ) -> List[Dict[str, Any]]:
        """逐个执行子问题并收集结果。"""
        sub_questions = question_plan.get("sub_questions", [])
        if not isinstance(sub_questions, list):
            sub_questions = []

        results: List[Dict[str, Any]] = []
        total = max(len(sub_questions), 1)

        for idx, item in enumerate(sub_questions, start=1):
            sub_question = str(item).strip()
            if not sub_question:
                continue

            logger.info(
                "SUBQ START: %s",
                json.dumps(
                    {
                        "index": idx,
                        "question": sub_question,
                        "status": "in_progress",
                    },
                    ensure_ascii=False,
                ),
            )

            previous_scope = self._get_active_search_scope()
            self._set_active_search_scope(f"subq:{idx}")
            try:
                try:
                    prompt_text = self._build_sub_question_prompt(
                        question_text=question_text,
                        question_plan=question_plan,
                        sub_question=sub_question,
                        sub_index=idx,
                        total_sub_questions=total,
                    )
                    response = self._invoke_tool_agent(
                        prompt_text=prompt_text,
                        thread_id=f"{active_thread_id}:subq:{idx}",
                    )
                    payload = self._normalize_agent_structured_response(response)
                    result_item = {
                        "index": idx,
                        "question": sub_question,
                        "status": "completed",
                        "answer": str(payload.get("answer", "") or "").strip(),
                        "gold_answer": payload.get("gold_answer"),
                        "evidence": payload.get("evidence"),
                    }
                except Exception as exc:
                    if is_network_api_error(exc):
                        logger.exception(
                            "Sub-question %s hit network/API error; aborting current run",
                            idx,
                        )
                        raise
                    result_item = {
                        "index": idx,
                        "question": sub_question,
                        "status": "failed",
                        "answer": "",
                        "gold_answer": None,
                        "evidence": None,
                        "error": str(exc),
                    }
            finally:
                self._set_active_search_scope(previous_scope)

            logger.info(
                "SUBQ DONE: %s",
                json.dumps(self._safe_trace_value(result_item), ensure_ascii=False),
            )
            results.append(result_item)

        return results
    def _synthesize_final_answer(
        self,
        question_text: str,
        question_plan: Dict[str, Any],
        sub_question_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """综合子问题结果生成最终回答。"""
        synthesis_prompt = self._build_final_synthesis_prompt(
            question_text=question_text,
            question_plan=question_plan,
            sub_question_results=sub_question_results,
        )
        response = self._invoke_model_with_network_retry(
            prompt_text=synthesis_prompt,
            call_name="synthesize_final_answer",
        )
        payload = self._payload_from_model_response(response)
        logger.info(
            "API response: synthesize_final_answer(answer_len=%d)",
            len(str(payload.get("answer", "") or "")),
        )
        return payload
    def _build_execution_prompt(self, question_text: str, question_plan: Dict[str, Any]) -> str:
        """构建拆解路径的执行 prompt。"""
        return self._render_runtime_prompt(
            "execution_prompt",
            replacements={
                "<question_text>": question_text,
                "<question_plan_json>": json.dumps(question_plan, ensure_ascii=False, indent=2),
            },
        )
    def _build_direct_execution_prompt(self, question_text: str) -> str:
        """构建直答路径的执行 prompt。"""
        return self._render_runtime_prompt(
            "direct_execution_prompt",
            replacements={"<question_text>": question_text},
        )
    def _finalize_recall_payload(
        self,
        payload: Dict[str, Any],
        *,
        question_plan: Dict[str, Any],
        sub_question_results: List[Dict[str, Any]],
        tool_calls: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """补全 payload 的证据、规划和追踪字段。"""
        safe_tool_calls = [self._safe_trace_value(call) for call in tool_calls]
        payload["tool_calls"] = safe_tool_calls
        payload["tool_call_count"] = len(tool_calls)
        episode_refs = self._collect_episode_refs_from_tool_calls(safe_tool_calls)
        payload["evidence_episode_refs"] = episode_refs
        payload["evidence_episode_ref_count"] = len(episode_refs)
        payload = self._append_episode_refs_to_payload(payload, episode_refs)
        payload["question_plan"] = question_plan
        payload["sub_question_results"] = sub_question_results
        if not isinstance(payload.get("sub_questions"), list):
            maybe_sub_questions = question_plan.get("sub_questions", [])
            payload["sub_questions"] = maybe_sub_questions if isinstance(maybe_sub_questions, list) else []
        if not payload.get("plan_summary"):
            payload["plan_summary"] = question_plan.get("decomposition_reason")
        self._log_structured_trace(
            self._TRACE_PREFIX_FINAL_PAYLOAD,
            payload,
        )
        self._last_tool_calls = safe_tool_calls
        return payload
    def _answer_directly(self, question_text: str, active_thread_id: str) -> Dict[str, Any]:
        """执行直接回答路径。"""
        prompt_text = self._build_direct_execution_prompt(question_text)
        previous_scope = self._get_active_search_scope()
        self._set_active_search_scope("direct")
        try:
            response = self._invoke_tool_agent(
                prompt_text=prompt_text,
                thread_id=f"{active_thread_id}:direct",
            )
        finally:
            self._set_active_search_scope(previous_scope)
        payload = self._normalize_agent_structured_response(response)
        self._promote_short_answer_to_gold(payload)
        self._ensure_sub_questions_field(payload)
        if not payload.get("plan_summary"):
            payload["plan_summary"] = self._get_runtime_prompt_text("direct_answer_plan_summary")
        self._log_structured_trace(
            self._TRACE_PREFIX_DIRECT_ANSWER,
            payload,
        )
        logger.info(
            "API response: direct_answer(answer_len=%d, gold_answer_present=%s, evidence_present=%s)",
            len(str(payload.get("answer", "") or "")),
            payload.get("gold_answer") is not None,
            bool(str(payload.get("evidence", "") or "").strip()),
        )
        return payload
    def shallow_recall(self, question: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
        """执行浅层回忆流程。"""
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")

        question_text = question.strip()
        self._reset_round_state()
        try:
            question_plan = self._build_shallow_question_plan(question_text)
            self._last_question_plan = question_plan
            previous_scope = self._get_active_search_scope()
            self._set_active_search_scope("shallow")
            try:
                search_result = self._search_details_with_trace(question_text)
                response = self._invoke_model_with_network_retry(
                    prompt_text=self._build_shallow_recall_prompt(question_text, search_result),
                    call_name="shallow_recall",
                )
            finally:
                self._set_active_search_scope(previous_scope)
            payload = self._payload_from_model_response(response)
            self._promote_short_answer_to_gold(payload)
            self._ensure_sub_questions_field(payload)
            if not payload.get("plan_summary"):
                payload["plan_summary"] = question_plan["decomposition_reason"]

            tool_calls = self._consume_current_tool_calls()
            return self._finalize_recall_payload(
                payload,
                question_plan=question_plan,
                sub_question_results=[],
                tool_calls=tool_calls,
            )
        except Exception:
            self._last_tool_calls = self._consume_current_tool_calls()
            raise
    def _log_question_strategy(
        self,
        *,
        question_text: str,
        decompose_first: bool,
        strategy_reason: str,
    ) -> None:
        """记录问题策略判定结果。"""
        self._log_structured_trace(
            self._TRACE_PREFIX_QUESTION_STRATEGY,
            {
                "question": question_text,
                "decompose_first": decompose_first,
                "reason": strategy_reason,
            },
        )
    def _run_direct_recall_path(
        self,
        *,
        question_text: str,
        active_thread_id: str,
        strategy_reason: str,
    ) -> Dict[str, Any]:
        """运行直答分支并完成收尾。"""
        direct_plan = self._build_direct_question_plan(question_text, strategy_reason)
        self._last_question_plan = direct_plan
        payload = self._answer_directly(
            question_text=question_text,
            active_thread_id=active_thread_id,
        )
        tool_calls = self._consume_current_tool_calls()
        return self._finalize_recall_payload(
            payload,
            question_plan=direct_plan,
            sub_question_results=[],
            tool_calls=tool_calls,
        )
    def _run_decomposed_recall_path(
        self,
        *,
        question_text: str,
        active_thread_id: str,
    ) -> Dict[str, Any]:
        """运行拆解分支并完成收尾。"""
        question_plan = self._decompose_question(question_text)
        self._last_question_plan = question_plan
        sub_question_results = self._solve_sub_questions(
            question_text=question_text,
            question_plan=question_plan,
            active_thread_id=active_thread_id,
        )
        payload = self._synthesize_final_answer(
            question_text=question_text,
            question_plan=question_plan,
            sub_question_results=sub_question_results,
        )
        tool_calls = self._consume_current_tool_calls()
        return self._finalize_recall_payload(
            payload,
            question_plan=question_plan,
            sub_question_results=sub_question_results,
            tool_calls=tool_calls,
        )
    def deep_recall(self, question: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
        """执行完整的深度回忆主流程。"""
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")

        question_text = question.strip()
        active_thread_id = thread_id or self.thread_id
        self._reset_round_state()
        try:
            decompose_first, strategy_reason = self._detect_direct_answer_strategy(question_text)
            self._log_question_strategy(
                question_text=question_text,
                decompose_first=decompose_first,
                strategy_reason=strategy_reason,
            )

            if not decompose_first:
                return self._run_direct_recall_path(
                    question_text=question_text,
                    active_thread_id=active_thread_id,
                    strategy_reason=strategy_reason,
                )

            return self._run_decomposed_recall_path(
                question_text=question_text,
                active_thread_id=active_thread_id,
            )
        except Exception:
            self._last_tool_calls = self._consume_current_tool_calls()
            raise
    def ask(self, question: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
        """兼容入口：转发到 deep_recall。"""
        return self.deep_recall(question=question, thread_id=thread_id)

