from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from m_agent.utils.api_error_utils import is_network_api_error

logger = logging.getLogger(__name__)


class MemoryAgentPlanningMixin:
    @staticmethod
    def _extract_message_text(message: Any) -> str:
        """doc."""
        content = getattr(message, "content", message)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: List[str] = []
            for item in content:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
            return "\n".join(chunk for chunk in chunks if chunk)
        return str(content or "")
    @classmethod
    def _parse_json_block(cls, text: str) -> Optional[Dict[str, Any]]:
        """doc."""
        if not isinstance(text, str) or not text.strip():
            return None
        stripped = text.strip()
        candidates = [stripped]
        matched = cls._JSON_BLOCK_PATTERN.search(stripped)
        if matched:
            candidates.append(matched.group(0))

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None
    def _payload_from_response_text(self, response_text: str) -> Dict[str, Any]:
        """doc."""
        parsed = self._parse_json_block(response_text)
        if isinstance(parsed, dict):
            return self._normalize_output(parsed)
        return self._normalize_output(
            {
                "answer": str(response_text or "").strip(),
                "gold_answer": None,
                "evidence": None,
            }
        )
    def _payload_from_model_response(self, response: Any) -> Dict[str, Any]:
        """doc."""
        return self._payload_from_response_text(self._extract_message_text(response))
    def _promote_short_answer_to_gold(self, payload: Dict[str, Any]) -> None:
        """doc."""
        answer_text = str(payload.get("answer", "") or "").strip()
        if (
            payload.get("gold_answer") is None
            and answer_text
            and not self._is_unanswerable_text(answer_text)
            and len(answer_text) <= 120
            and "\n" not in answer_text
        ):
            payload["gold_answer"] = answer_text
    @staticmethod
    def _ensure_sub_questions_field(payload: Dict[str, Any]) -> None:
        """doc."""
        if not isinstance(payload.get("sub_questions"), list):
            payload["sub_questions"] = []
    @staticmethod
    def _normalize_question_plan(question_text: str, plan: Dict[str, Any]) -> Dict[str, Any]:
        """doc."""
        normalized_sub_questions = plan.get("sub_questions", [])
        if not isinstance(normalized_sub_questions, list):
            normalized_sub_questions = []
        normalized_sub_questions = [
            str(item).strip() for item in normalized_sub_questions if str(item).strip()
        ]

        tool_order = plan.get("suggested_tool_order", [])
        if not isinstance(tool_order, list):
            tool_order = []
        tool_order = [str(item).strip() for item in tool_order if str(item).strip()]

        question_type = str(plan.get("question_type", "") or "").strip().lower()
        if not question_type:
            question_type = "direct_lookup"

        goal = str(plan.get("goal", "") or "").strip() or question_text
        decomposition_reason = str(plan.get("decomposition_reason", "") or "").strip()
        completion_criteria = str(plan.get("completion_criteria", "") or "").strip()

        if not normalized_sub_questions:
            normalized_sub_questions = [question_text]

        return {
            "goal": goal,
            "question_type": question_type,
            "decomposition_reason": decomposition_reason,
            "sub_questions": normalized_sub_questions,
            "suggested_tool_order": tool_order,
            "completion_criteria": completion_criteria,
        }
    def _fallback_question_plan(self, question_text: str) -> Dict[str, Any]:
        """doc."""
        normalized = question_text.strip()
        lowered = normalized.lower()

        if any(token in lowered for token in ("compare", "difference", "different", "similar")) or any(
            token in normalized
            for token in (
                "\u5bf9\u6bd4",
                "\u6bd4\u8f83",
                "\u533a\u522b",
                "\u4e0d\u540c",
                "\u76f8\u540c",
            )
        ):
            question_type = "comparison"
            sub_questions = [
                self._render_runtime_prompt(
                    "fallback_sub_questions",
                    "comparison",
                    "first_side",
                    replacements={"<question_text>": normalized},
                ),
                self._render_runtime_prompt(
                    "fallback_sub_questions",
                    "comparison",
                    "second_side",
                    replacements={"<question_text>": normalized},
                ),
                self._render_runtime_prompt(
                    "fallback_sub_questions",
                    "comparison",
                    "final_compare",
                    replacements={"<question_text>": normalized},
                ),
            ]
        elif any(token in lowered for token in ("how many", "count", "number of")) or any(
            token in normalized
            for token in (
                "\u591a\u5c11",
                "\u51e0\u6b21",
                "\u51e0\u4e2a\u4eba",
                "\u6570\u91cf",
                "\u603b\u5171",
            )
        ):
            question_type = "counting"
            sub_questions = [
                self._render_runtime_prompt(
                    "fallback_sub_questions",
                    "counting",
                    "gather",
                    replacements={"<question_text>": normalized},
                ),
                self._render_runtime_prompt(
                    "fallback_sub_questions",
                    "counting",
                    "count",
                    replacements={"<question_text>": normalized},
                ),
            ]
        elif any(token in lowered for token in ("summary", "summarize")) or any(
            token in normalized
            for token in (
                "\u603b\u7ed3",
                "\u6982\u62ec",
                "\u6982\u8ff0",
            )
        ):
            question_type = "summary"
            sub_questions = [
                self._render_runtime_prompt(
                    "fallback_sub_questions",
                    "summary",
                    "gather",
                    replacements={"<question_text>": normalized},
                ),
                self._render_runtime_prompt(
                    "fallback_sub_questions",
                    "summary",
                    "summarize",
                    replacements={"<question_text>": normalized},
                ),
            ]
        elif any(token in lowered for token in ("when", "before", "after", "during", "date", "time")) or any(
            token in normalized
            for token in (
                "\u4ec0\u4e48\u65f6\u5019",
                "\u4e4b\u524d",
                "\u4e4b\u540e",
                "\u671f\u95f4",
                "\u65e5\u671f",
                "\u65f6\u95f4",
            )
        ):
            question_type = "temporal"
            sub_questions = [
                self._render_runtime_prompt(
                    "fallback_sub_questions",
                    "temporal",
                    "anchors",
                    replacements={"<question_text>": normalized},
                ),
                self._render_runtime_prompt(
                    "fallback_sub_questions",
                    "temporal",
                    "answer",
                    replacements={"<question_text>": normalized},
                ),
            ]
        elif any(token in lowered for token in ("why", "reason", "because", "cause")) or any(
            token in normalized
            for token in (
                "\u4e3a\u4ec0\u4e48",
                "\u539f\u56e0",
                "\u56e0\u4e3a",
                "\u5bfc\u81f4",
            )
        ):
            question_type = "causal"
            sub_questions = [
                self._render_runtime_prompt(
                    "fallback_sub_questions",
                    "causal",
                    "context",
                    replacements={"<question_text>": normalized},
                ),
                self._render_runtime_prompt(
                    "fallback_sub_questions",
                    "causal",
                    "cause",
                    replacements={"<question_text>": normalized},
                ),
            ]
        else:
            question_type = "direct_lookup"
            sub_questions = [normalized]

        return {
            "goal": normalized,
            "question_type": question_type,
            "decomposition_reason": self._get_runtime_prompt_text("heuristic_decomposition_reason"),
            "sub_questions": sub_questions,
            "suggested_tool_order": [
                "search_events_by_time_range" if question_type == "temporal" else "search_details",
                "search_content",
            ],
            "completion_criteria": self._get_runtime_prompt_text("heuristic_completion_criteria"),
        }
    def _build_decomposition_gate_prompt(self, question_text: str) -> str:
        """doc."""
        return self._render_runtime_prompt(
            "decomposition_gate_prompt",
            replacements={"<question_text>": question_text},
        )

    def _detect_direct_answer_strategy(self, question_text: str) -> Tuple[bool, str]:
        """doc."""
        normalized = str(question_text or "").strip()
        if not normalized:
            return False, "empty_question_direct"

        prompt_text = self._build_decomposition_gate_prompt(normalized)
        try:
            response = self._invoke_model_with_network_retry(
                prompt_text=prompt_text,
                call_name="decomposition_gate",
            )
            response_text = self._extract_message_text(response)
            parsed = self._parse_json_block(response_text) or {}
            decompose_first = bool(parsed.get("decompose_first", False))
            parallelizable = bool(parsed.get("parallelizable", False))
            reason = str(parsed.get("reason", "") or "").strip()
            if decompose_first and not parallelizable:
                # 涓茶渚濊禆棰橈細涓嶈鍒嗚В锛岄伩鍏嶅綋鍓嶉摼璺鎷?
                return False, reason or "serial_dependency_detected"
            return decompose_first, reason or ("llm_parallel_decompose" if decompose_first else "llm_direct")
        except Exception as exc:
            if is_network_api_error(exc):
                raise
            logger.warning("decomposition_gate failed, default to direct answer: %s", exc)
            return False, "gate_failed_direct_default"
    def _build_direct_question_plan(self, question_text: str, reason: str) -> Dict[str, Any]:
        """doc."""
        normalized = str(question_text or "").strip()
        return {
            "goal": normalized,
            "question_type": "direct_lookup",
            "decomposition_reason": reason,
            "sub_questions": [],
            "suggested_tool_order": ["search_details", "search_content"],
            "completion_criteria": self._get_runtime_prompt_text("direct_lookup_completion_criteria"),
        }
    @staticmethod
    def _build_shallow_question_plan(question_text: str) -> Dict[str, Any]:
        """doc."""
        return {
            "goal": "",
            "question_type": "",
            "decomposition_reason": "",
            "sub_questions": [],
            "suggested_tool_order": [],
            "completion_criteria": "",
        }
    def _decompose_question(self, question_text: str) -> Dict[str, Any]:
        """doc."""
        try:
            response = self._invoke_model_with_network_retry(
                prompt_text=self._render_runtime_prompt(
                    "decompose_question_prompt",
                    replacements={
                        "<planner_prompt>": self.planner_prompt,
                        "<question_text>": question_text,
                    },
                ),
                call_name="decompose_question",
            )
            plan_text = self._extract_message_text(response)
            parsed = self._parse_json_block(plan_text)
            if isinstance(parsed, dict):
                normalized = self._normalize_question_plan(question_text, parsed)
                logger.info(
                    "PLAN UPDATE: %s",
                    json.dumps(self._safe_trace_value(normalized), ensure_ascii=False),
                )
                logger.info(
                    "API response: decompose_question(question_type=%s, sub_question_count=%d)",
                    normalized.get("question_type"),
                    len(normalized.get("sub_questions", [])),
                )
                return normalized
        except Exception as exc:
            if is_network_api_error(exc):
                logger.exception("decompose_question hit network/API error; aborting current run")
                raise
            logger.warning("decompose_question failed, fallback to heuristic plan: %s", exc)

        fallback = self._fallback_question_plan(question_text)
        logger.info(
            "PLAN UPDATE: %s",
            json.dumps(self._safe_trace_value(fallback), ensure_ascii=False),
        )
        logger.info(
            "API response: decompose_question(question_type=%s, sub_question_count=%d, fallback=true)",
            fallback.get("question_type"),
            len(fallback.get("sub_questions", [])),
        )
        return fallback
    def _build_sub_question_prompt(
        self,
        question_text: str,
        question_plan: Dict[str, Any],
        sub_question: str,
        sub_index: int,
        total_sub_questions: int,
    ) -> str:
        """doc."""
        return self._render_runtime_prompt(
            "sub_question_prompt",
            replacements={
                "<question_text>": question_text,
                "<question_plan_json>": json.dumps(question_plan, ensure_ascii=False, indent=2),
                "<sub_question>": sub_question,
                "<sub_index>": sub_index,
                "<total_sub_questions>": total_sub_questions,
            },
        )
    def _build_final_synthesis_prompt(
        self,
        question_text: str,
        question_plan: Dict[str, Any],
        sub_question_results: List[Dict[str, Any]],
    ) -> str:
        """doc."""
        return self._render_runtime_prompt(
            "final_synthesis_prompt",
            replacements={
                "<question_text>": question_text,
                "<question_plan_json>": json.dumps(question_plan, ensure_ascii=False, indent=2),
                "<sub_question_results_json>": json.dumps(
                    sub_question_results,
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        )
    def _build_shallow_recall_prompt(
        self,
        question_text: str,
        search_result: Dict[str, Any],
    ) -> str:
        """doc."""
        return self._render_runtime_prompt(
            "shallow_recall_prompt",
            replacements={
                "<question_text>": question_text,
                "<search_result_json>": json.dumps(search_result, ensure_ascii=False, indent=2),
            },
        )


