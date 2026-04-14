from __future__ import annotations

import json
import logging
import re
import time
from calendar import monthrange
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryAgentStateMixin:
    @staticmethod
    def _is_unanswerable_text(text: Any) -> bool:
        """判断文本是否为“信息不足/无法回答”。"""
        if not isinstance(text, str):
            return False
        normalized = text.strip().lower()
        if not normalized:
            return True
        markers = (
            "cannot determine",
            "can't determine",
            "cannot answer",
            "can't answer",
            "insufficient evidence",
            "not enough information",
            "no information",
            "no relevant information",
            "not mentioned",
            "unknown",
            "\u65e0\u6cd5\u786e\u5b9a",
            "\u65e0\u6cd5\u56de\u7b54",
            "\u4fe1\u606f\u4e0d\u8db3",
            "\u6ca1\u6709\u8db3\u591f\u4fe1\u606f",
            "\u672a\u63d0\u53ca",
        )
        return any(marker in normalized for marker in markers)
    @classmethod
    def _normalize_output(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        """标准化输出字段，并处理 gold_answer 一致性。"""
        answer_text = payload.get("answer")
        gold_answer = payload.get("gold_answer")

        if isinstance(answer_text, str):
            answer_text = cls._absolutize_relative_time(answer_text).strip()
            payload["answer"] = answer_text

        if isinstance(gold_answer, str):
            gold_answer = cls._absolutize_relative_time(gold_answer).strip() or None
        payload["gold_answer"] = gold_answer

        if cls._is_unanswerable_text(answer_text):
            payload["gold_answer"] = None

        return payload
    @classmethod
    def _absolutize_relative_time(cls, text: str) -> str:
        """将相对月份表达改写为绝对月份。"""
        if not isinstance(text, str) or not text.strip():
            return text

        def _replace(match: re.Match[str]) -> str:
            """基于锚点日期计算替换后的月份字符串。"""
            direction = (match.group("direction") or "").strip().lower()
            anchor_text = (match.group("anchor") or "").strip()
            anchor_dt = cls._parse_anchor_date(anchor_text)
            if anchor_dt is None:
                return match.group(0)

            delta = 1 if direction == "next" else -1
            shifted = cls._shift_month(anchor_dt, delta)
            return shifted.strftime("%B %Y")

        return cls._RELATIVE_MONTH_FROM_PATTERN.sub(_replace, text)
    @staticmethod
    def _parse_anchor_date(text: str) -> Optional[datetime]:
        """解析锚点日期字符串。"""
        cleaned = (text or "").strip()
        if not cleaned:
            return None

        formats = (
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%B %d, %Y",
            "%B %d %Y",
            "%b %d, %Y",
            "%b %d %Y",
        )
        for fmt in formats:
            try:
                return datetime.strptime(cleaned, fmt)
            except Exception:
                continue
        return None
    @staticmethod
    def _shift_month(dt: datetime, delta: int) -> datetime:
        """按月偏移日期，并处理日子越界。"""
        month_index = dt.month - 1 + delta
        year = dt.year + month_index // 12
        month = month_index % 12 + 1
        day = min(dt.day, monthrange(year, month)[1])
        return dt.replace(year=year, month=month, day=day)
    @staticmethod
    def _safe_trace_value(value: Any, depth: int = 0) -> Any:
        """将复杂对象转成可记录的安全结构。"""
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if depth > 24:
            return "<max_depth>"
        if is_dataclass(value):
            return MemoryAgentStateMixin._safe_trace_value(asdict(value), depth=depth + 1)
        if isinstance(value, dict):
            return {
                str(k): MemoryAgentStateMixin._safe_trace_value(v, depth=depth + 1)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [MemoryAgentStateMixin._safe_trace_value(v, depth=depth + 1) for v in value]
        return str(value)
    def _log_structured_trace(self, prefix: str, payload: Dict[str, Any]) -> None:
        """按统一前缀记录结构化日志。"""
        logger.info(
            "%s%s",
            prefix,
            json.dumps(self._safe_trace_value(payload), ensure_ascii=False),
        )
    def _record_tool_call(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """记录一次工具调用的起始信息。"""
        self._tool_call_seq += 1
        entry = {
            "call_id": self._tool_call_seq,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tool_name": str(tool_name),
            "params": self._safe_trace_value(params),
            "status": "started",
        }
        self._current_tool_calls.append(entry)
        self._log_structured_trace(self._TRACE_PREFIX_TOOL_CALL, entry)
        return entry
    def _finalize_tool_call(
        self,
        entry: Dict[str, Any],
        *,
        result: Any = None,
        error: BaseException | None = None,
    ) -> None:
        """更新工具调用的成功或失败状态。"""
        if error is None:
            entry["status"] = "completed"
            entry["result"] = self._safe_trace_value(result)
        else:
            entry["status"] = "failed"
            entry["error"] = str(error)
        self._log_structured_trace(
            self._TRACE_PREFIX_TOOL_RESULT,
            entry,
        )
    def _consume_current_tool_calls(self) -> List[Dict[str, Any]]:
        """取出当前轮工具调用并清空缓存。"""
        calls = self._current_tool_calls
        self._current_tool_calls = []
        return calls
    def get_last_tool_calls(self) -> List[Dict[str, Any]]:
        """返回上一轮工具调用记录。"""
        return [self._safe_trace_value(call) for call in self._last_tool_calls]
    def get_last_question_plan(self) -> Optional[Dict[str, Any]]:
        """返回上一轮问题规划。"""
        if not isinstance(self._last_question_plan, dict):
            return None
        return {
            str(k): self._safe_trace_value(v)
            for k, v in self._last_question_plan.items()
        }
    def _resolve_topk(self, topk: Optional[int]) -> int:
        """解析 topk 参数，缺省时使用默认值。"""
        return self.detail_search_defaults["topk"] if topk is None else int(topk)
    @staticmethod
    def _is_successful_tool_call(call: Dict[str, Any]) -> bool:
        """判断工具调用是否成功且未被阻断。"""
        status = str(call.get("status", "") or "").strip().lower()
        if status and status != "completed":
            return False

        result = call.get("result")
        if isinstance(result, dict):
            if bool(result.get("blocked", False)):
                return False
            if "hit" in result and not bool(result.get("hit")):
                return False
            if "success" in result and not bool(result.get("success")):
                return False
        return True
    @classmethod
    def _collect_episode_refs_from_value(cls, value: Any, refs: set[str]) -> None:
        """递归提取 value 中的片段引用。"""
        if isinstance(value, dict):
            dialogue_id = str(value.get("dialogue_id", "") or "").strip()
            episode_id = str(value.get("episode_id", "") or "").strip()
            if dialogue_id and episode_id:
                refs.add(f"{dialogue_id}:{episode_id}")
            for child in value.values():
                cls._collect_episode_refs_from_value(child, refs)
            return

        if isinstance(value, (list, tuple, set)):
            for item in value:
                cls._collect_episode_refs_from_value(item, refs)
    def _collect_episode_refs_from_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> List[str]:
        """从工具调用结果汇总片段引用。"""
        refs: set[str] = set()
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            if not self._is_successful_tool_call(call):
                continue
            self._collect_episode_refs_from_value(call.get("result"), refs)
            # search_content 这类工具会在 params 里直接带 dialogue_id/episode_id
            self._collect_episode_refs_from_value(call.get("params"), refs)
        return sorted(refs)
    def _format_episode_refs_line(self, refs: List[str], text_hint: str = "") -> str:
        """将片段引用格式化为展示行。"""
        if not refs:
            return ""
        max_items = max(1, int(getattr(self, "evidence_episode_ref_max_in_text", 8)))
        shown = refs[:max_items]
        remaining = max(len(refs) - len(shown), 0)
        prefer_zh = bool(self._CJK_PATTERN.search(text_hint or ""))
        if prefer_zh:
            suffix = f"（另有{remaining}个）" if remaining > 0 else ""
            return f"证据片段编号: {', '.join(shown)}{suffix}"
        suffix = f" (+{remaining} more)" if remaining > 0 else ""
        return f"Evidence episode snippets: {', '.join(shown)}{suffix}"
    def _append_episode_refs_to_payload(
        self,
        payload: Dict[str, Any],
        refs: List[str],
    ) -> Dict[str, Any]:
        """把片段引用追加到 payload 输出。"""
        if not refs:
            return payload

        answer_text = str(payload.get("answer", "") or "").strip()
        evidence_text = str(payload.get("evidence", "") or "").strip()
        line = self._format_episode_refs_line(refs, text_hint=f"{answer_text}\n{evidence_text}")
        if not line:
            return payload

        if bool(getattr(self, "attach_episode_refs_to_answer", True)) and line not in answer_text:
            payload["answer"] = f"{answer_text}\n{line}".strip() if answer_text else line
        if line not in evidence_text:
            payload["evidence"] = f"{evidence_text}\n{line}".strip() if evidence_text else line
        return payload
    def _reset_round_state(self) -> None:
        """重置单轮问答的临时状态。"""
        self._current_tool_calls = []
        self._last_tool_calls = []
        self._last_question_plan = None
        self._active_search_scope = "global"

