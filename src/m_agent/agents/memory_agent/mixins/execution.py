from __future__ import annotations

import logging
import time
import json
from typing import Any, Dict, List, Optional

from ..action_executor import execute_actions
from ..action_planner import (
    action_signature,
    build_query_intent,
    intent_to_question_plan,
    plan_actions_llm,
    plan_actions_rule_based,
    tool_registry_for_prompt,
)
from ..answerability import JudgeDecision, llm_judge_workspace
from ..workspace import Workspace
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

    def _finalize_recall_payload(
        self,
        payload: Dict[str, Any],
        *,
        question_plan: Dict[str, Any],
        sub_question_results: List[Dict[str, Any]],
        tool_calls: List[Dict[str, Any]],
        kept_evidence_ids: List[str] | None = None,
    ) -> Dict[str, Any]:
        """补全 payload 的证据、规划和追踪字段。"""
        safe_tool_calls = [self._safe_trace_value(call) for call in tool_calls]
        payload["tool_calls"] = safe_tool_calls
        payload["tool_call_count"] = len(tool_calls)
        if kept_evidence_ids is not None:
            episode_refs = sorted(kept_evidence_ids)
        else:
            episode_refs = self._collect_episode_refs_from_tool_calls(safe_tool_calls)
        payload["evidence_episode_refs"] = episode_refs
        payload["evidence_episode_ref_count"] = len(episode_refs)
        payload = self._append_episode_refs_to_payload(payload, episode_refs)
        payload["question_plan"] = question_plan
        payload["sub_question_results"] = sub_question_results
        payload.pop("sub_questions", None)
        payload.pop("plan_summary", None)
        self._log_structured_trace(
            self._TRACE_PREFIX_FINAL_PAYLOAD,
            payload,
        )
        self._last_tool_calls = safe_tool_calls
        return payload

    def _build_insufficient_payload(self, question_text: str, workspace: Workspace) -> Dict[str, Any]:
        prefer_zh = bool(self._CJK_PATTERN.search(question_text or ""))
        if prefer_zh:
            answer = "我目前没有足够的记忆证据来确认这个问题。"
            evidence = workspace.to_evidence_summary() or "没有检索到可用的 Episode 证据。"
        else:
            answer = "I do not have enough memory evidence to answer this confidently."
            evidence = workspace.to_evidence_summary() or "No usable episode evidence was found."
        return {
            "answer": answer,
            "gold_answer": None,
            "evidence": evidence,
        }

    def _build_final_answer_from_workspace_prompt(
        self,
        *,
        question_text: str,
        workspace: Workspace,
    ) -> str:
        return self._render_runtime_prompt(
            "final_answer_from_workspace_prompt",
            replacements={
                "<question_text>": question_text,
                "<workspace_evidence_summary>": workspace.to_evidence_summary(),
            },
        )

    def _generate_final_payload_from_workspace(
        self,
        *,
        question_text: str,
        question_plan: Dict[str, Any],
        workspace: Workspace,
    ) -> Dict[str, Any]:
        response = self._invoke_model_with_network_retry(
            prompt_text=self._build_final_answer_from_workspace_prompt(
                question_text=question_text,
                workspace=workspace,
            ),
            call_name="final_answer_from_workspace",
        )
        payload = self._payload_from_model_response(response)
        payload.pop("sub_questions", None)
        payload.pop("plan_summary", None)
        self._promote_short_answer_to_gold(payload)
        if not payload.get("evidence"):
            payload["evidence"] = workspace.to_evidence_summary()
        return payload

    # ------------------------------------------------------------------
    # LLM action planner
    # ------------------------------------------------------------------

    def _build_action_plan_prompt(
        self,
        workspace: Workspace,
        previous_action_signatures: set[str],
        force_remedy: bool,
    ) -> str:
        tools = tool_registry_for_prompt(
            force_remedy=force_remedy,
            registry=getattr(self, "tool_registry", None),
        )
        workspace_summary = workspace.to_evidence_summary() or "(no evidence collected yet)"
        prev_actions = sorted(previous_action_signatures) if previous_action_signatures else []
        return self._render_runtime_prompt(
            "action_plan_prompt",
            replacements={
                "<original_question>": workspace.original_question,
                "<cur_query>": workspace.cur_query,
                "<workspace_summary>": workspace_summary,
                "<previous_actions_json>": json.dumps(prev_actions, ensure_ascii=False),
                "<available_tools_json>": json.dumps(tools, ensure_ascii=False, indent=2),
            },
        )

    def _plan_actions_with_llm(
        self,
        workspace: Workspace,
        *,
        round_id: int,
        max_actions: int,
        force_remedy: bool,
        previous_action_signatures: set[str],
    ) -> List[Dict[str, Any]]:
        prompt_text = self._build_action_plan_prompt(
            workspace, previous_action_signatures, force_remedy,
        )
        return plan_actions_llm(
            llm_func=lambda text: self._invoke_model_with_network_retry(
                prompt_text=text,
                call_name="action_planner",
            ),
            prompt_text=prompt_text,
            round_id=round_id,
            max_actions=max_actions,
            previous_action_signatures=previous_action_signatures,
        )

    # ------------------------------------------------------------------
    # Rerank helpers
    # ------------------------------------------------------------------

    def _rerank_new_evidences(
        self,
        workspace: Workspace,
        cur_query: str,
        new_evidence_ids: List[str],
    ) -> List[str]:
        """Rerank only the newly added evidence using the current query.

        Drops evidences whose rerank score is strictly below ``rerank_score_threshold``
        (config ``workspace.rerank.score_threshold``). Returns ``new_evidence_ids`` with
        removed ids filtered out so downstream judge sees only surviving new docs.
        """
        rerank_func = getattr(self, "rerank_func", None)
        if not rerank_func or not new_evidence_ids:
            return list(new_evidence_ids)

        max_docs = max(1, int(getattr(self, "rerank_max_documents", 16)))
        ids_to_rank = new_evidence_ids[:max_docs]

        all_docs = {doc["evidence_id"]: doc for doc in workspace.all_evidences()}
        documents: List[str] = []
        ordered_ids: List[str] = []
        for eid in ids_to_rank:
            doc = all_docs.get(eid)
            if doc is None:
                continue
            content = str(doc.get("content", "") or "").strip()
            if not content:
                continue
            documents.append(content)
            ordered_ids.append(eid)

        if not documents:
            return [eid for eid in new_evidence_ids if workspace.has_evidence(eid)]

        threshold = float(getattr(self, "rerank_score_threshold", 0.0))

        try:
            logger.info(
                "API call: rerank(query_len=%d, doc_count=%d)",
                len(cur_query),
                len(documents),
            )
            results = rerank_func(cur_query, documents, len(documents))
            for item in results:
                idx = int(item.get("index", -1))
                score = float(item.get("relevance_score", 0.0))
                if 0 <= idx < len(ordered_ids):
                    workspace.set_rerank_score(ordered_ids[idx], score)
            logger.info(
                "API response: rerank(scored=%d)",
                len(results),
            )
            # Drop reranked docs below threshold; unranked (beyond max_docs) keep recall only.
            dropped = 0
            for eid in ordered_ids:
                doc = workspace.get_document(eid)
                if doc is None:
                    continue
                rr = doc.get("rerank_score")
                if rr is not None and float(rr) < threshold:
                    workspace.remove_evidence(eid)
                    dropped += 1
            if dropped:
                logger.info(
                    "Rerank threshold %.4f: dropped %d below-threshold evidence(s)",
                    threshold,
                    dropped,
                )
        except Exception as exc:
            logger.warning("Rerank failed, falling back to recall scores: %s", exc)

        return [eid for eid in new_evidence_ids if workspace.has_evidence(eid)]

    # ------------------------------------------------------------------
    # LLM judge helpers
    # ------------------------------------------------------------------

    def _build_workspace_judge_prompt(
        self,
        workspace: Workspace,
        new_evidence_ids: List[str],
    ) -> str:
        return self._render_runtime_prompt(
            "workspace_judge_prompt",
            replacements={
                "<original_question>": workspace.original_question,
                "<cur_query>": workspace.cur_query,
                "<workspace_evidence_summary>": workspace.to_evidence_summary(),
                "<new_evidence_ids_json>": json.dumps(new_evidence_ids, ensure_ascii=False),
            },
        )

    def _run_llm_judge(
        self,
        workspace: Workspace,
        new_evidence_ids: List[str],
    ) -> "JudgeDecision":
        prompt_text = self._build_workspace_judge_prompt(workspace, new_evidence_ids)
        return llm_judge_workspace(
            workspace=workspace,
            new_evidence_ids=new_evidence_ids,
            llm_func=lambda text: self._invoke_model_with_network_retry(
                prompt_text=text,
                call_name="workspace_judge",
            ),
            prompt_text=prompt_text,
        )

    # ------------------------------------------------------------------
    # Main state-machine loop
    # ------------------------------------------------------------------

    def _run_state_machine_recall(
        self,
        *,
        question_text: str,
        max_rounds: int,
    ) -> Dict[str, Any]:
        query_intent = build_query_intent(question_text)
        question_plan = intent_to_question_plan(query_intent)
        self._last_question_plan = question_plan

        workspace = Workspace(max_keep=max(1, int(getattr(self, "workspace_max_keep", 6))))
        workspace.original_question = question_text
        workspace.cur_query = question_text

        round_traces: List[Dict[str, Any]] = []
        previous_action_signatures: set[str] = set()
        remedy_used = 0
        last_status = "INIT"
        max_actions_per_round = max(1, int(getattr(self, "workspace_max_actions_per_round", 4)))
        max_episode_candidates = max(1, int(getattr(self, "workspace_max_episode_candidates", 12)))
        remedy_limit = max(0, int(getattr(self, "workspace_remedy_recall_max_times", 1)))
        detail_defaults = getattr(self, "detail_search_defaults", {"topk": 5})
        default_topk = max(1, int(detail_defaults.get("topk", 5)))
        for round_id in range(1, max(1, int(max_rounds)) + 1):
            workspace.set_round(round_id)
            cur_query = workspace.cur_query

            force_remedy = (
                last_status == "INVALID"
                and remedy_used < remedy_limit
            )
            if force_remedy:
                remedy_used += 1
            self._log_structured_trace(
                self._TRACE_PREFIX_WORKSPACE_STATE,
                {
                    "phase": "round_started",
                    "round_id": round_id,
                    "cur_query": cur_query,
                    "force_remedy": force_remedy,
                    "last_status": last_status,
                    "workspace": workspace.snapshot(),
                },
            )

            use_llm_planner = str(getattr(self, "action_planner_mode", "rule")).strip().lower() == "llm"
            actions = None
            if use_llm_planner:
                try:
                    actions = self._plan_actions_with_llm(
                        workspace,
                        round_id=round_id,
                        max_actions=max_actions_per_round,
                        force_remedy=force_remedy,
                        previous_action_signatures=previous_action_signatures,
                    )
                except Exception as exc:
                    logger.warning("LLM action planner failed, falling back to rules: %s", exc)
                    actions = None
            if not actions:
                round_intent = build_query_intent(cur_query)
                actions = plan_actions_rule_based(
                    round_intent,
                    round_id=round_id,
                    topk=default_topk,
                    max_actions=max_actions_per_round,
                    force_remedy=force_remedy,
                    previous_action_signatures=previous_action_signatures,
                )
            if not actions:
                workspace.mark("INVALID", "no_new_actions")
                trace_item = {
                    "round_id": round_id,
                    "status": "INVALID",
                    "gap_type": "no_new_actions",
                    "reason": "No non-duplicate actions generated.",
                    "actions": [],
                }
                round_traces.append(trace_item)
                self._log_structured_trace(
                    self._TRACE_PREFIX_WORKSPACE_STATE,
                    {
                        "phase": "round_judged",
                        **trace_item,
                        "workspace": workspace.snapshot(),
                    },
                )
                break

            for action in actions:
                previous_action_signatures.add(action_signature(action))

            report = execute_actions(
                actions=actions,
                round_id=round_id,
                search_details=lambda detail, topk: self._search_details_with_trace(detail=detail, topk=topk),
                search_details_multi_route=lambda detail, topk: self._search_details_multi_route_with_trace(
                    detail=detail,
                    topk=topk,
                ),
                search_events_by_time_range=lambda start_time, end_time: self._execute_traced_tool_call(
                    tool_name="search_events_by_time_range",
                    params={"start_time": start_time, "end_time": end_time},
                    call_log="API call: search_events_by_time_range(start_time=%s, end_time=%s)",
                    call_log_args=(start_time, end_time),
                    invoke=lambda: self.memory_sys.search_events_by_time_range(
                        start_time=start_time,
                        end_time=end_time,
                    ),
                    response_log="API response: search_events_by_time_range(result_count=%s)",
                    response_log_args=lambda result: (len(result) if isinstance(result, list) else None,),
                ),
                search_contents_by_episode_refs=lambda refs: self._search_contents_by_episode_refs_with_trace(refs),
                search_entity_feature=lambda eid, fq, topk: self._execute_traced_tool_call(
                    tool_name="search_entity_feature",
                    params={"entity_id": eid, "feature_query": fq, "topk": topk},
                    call_log="API call: search_entity_feature(entity_id=%s, feature_query=%s, topk=%s)",
                    call_log_args=(eid, fq, topk),
                    invoke=lambda: self.memory_sys.search_entity_feature(
                        entity_id=eid, feature_query=fq, topk=topk,
                    ),
                    response_log="API response: search_entity_feature(hit=%s, matched_count=%s)",
                    response_log_args=lambda result: (
                        self._dict_field(result, "hit"),
                        self._dict_field(result, "matched_count"),
                    ),
                ) if hasattr(self.memory_sys, "search_entity_feature") else None,
                search_entity_event=lambda eid, eq, topk: self._execute_traced_tool_call(
                    tool_name="search_entity_event",
                    params={"entity_id": eid, "event_query": eq, "topk": topk},
                    call_log="API call: search_entity_event(entity_id=%s, event_query=%s, topk=%s)",
                    call_log_args=(eid, eq, topk),
                    invoke=lambda: self.memory_sys.search_entity_event(
                        entity_id=eid, event_query=eq, topk=topk,
                    ),
                    response_log="API response: search_entity_event(hit=%s, matched_count=%s)",
                    response_log_args=lambda result: (
                        self._dict_field(result, "hit"),
                        self._dict_field(result, "matched_count"),
                    ),
                ) if hasattr(self.memory_sys, "search_entity_event") else None,
                max_episode_candidates=max_episode_candidates,
            )

            new_evidence_ids = workspace.extend_and_track_new(report["evidences"])
            new_evidence_ids = self._rerank_new_evidences(
                workspace, cur_query, new_evidence_ids
            )
            workspace.keep_top(
                max(1, int(getattr(self, "workspace_max_keep", 6))),
                protected_ids=workspace.kept_evidence_ids,
            )

            decision = self._run_llm_judge(workspace, new_evidence_ids)
            workspace.mark(decision["status"], decision.get("gap_type"))
            last_status = decision["status"]

            if decision.get("useful_evidence_ids"):
                workspace.kept_evidence_ids = decision["useful_evidence_ids"]

            if decision["status"] == "INSUFFICIENT" and decision.get("next_query"):
                workspace.cur_query = decision["next_query"]

            trace_item = {
                "round_id": round_id,
                "status": decision["status"],
                "gap_type": decision.get("gap_type"),
                "reason": decision.get("reason"),
                "next_query": decision.get("next_query"),
                "action_types": [str(action.get("action_type", "")) for action in actions],
                "episode_ref_count": len(report.get("episode_refs", [])),
                "kept_evidence_count": len(workspace.kept_evidence_ids),
                "useful_evidence_count": len(decision.get("useful_evidence_ids", [])),
            }
            round_traces.append(trace_item)
            self._log_structured_trace(
                self._TRACE_PREFIX_WORKSPACE_STATE,
                {
                    "phase": "round_judged",
                    **trace_item,
                    "workspace": workspace.snapshot(),
                },
            )

            if decision["status"] == "SUFFICIENT":
                break
            if decision["status"] == "INVALID" and remedy_used >= remedy_limit:
                break
            if round_id >= max(1, int(max_rounds)):
                break

        if workspace.status == "SUFFICIENT":
            payload = self._generate_final_payload_from_workspace(
                question_text=question_text,
                question_plan=question_plan,
                workspace=workspace,
            )
        elif workspace.kept_evidence_ids:
            logger.info(
                "Workspace status is %s but %d kept evidence(s) exist; generating best-effort answer",
                workspace.status,
                len(workspace.kept_evidence_ids),
            )
            payload = self._generate_final_payload_from_workspace(
                question_text=question_text,
                question_plan=question_plan,
                workspace=workspace,
            )
        else:
            payload = self._build_insufficient_payload(question_text, workspace)

        self._log_structured_trace(
            self._TRACE_PREFIX_WORKSPACE_STATE,
            {
                "phase": "finalized",
                "status": workspace.status,
                "gap_type": workspace.gap_type,
                "workspace": workspace.snapshot(),
            },
        )

        tool_calls = self._consume_current_tool_calls()
        return self._finalize_recall_payload(
            payload,
            question_plan=question_plan,
            sub_question_results=round_traces,
            tool_calls=tool_calls,
            kept_evidence_ids=list(workspace.kept_evidence_ids),
        )

    def shallow_recall(self, question: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
        """Run single-round state-machine recall."""
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")
        self._reset_round_state()
        try:
            return self._run_state_machine_recall(
                question_text=question.strip(),
                max_rounds=1,
            )
        except Exception:
            self._last_tool_calls = self._consume_current_tool_calls()
            raise

    def deep_recall(self, question: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
        """Run multi-round state-machine recall."""
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")
        self._reset_round_state()
        try:
            max_rounds = max(1, int(getattr(self, "workspace_max_rounds", 2)))
            return self._run_state_machine_recall(
                question_text=question.strip(),
                max_rounds=max_rounds,
            )
        except Exception:
            self._last_tool_calls = self._consume_current_tool_calls()
            raise
    def ask(self, question: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
        """兼容入口：转发到 deep_recall。"""
        return self.deep_recall(question=question, thread_id=thread_id)

