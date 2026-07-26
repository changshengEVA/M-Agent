"""
Cross-turn working memory (WM): project controller tool results into compact entries
and render the tail for system-prompt injection. Does not participate in LT flush.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


def _compact_ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _truncate(text: str, limit: int) -> str:
    body = _compact_ws(text)
    if limit <= 0 or len(body) <= limit:
        return body
    return body[: max(0, limit - 3)].rstrip() + "..."


@dataclass(frozen=True)
class WorkingMemoryConfig:
    enable: bool = True
    inject_max_entries: int = 20
    max_stored_entries: int = 200
    max_question_chars: int = 512
    max_answer_chars: int = 1200
    max_email_ask_items: int = 20
    max_subject_chars: int = 120
    max_email_body_excerpt_chars: int = 1500
    max_schedule_summary_chars: int = 400
    max_web_answer_chars: int = 1000
    max_web_result_items: int = 5
    max_web_snippet_chars: int = 240
    record_time_tool: bool = True
    # Last N WM entries included in thread_state.working_memory.entries for UI (GET memory/state, SSE).
    ui_expose_max_entries: int = 200


def normalize_working_memory_config(raw: Any) -> WorkingMemoryConfig:
    if raw is None or raw is False:
        return WorkingMemoryConfig(enable=False)
    if not isinstance(raw, dict):
        return WorkingMemoryConfig()

    def _int(key: str, default: int) -> int:
        try:
            return max(0, int(raw.get(key, default)))
        except (TypeError, ValueError):
            return default

    return WorkingMemoryConfig(
        enable=bool(raw.get("enable", True)),
        inject_max_entries=max(1, _int("inject_max_entries", 20)),
        max_stored_entries=max(10, _int("max_stored_entries", 200)),
        max_question_chars=max(32, _int("max_question_chars", 512)),
        max_answer_chars=max(64, _int("max_answer_chars", 1200)),
        max_email_ask_items=max(1, min(50, _int("max_email_ask_items", 20))),
        max_subject_chars=max(16, _int("max_subject_chars", 120)),
        max_email_body_excerpt_chars=max(128, _int("max_email_body_excerpt_chars", 1500)),
        max_schedule_summary_chars=max(32, _int("max_schedule_summary_chars", 400)),
        max_web_answer_chars=max(128, _int("max_web_answer_chars", 1000)),
        max_web_result_items=max(1, min(20, _int("max_web_result_items", 5))),
        max_web_snippet_chars=max(64, _int("max_web_snippet_chars", 240)),
        record_time_tool=bool(raw.get("record_time_tool", True)),
        ui_expose_max_entries=max(0, min(5000, _int("ui_expose_max_entries", 200))),
    )


def build_working_memory_api_payload(
    entries: List[Dict[str, Any]],
    config: WorkingMemoryConfig,
    *,
    task_progress: Any = None,
) -> Dict[str, Any]:
    """Shape embedded under thread_state.working_memory for HTTP/SSE clients."""
    cap = max(0, int(config.ui_expose_max_entries))
    tail = copy.deepcopy(entries[-cap:]) if cap else []
    progress_payload = _task_progress_payload(task_progress)
    return {
        "enabled": config.enable,
        "stored_entries": len(entries),
        "inject_max_entries": config.inject_max_entries,
        "max_stored_entries": config.max_stored_entries,
        "ui_expose_max_entries": config.ui_expose_max_entries,
        "task_progress": progress_payload,
        "entries": tail,
    }


def _task_progress_payload(task_progress: Any) -> Dict[str, Any]:
    if task_progress is None:
        return {
            "goal": "",
            "completion_status": "processing",
            "completed": [],
            "remaining": [],
        }
    if hasattr(task_progress, "to_dict"):
        try:
            payload = task_progress.to_dict()
        except Exception:
            payload = {}
    elif isinstance(task_progress, dict):
        payload = task_progress
    else:
        payload = {
            "goal": getattr(task_progress, "goal", ""),
            "completion_status": getattr(
                task_progress, "completion_status", "processing"
            ),
            "completed": getattr(task_progress, "completed", []),
            "remaining": getattr(task_progress, "remaining", []),
        }
    completed = payload.get("completed", []) if isinstance(payload, dict) else []
    remaining = payload.get("remaining", []) if isinstance(payload, dict) else []
    return {
        "goal": str(payload.get("goal", "") or "").strip() if isinstance(payload, dict) else "",
        "completion_status": (
            str(payload.get("completion_status", "processing") or "processing").strip()
            if isinstance(payload, dict)
            else "processing"
        ),
        "completed": [
            str(item or "").strip()
            for item in (completed if isinstance(completed, list) else [])
            if str(item or "").strip()
        ],
        "remaining": [
            str(item or "").strip()
            for item in (remaining if isinstance(remaining, list) else [])
            if str(item or "").strip()
        ],
    }


def _task_progress_is_empty(task_progress: Any) -> bool:
    payload = _task_progress_payload(task_progress)
    return not (
        payload["goal"]
        or payload["completion_status"] != "processing"
        or payload["completed"]
        or payload["remaining"]
    )


def _format_task_progress(task_progress: Any, *, zh: bool) -> str:
    payload = _task_progress_payload(task_progress)
    lines: List[str] = ["[Task progress]"]
    if zh:
        lines = ["[浠诲姟杩涘害]"]
    goal = payload["goal"] or ("(empty)" if not zh else "(绌?)")
    lines.append(f"goal: {goal}")
    lines.append(f"completion_status: {payload['completion_status']}")
    completed = payload["completed"]
    remaining = payload["remaining"]
    lines.append("completed:")
    if completed:
        lines.extend(f"- {item}" for item in completed)
    else:
        lines.append("- (none)" if not zh else "- (鏃?)")
    lines.append("remaining:")
    if remaining:
        lines.extend(f"- {item}" for item in remaining)
    else:
        lines.append("- (none)" if not zh else "- (鏃?)")
    return "\n".join(lines)


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _project_limit_entry(tool_name: str, result: Dict[str, Any], config: WorkingMemoryConfig) -> Dict[str, Any]:
    msg = _truncate(str(result.get("message", "") or ""), 400)
    scope = str(result.get("limit_scope", "") or "").strip()
    return {
        "kind": "limit",
        "tool": tool_name,
        "limit_scope": scope,
        "summary": msg or "tool limit reached",
    }


def _project_recall(tool_name: str, params: Dict[str, Any], result: Dict[str, Any], config: WorkingMemoryConfig) -> Dict[str, Any]:
    question = _truncate(str(params.get("question", "") or ""), config.max_question_chars)
    answer = _truncate(str(result.get("answer", "") or ""), config.max_answer_chars)
    return {
        "kind": "recall",
        "tool": tool_name,
        "mode": tool_name,
        "question": question,
        "answer": answer,
    }


def _project_email_ask(params: Dict[str, Any], result: Dict[str, Any], config: WorkingMemoryConfig) -> Dict[str, Any]:
    items_out: List[Dict[str, str]] = []
    evidence_index = result.get("evidence_index")
    if isinstance(evidence_index, list):
        for row in evidence_index[: config.max_email_ask_items]:
            if not isinstance(row, dict):
                continue
            items_out.append(
                {
                    "message_id": str(row.get("message_id", "") or "").strip(),
                    "thread_id": str(row.get("thread_id", "") or "").strip(),
                    "subject": _truncate(str(row.get("subject", "") or ""), config.max_subject_chars),
                }
            )
    return {
        "kind": "email",
        "phase": "ask",
        "tool": "email_ask",
        "keywords": _truncate(str(params.get("keywords", "") or ""), 128),
        "mail_scope": str(params.get("mail_scope", "") or "").strip(),
        "items": items_out,
    }


def _project_email_read(params: Dict[str, Any], result: Dict[str, Any], config: WorkingMemoryConfig) -> Dict[str, Any]:
    messages = result.get("messages")
    bodies: List[str] = []
    subject = ""
    msg_id = str(params.get("message_id", "") or "").strip()
    thr_id = str(params.get("thread_id", "") or "").strip()
    if isinstance(messages, list):
        for idx, m in enumerate(messages):
            if not isinstance(m, dict):
                continue
            if idx == 0:
                subject = _truncate(str(m.get("subject", "") or ""), config.max_subject_chars)
                if not msg_id:
                    msg_id = str(m.get("message_id", "") or "").strip()
                if not thr_id:
                    thr_id = str(m.get("thread_id", "") or "").strip()
            bt = str(m.get("body_text", "") or "").strip()
            if bt:
                bodies.append(bt)
    excerpt_src = "\n\n".join(bodies).strip() or str(result.get("answer", "") or "").strip()
    body_excerpt = _truncate(excerpt_src, config.max_email_body_excerpt_chars)
    return {
        "kind": "email",
        "phase": "read",
        "tool": "email_read",
        "message_id": msg_id,
        "thread_id": thr_id,
        "subject": subject,
        "body_excerpt": body_excerpt,
        "message_count": int(result.get("message_count", 0) or 0) if isinstance(result.get("message_count"), int) else len(bodies),
    }


def _project_email_send(params: Dict[str, Any], result: Dict[str, Any], config: WorkingMemoryConfig) -> Dict[str, Any]:
    success = bool(result.get("success", False))
    status = str(result.get("status", "") or ("sent" if success else "failed")).strip()
    to_list = result.get("to")
    if isinstance(to_list, list) and to_list:
        to_summary = ", ".join(str(x) for x in to_list[:5])
        if len(to_list) > 5:
            to_summary += ", ..."
    else:
        to_summary = str(params.get("to", "") or "").strip()
    to_summary = _truncate(to_summary, 200)
    subject = _truncate(str(result.get("subject", "") or params.get("subject", "") or ""), config.max_subject_chars)
    try:
        content_len = int(params.get("content_length", 0) or 0)
    except (TypeError, ValueError):
        content_len = 0
    inner = result.get("result")
    inner_id = ""
    if isinstance(inner, dict):
        inner_id = str(inner.get("id", "") or inner.get("message_id", "") or "").strip()
    content_note = f"[正文已省略，约 {content_len} 字符]" if content_len > 0 else "[正文已省略]"
    return {
        "kind": "email",
        "phase": "send",
        "tool": "email_send",
        "to_summary": to_summary,
        "subject": subject,
        "content_note": content_note,
        "success": success,
        "status": status,
        "provider_message_id": inner_id,
    }


def _schedule_primary_id(result: Dict[str, Any]) -> str:
    item = result.get("item")
    if isinstance(item, dict):
        sid = str(item.get("schedule_id", "") or "").strip()
        if sid:
            return sid
    items = result.get("items")
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict):
            sid = str(first.get("schedule_id", "") or "").strip()
            if sid:
                return sid
    candidates = result.get("candidates")
    if isinstance(candidates, list) and candidates:
        first = candidates[0]
        if isinstance(first, dict):
            sid = str(first.get("schedule_id", "") or "").strip()
            if sid:
                return sid
    return ""


def _project_schedule(tool_name: str, params: Dict[str, Any], result: Dict[str, Any], config: WorkingMemoryConfig) -> Dict[str, Any]:
    action = str(result.get("action", "") or "").strip()
    summary = _truncate(str(result.get("answer", "") or result.get("message", "") or ""), config.max_schedule_summary_chars)
    schedule_id = _schedule_primary_id(result)
    extra = ""
    if tool_name == "schedule_query":
        kw = str(params.get("keyword", "") or "")
        start = str(params.get("start_at", "") or "")
        end = str(params.get("end_at", "") or "")
        extra = _truncate(f"{kw}|{start}|{end}".strip("|"), 200)
    elif tool_name == "schedule_create":
        extra = _truncate(
            f"{params.get('due_at', '')} {params.get('text', '')}".strip(),
            200,
        )
    elif tool_name == "schedule_delete":
        extra = _truncate(str(params.get("schedule_id", "") or ""), 80)
    return {
        "kind": "schedule",
        "tool": tool_name,
        "action": action,
        "schedule_id": schedule_id,
        "query_or_instruction": extra,
        "summary": summary,
        "count": int(result.get("count", 0) or 0) if isinstance(result.get("count"), int) else 0,
    }


def _project_time(params: Dict[str, Any], result: Dict[str, Any], config: WorkingMemoryConfig) -> Dict[str, Any]:
    if not result.get("ok", True):
        err = _truncate(str(result.get("error", "") or "time lookup failed"), 200)
        return {"kind": "time", "tool": "get_current_time", "summary": err}
    tz = str(result.get("timezone_name", "") or params.get("timezone_name", "") or "").strip()
    local_dt = str(result.get("local_datetime", "") or "").strip()
    summary = f"{tz} {local_dt}".strip() if tz or local_dt else json.dumps(result, ensure_ascii=False)[:300]
    return {"kind": "time", "tool": "get_current_time", "summary": _truncate(summary, 240)}


def _project_web_search(params: Dict[str, Any], result: Dict[str, Any], config: WorkingMemoryConfig) -> Dict[str, Any]:
    items_out: List[Dict[str, Any]] = []
    results = result.get("results")
    if isinstance(results, list):
        for row in results[: config.max_web_result_items]:
            if not isinstance(row, dict):
                continue
            item: Dict[str, Any] = {
                "title": _truncate(str(row.get("title", "") or ""), config.max_subject_chars),
                "url": _truncate(str(row.get("url", "") or ""), 300),
                "snippet": _truncate(str(row.get("snippet", "") or ""), config.max_web_snippet_chars),
            }
            if row.get("content_chars") is not None:
                try:
                    item["content_chars"] = int(row.get("content_chars") or 0)
                except (TypeError, ValueError):
                    item["content_chars"] = 0
            items_out.append(item)

    mode = str(result.get("mode", "") or "").strip()
    if not mode:
        mode = "extract" if str(result.get("url", "") or params.get("url", "") or "").strip() else "search"

    try:
        result_count = int(result.get("result_count", len(items_out)) or 0)
    except (TypeError, ValueError):
        result_count = len(items_out)

    content_chars = None
    if result.get("content_chars") is not None:
        try:
            content_chars = int(result.get("content_chars") or 0)
        except (TypeError, ValueError):
            content_chars = None

    return {
        "kind": "web_search",
        "tool": "web_search",
        "mode": mode,
        "provider": str(result.get("provider", "") or params.get("provider", "") or "").strip(),
        "query": _truncate(str(result.get("query", "") or params.get("query", "") or ""), config.max_question_chars),
        "url": _truncate(str(result.get("url", "") or params.get("url", "") or ""), 300),
        "answer": _truncate(str(result.get("answer", "") or result.get("message", "") or ""), config.max_web_answer_chars),
        "result_count": result_count,
        "content_chars": content_chars,
        "insufficient": bool(result.get("insufficient", False)),
        "needs_clarification": bool(result.get("needs_clarification", False)),
        "auth_required": bool(result.get("auth_required", False)),
        "items": items_out,
    }


def _project_fallback(tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    msg = result.get("message") or result.get("answer") or ""
    text = str(msg).strip() or json.dumps(result, ensure_ascii=False)[:400]
    return {"kind": "other", "tool": tool_name, "summary": _truncate(text, 400)}


def project_tool_call_to_entry(
    history_item: Dict[str, Any],
    config: WorkingMemoryConfig,
) -> Optional[Dict[str, Any]]:
    tool_name = str(history_item.get("tool_name", "") or "").strip()
    params = _safe_dict(history_item.get("params"))
    result = history_item.get("result")
    if not tool_name:
        return None
    if not isinstance(result, dict):
        return {
            "kind": "other",
            "tool": tool_name,
            "summary": _truncate(str(result), 320),
        }

    if result.get("limit_reached") is True:
        return _project_limit_entry(tool_name, result, config)

    if tool_name in {"shallow_recall", "deep_recall"}:
        return _project_recall(tool_name, params, result, config)
    if tool_name == "email_ask":
        return _project_email_ask(params, result, config)
    if tool_name == "email_read":
        return _project_email_read(params, result, config)
    if tool_name == "email_send":
        return _project_email_send(params, result, config)
    if tool_name in {"schedule_create", "schedule_query", "schedule_delete"}:
        return _project_schedule(tool_name, params, result, config)
    if tool_name == "get_current_time":
        if not config.record_time_tool:
            return None
        return _project_time(params, result, config)
    if tool_name == "web_search":
        return _project_web_search(params, result, config)

    return _project_fallback(tool_name, result)


def append_tool_history_to_working_memory(
    entries: List[Dict[str, Any]],
    controller_tool_history: Any,
    config: WorkingMemoryConfig,
) -> None:
    if not config.enable:
        return
    if not isinstance(controller_tool_history, list):
        return
    for item in controller_tool_history:
        if not isinstance(item, dict):
            continue
        projected = project_tool_call_to_entry(item, config)
        if projected:
            entries.append(projected)
    overflow = len(entries) - config.max_stored_entries
    if overflow > 0:
        del entries[0:overflow]


def _format_entry_line(index: int, entry: Dict[str, Any], *, zh: bool) -> str:
    kind = str(entry.get("kind", "") or "").strip()
    if kind == "recall":
        q = str(entry.get("question", "") or "")
        a = str(entry.get("answer", "") or "")
        mode = str(entry.get("mode", "") or "")
        if zh:
            return f"{index}. recall[{mode}] Q:{q} | A:{a}"
        return f"{index}. recall[{mode}] Q:{q} | A:{a}"
    if kind == "email" and entry.get("phase") == "ask":
        parts = []
        for it in entry.get("items", []) or []:
            if not isinstance(it, dict):
                continue
            mid = str(it.get("message_id", "") or "").strip()
            sid = str(it.get("thread_id", "") or "").strip()
            sub = str(it.get("subject", "") or "").strip()
            label = mid or sid or "?"
            parts.append(f"id={label} subj={sub}")
        joined = " ; ".join(parts) if parts else "(no items)"
        scope = str(entry.get("mail_scope", "") or "").strip()
        kw = str(entry.get("keywords", "") or "").strip()
        if zh:
            return f"{index}. email_ask scope={scope} kw={kw} | {joined}"
        return f"{index}. email_ask scope={scope} kw={kw} | {joined}"
    if kind == "email" and entry.get("phase") == "read":
        mid = str(entry.get("message_id", "") or "")
        tid = str(entry.get("thread_id", "") or "")
        sub = str(entry.get("subject", "") or "")
        body = str(entry.get("body_excerpt", "") or "")
        mc = entry.get("message_count", "")
        if zh:
            return f"{index}. email_read mid={mid} thread={tid} subj={sub} n={mc} | body:{body}"
        return f"{index}. email_read mid={mid} thread={tid} subj={sub} n={mc} | body:{body}"
    if kind == "email" and entry.get("phase") == "send":
        to_s = str(entry.get("to_summary", "") or "")
        sub = str(entry.get("subject", "") or "")
        st = str(entry.get("status", "") or "")
        ok = entry.get("success", False)
        note = str(entry.get("content_note", "") or "")
        pid = str(entry.get("provider_message_id", "") or "")
        if zh:
            return f"{index}. email_send to={to_s} subj={sub} ok={ok} status={st} {note} id={pid}"
        return f"{index}. email_send to={to_s} subj={sub} ok={ok} status={st} {note} id={pid}"
    if kind == "schedule":
        act = str(entry.get("action", "") or "")
        sid = str(entry.get("schedule_id", "") or "")
        sm = str(entry.get("summary", "") or "")
        qoi = str(entry.get("query_or_instruction", "") or "")
        cnt = entry.get("count", "")
        if zh:
            return f"{index}. schedule[{str(entry.get('tool', ''))}] action={act} id={sid} cnt={cnt} q={qoi} | {sm}"
        return f"{index}. schedule[{str(entry.get('tool', ''))}] action={act} id={sid} cnt={cnt} q={qoi} | {sm}"
    if kind == "time":
        return f"{index}. time: {str(entry.get('summary', '') or '')}"
    if kind == "web_search":
        mode = str(entry.get("mode", "") or "")
        provider = str(entry.get("provider", "") or "")
        query = str(entry.get("query", "") or "")
        url = str(entry.get("url", "") or "")
        answer = str(entry.get("answer", "") or "")
        cnt = entry.get("result_count", "")
        content_chars = entry.get("content_chars")
        flags = []
        if entry.get("auth_required"):
            flags.append("auth_required")
        if entry.get("needs_clarification"):
            flags.append("needs_clarification")
        if entry.get("insufficient"):
            flags.append("insufficient")
        facts = [f"mode={mode}", f"provider={provider}", f"cnt={cnt}"]
        if query:
            facts.append(f"q={query}")
        if url:
            facts.append(f"url={url}")
        if content_chars is not None:
            facts.append(f"content_chars={content_chars}")
        if flags:
            facts.append(f"flags={','.join(flags)}")

        parts = []
        for it in entry.get("items", []) or []:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title", "") or "")
            hit_url = str(it.get("url", "") or "")
            snippet = str(it.get("snippet", "") or "")
            label = title or hit_url or "result"
            hit = f"title={label}"
            if hit_url:
                hit += f" url={hit_url}"
            if snippet:
                hit += f" snippet={snippet}"
            if it.get("content_chars") is not None:
                hit += f" content_chars={it.get('content_chars')}"
            parts.append(hit)
        joined = " ; ".join(parts) if parts else "(no sources)"
        return f"{index}. web_search {' '.join(facts)} | answer:{answer} | sources:{joined}"
    if kind == "limit":
        return f"{index}. LIMIT tool={str(entry.get('tool', '') or '')} scope={str(entry.get('limit_scope', '') or '')} | {str(entry.get('summary', '') or '')}"
    return f"{index}. {str(entry.get('tool', '') or 'tool')}: {str(entry.get('summary', '') or '')}"


def format_working_memory_prompt(
    entries: List[Dict[str, Any]],
    config: WorkingMemoryConfig,
    *,
    prompt_language: str = "zh",
    task_progress: Any = None,
) -> str:
    if not config.enable:
        return ""
    zh = str(prompt_language or "zh").strip().lower().startswith("zh")
    has_progress = not _task_progress_is_empty(task_progress)
    if not entries and not has_progress:
        return ""
    tail = entries[-config.inject_max_entries :]
    evidence_header = "[Tool evidence]"
    if zh:
        header = "[工作记忆]"
        guide = (
            "以下为最近若干次顶层工具调用的摘要，仅供本轮推理使用；"
            "勿向用户复述全文或敏感细节；若与当前对话冲突，以对话为准。"
        )
    else:
        header = "[Working memory]"
        guide = (
            "Task progress is maintained by the thinking layer; tool evidence is recent tool output for reasoning only. "
            "do not repeat verbatim to the user; if this conflicts with the dialogue, prefer the dialogue."
        )
    sections = [header, guide]
    if has_progress:
        sections.append(_format_task_progress(task_progress, zh=zh))
    if tail:
        lines = [_format_entry_line(i + 1, e, zh=zh) for i, e in enumerate(tail)]
        sections.append("\n".join([evidence_header, *lines]))
    return "\n".join(sections).strip()
