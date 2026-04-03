from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml
from langchain.tools import tool

from m_agent.config_paths import DEFAULT_EMAIL_AGENT_CONFIG_PATH, resolve_config_path, resolve_related_config_path
from m_agent.integrations.gmail_client import GmailApiClient, GmailClientConfig


logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = DEFAULT_EMAIL_AGENT_CONFIG_PATH
_HEADER_KEYS = ("Subject", "From", "To", "Date")
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SENSITIVE_PATTERN = re.compile(
    r"(\b\d{15,19}\b|\bpassword\b|\bpasswd\b|\bsecret\b)",
    flags=re.IGNORECASE,
)
_QUOTED_TERM_PATTERN = re.compile(r"[\"']([^\"']{1,60})[\"']")
_TOKEN_SPLIT_PATTERN = re.compile(r"[\uFF0C\u3002\uFF01\uFF1F\u3001,.;:()\uFF08\uFF09\[\]{}<>\u300A\u300B\s]+")
_QUERY_NOISE_PHRASES: tuple[str, ...] = (
    "\u5e2e\u6211",  # 帮我
    "\u8bf7\u5e2e\u6211",  # 请帮我
    "\u8bf7",  # 请
    "\u9ebb\u70e6",  # 麻烦
    "\u770b\u770b",  # 看看
    "\u770b\u4e0b",  # 看下
    "\u67e5\u4e0b",  # 查下
    "\u67e5\u4e00\u4e0b",  # 查一下
    "\u67e5\u8be2",  # 查询
    "\u6709\u6ca1\u6709",  # 有没有
    "\u6709\u65e0",  # 有无
    "\u76f8\u5173",  # 相关
    "\u90ae\u4ef6",  # 邮件
    "\u90ae\u7bb1",  # 邮箱
    "\u5185\u5bb9",  # 内容
    "\u6d88\u606f",  # 消息
    "\u4e00\u4e0b",  # 一下
    "\u5e2e\u5fd9",  # 帮忙
)
_QUERY_HINT_TERMS: tuple[str, ...] = (
    "\u5b9e\u4e60",  # 实习
    "\u62db\u8058",  # 招聘
    "\u6821\u62db",  # 校招
    "\u793e\u62db",  # 社招
    "\u5185\u63a8",  # 内推
    "\u9762\u8bd5",  # 面试
    "\u7b14\u8bd5",  # 笔试
    "offer",
    "intern",
    "internship",
    "job",
    "career",
    "\u7b80\u5386",  # 简历
)


class EmailAgent:
    """Standalone Gmail EmailAgent with two public interfaces: ask and send."""

    def __init__(self, config_path: str | Path = DEFAULT_CONFIG_PATH, *, gmail_client: Optional[GmailApiClient] = None) -> None:
        self.config_path = resolve_config_path(config_path)
        self.config = self._load_config(self.config_path)
        self.query_defaults = self._load_query_defaults(self.config.get("query_defaults"))
        self.execution_config = self._load_execution_config(self.config.get("execution"))

        self._tool_call_seq = 0
        self._current_tool_trace: List[Dict[str, Any]] = []
        self._last_tool_trace: List[Dict[str, Any]] = []

        self.gmail_client = gmail_client or self._build_gmail_client()
        self.tools = self._build_tools()

    @staticmethod
    def _load_config(path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"EmailAgent config not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        if not isinstance(config, dict):
            raise ValueError(f"EmailAgent config must be a dict: {path}")
        if str(config.get("provider", "") or "").strip().lower() != "gmail":
            raise ValueError("Only Gmail provider is supported currently (`provider: gmail`).")
        if not isinstance(config.get("gmail"), dict):
            raise ValueError("`gmail` section is required in EmailAgent config.")
        return config

    def _resolve_optional_related_path(self, raw_path: Any) -> Optional[Path]:
        if raw_path is None or not str(raw_path).strip():
            return None
        return resolve_related_config_path(self.config_path, raw_path)

    def _build_gmail_client(self) -> GmailApiClient:
        gmail_cfg = dict(self.config.get("gmail") or {})
        oauth_cfg = dict(gmail_cfg.get("oauth") or {})
        scopes_raw = gmail_cfg.get("scopes")
        scopes = tuple(str(x).strip() for x in scopes_raw if str(x).strip()) if isinstance(scopes_raw, list) else ()
        client_config = GmailClientConfig(
            user_id=str(gmail_cfg.get("user_id", "me")).strip() or "me",
            credentials_path=self._resolve_optional_related_path(gmail_cfg.get("credentials_path")),
            token_path=self._resolve_optional_related_path(gmail_cfg.get("token_path")),
            scopes=scopes or GmailClientConfig().scopes,
            allow_local_webserver_flow=bool(oauth_cfg.get("allow_local_webserver_flow", True)),
            allow_console_flow=bool(oauth_cfg.get("allow_console_flow", False)),
        )
        return GmailApiClient(config=client_config)

    @staticmethod
    def _load_query_defaults(raw: Any) -> Dict[str, int]:
        defaults = {
            "recall_thread_topk": 40,
            "recall_message_topk": 60,
            "recall_expand_threads": 8,
            "recall_per_thread_message_limit": 6,
        }
        if isinstance(raw, dict):
            for key in defaults:
                value = raw.get(key)
                if value is None:
                    continue
                try:
                    defaults[key] = max(1, int(value))
                except Exception:
                    continue
        return defaults

    @staticmethod
    def _load_execution_config(raw: Any) -> Dict[str, Any]:
        defaults: Dict[str, Any] = {
            "allow_external_recipient": True,
            "allowed_recipient_domains": [],
            "block_on_risk_flags": False,
        }
        if isinstance(raw, dict):
            defaults.update(raw)
        defaults["allow_external_recipient"] = bool(defaults.get("allow_external_recipient", True))
        defaults["block_on_risk_flags"] = bool(defaults.get("block_on_risk_flags", False))
        allowed = defaults.get("allowed_recipient_domains")
        if not isinstance(allowed, list):
            allowed = []
        defaults["allowed_recipient_domains"] = sorted({str(x).strip().lower() for x in allowed if str(x).strip()})
        return defaults

    def _build_tools(self) -> List[Any]:
        @tool("ask", description="Search Gmail by instruction. mail_scope supports: unread or all.")
        def ask_tool(instruction: str, mail_scope: str = "unread", debug: bool = False) -> Dict[str, Any]:
            return self.ask(instruction=instruction, mail_scope=mail_scope, debug=debug)

        @tool("send", description="Send email directly. content is body text, to is recipient email or comma-separated emails.")
        def send_tool(
            content: str,
            to: str,
            subject: str = "",
            cc: str = "",
            bcc: str = "",
            body_html: Optional[str] = None,
            reply_to: Optional[str] = None,
        ) -> Dict[str, Any]:
            return self.send(
                content=content,
                to=to,
                subject=subject,
                cc=cc,
                bcc=bcc,
                body_html=body_html,
                reply_to=reply_to,
            )

        return [ask_tool, send_tool]

    def _start_tool_call(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._tool_call_seq += 1
        entry = {
            "call_id": self._tool_call_seq,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tool_name": str(tool_name),
            "params": params,
            "status": "started",
        }
        self._current_tool_trace.append(entry)
        return entry

    def _finish_tool_call(self, entry: Dict[str, Any], *, result: Any = None, error: Optional[BaseException] = None) -> None:
        if error is None:
            entry["status"] = "completed"
            entry["result"] = result
        else:
            entry["status"] = "failed"
            entry["error"] = str(error)

    def _begin_round(self) -> None:
        self._current_tool_trace = []

    def _end_round(self) -> List[Dict[str, Any]]:
        self._last_tool_trace = list(self._current_tool_trace)
        self._current_tool_trace = []
        return list(self._last_tool_trace)

    @staticmethod
    def _normalize_mail_scope(mail_scope: str) -> str:
        scope = str(mail_scope or "unread").strip().lower()
        if scope not in {"unread", "all"}:
            raise ValueError("mail_scope must be 'unread' or 'all'.")
        return scope

    @staticmethod
    def _dedupe_keep_order(items: Sequence[str]) -> List[str]:
        seen: set[str] = set()
        result: List[str] = []
        for item in items:
            value = str(item or "").strip()
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    def _extract_core_terms(self, instruction: str) -> List[str]:
        safe = str(instruction or "").strip()
        if not safe:
            return []
        quoted_terms = [x.strip() for x in _QUOTED_TERM_PATTERN.findall(safe) if x.strip()]
        lowered = safe.lower()
        hinted_terms = [term for term in _QUERY_HINT_TERMS if term in lowered]
        cleaned = safe
        for phrase in _QUERY_NOISE_PHRASES:
            cleaned = cleaned.replace(phrase, " ")
        fragments = [x.strip() for x in _TOKEN_SPLIT_PATTERN.split(cleaned) if x and x.strip()]
        fragment_terms = [x for x in fragments if len(x) >= 2]
        merged = quoted_terms + hinted_terms + fragment_terms
        filtered = [x for x in merged if x not in _QUERY_NOISE_PHRASES]
        return self._dedupe_keep_order(filtered)

    def _plan_search_queries(self, instruction: str) -> List[str]:
        safe = str(instruction or "").strip()
        if not safe:
            return []
        terms = self._extract_core_terms(safe)
        if not terms:
            return [safe]
        top_terms = terms[:4]
        planned: List[str] = []
        if len(top_terms) >= 2:
            planned.append(" OR ".join(top_terms))
            planned.append(" ".join(top_terms))
        planned.extend(top_terms)
        return self._dedupe_keep_order(planned)

    @staticmethod
    def _apply_scope_to_query(base_query: str, mail_scope: str) -> str:
        query = str(base_query or "").strip()
        if mail_scope == "all":
            return query
        if not query:
            return "is:unread"
        return f"is:unread ({query})"

    def _search_candidates(
        self,
        *,
        kind: str,
        query_candidates: Sequence[str],
        max_results: int,
        mail_scope: str,
    ) -> tuple[List[Dict[str, Any]], str]:
        safe_candidates = list(query_candidates) if query_candidates else [""]
        last_query = ""
        for candidate in safe_candidates:
            scoped_query = self._apply_scope_to_query(candidate, mail_scope)
            last_query = scoped_query
            if kind == "threads":
                result = self._tool_search_threads(query=scoped_query, max_results=max_results)
                refs = result.get("threads") if isinstance(result, dict) else None
            else:
                result = self._tool_search_messages(query=scoped_query, max_results=max_results)
                refs = result.get("messages") if isinstance(result, dict) else None
            refs = refs if isinstance(refs, list) else []
            if refs:
                return refs, scoped_query
        return [], last_query

    def ask(self, instruction: str, mail_scope: str = "unread", debug: bool = False) -> Dict[str, Any]:
        safe_instruction = str(instruction or "").strip()
        if not safe_instruction:
            raise ValueError("instruction must be a non-empty string")
        scope = self._normalize_mail_scope(mail_scope)

        self._begin_round()
        try:
            query_candidates = self._plan_search_queries(safe_instruction)
            thread_refs, thread_query = self._search_candidates(
                kind="threads",
                query_candidates=query_candidates,
                max_results=self.query_defaults["recall_thread_topk"],
                mail_scope=scope,
            )
            message_refs: List[Dict[str, Any]] = []
            message_query = ""
            if not thread_refs:
                message_refs, message_query = self._search_candidates(
                    kind="messages",
                    query_candidates=query_candidates,
                    max_results=self.query_defaults["recall_message_topk"],
                    mail_scope=scope,
                )

            thread_ids: List[str] = []
            for item in thread_refs:
                tid = str(item.get("id", "") or "").strip()
                if tid and tid not in thread_ids:
                    thread_ids.append(tid)
                if len(thread_ids) >= self.query_defaults["recall_expand_threads"]:
                    break
            if not thread_ids:
                for item in message_refs:
                    tid = str(item.get("threadId", "") or "").strip()
                    if tid and tid not in thread_ids:
                        thread_ids.append(tid)
                    if len(thread_ids) >= self.query_defaults["recall_expand_threads"]:
                        break

            threads = [
                self._tool_get_thread(thread_id=tid, message_limit=self.query_defaults["recall_per_thread_message_limit"])
                for tid in thread_ids
            ]
            facts = self._tool_extract_facts(threads=threads)
            metrics = self._tool_aggregate_facts(facts=facts)
            summary = self._build_recall_answer(instruction=safe_instruction, facts=facts, metrics=metrics)
            evidence_index = self._build_evidence_index(facts=facts, limit=20)
            evidence_summary = self._build_evidence_summary(metrics=metrics, evidence_index=evidence_index)
            insufficient = int(metrics.get("total_messages", 0) or 0) == 0

            result = {
                "answer": summary,
                "insufficient": insufficient,
                "search_query": thread_query if thread_refs else (message_query or thread_query),
                "evidence_summary": evidence_summary,
                "evidence_index": evidence_index,
            }
            trace = self._end_round()
            if bool(debug):
                result["trace"] = trace
            return result
        except Exception:
            self._end_round()
            raise

    def send(
        self,
        *,
        content: str,
        to: Any,
        subject: str = "",
        cc: Any = None,
        bcc: Any = None,
        body_html: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        safe_content = str(content or "").strip()
        if not safe_content and not str(body_html or "").strip():
            raise ValueError("content or body_html must be provided.")

        self._begin_round()
        try:
            outbound = self._tool_draft_outbound(
                payload={
                    "to": to,
                    "cc": cc,
                    "bcc": bcc,
                    "subject": subject,
                    "body_text": safe_content,
                    "body_html": body_html,
                    "reply_to": reply_to,
                }
            )
            policy = self._tool_policy_check(outbound=outbound)
            if not bool(policy.get("ok", False)):
                raise ValueError(f"Send blocked by policy: {policy.get('blocked_reasons', [])}")

            risk_flags = list(policy.get("risk_flags", []))
            if bool(self.execution_config.get("block_on_risk_flags", False)) and risk_flags:
                raise ValueError(f"Send blocked by risk flags: {risk_flags}")

            sent = self._tool_send_direct(outbound=outbound)
            return {
                "success": True,
                "type": "send",
                "status": "sent",
                "to": list(outbound.get("to", [])),
                "subject": str(outbound.get("subject", "") or ""),
                "risk_flags": risk_flags,
                "result": sent,
                "tool_trace": self._end_round(),
            }
        except Exception:
            self._end_round()
            raise

    def _tool_search_threads(self, *, query: str, max_results: int) -> Dict[str, Any]:
        entry = self._start_tool_call("email_search_threads", {"query": query, "max_results": max_results})
        try:
            result = self.gmail_client.search_threads(query=query, max_results=max_results)
            self._finish_tool_call(entry, result={"result_count": len(result.get("threads", []) or [])})
            return result
        except Exception as exc:
            self._finish_tool_call(entry, error=exc)
            raise

    def _tool_search_messages(self, *, query: str, max_results: int) -> Dict[str, Any]:
        entry = self._start_tool_call("email_search_messages", {"query": query, "max_results": max_results})
        try:
            result = self.gmail_client.search_messages(query=query, max_results=max_results)
            self._finish_tool_call(entry, result={"result_count": len(result.get("messages", []) or [])})
            return result
        except Exception as exc:
            self._finish_tool_call(entry, error=exc)
            raise

    def _tool_get_thread(self, *, thread_id: str, message_limit: int) -> Dict[str, Any]:
        entry = self._start_tool_call("email_get_thread", {"thread_id": thread_id, "message_limit": message_limit})
        try:
            raw = self.gmail_client.get_thread(thread_id=thread_id, fmt="metadata", metadata_headers=_HEADER_KEYS)
            normalized = self._normalize_thread(raw, message_limit=message_limit)
            self._finish_tool_call(entry, result={"thread_id": normalized.get("thread_id"), "message_count": len(normalized.get("messages", []))})
            return normalized
        except Exception as exc:
            self._finish_tool_call(entry, error=exc)
            raise

    def _tool_extract_facts(self, *, threads: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        thread_facts: List[Dict[str, Any]] = []
        message_facts: List[Dict[str, Any]] = []
        for thread in threads:
            messages = thread.get("messages", []) if isinstance(thread.get("messages"), list) else []
            latest = messages[-1] if messages else {}
            thread_facts.append(
                {
                    "thread_id": thread.get("thread_id"),
                    "subject": str(latest.get("subject", "") or "").strip(),
                    "latest_from": str(latest.get("from", "") or "").strip(),
                    "latest_date": str(latest.get("date", "") or "").strip(),
                    "message_count": len(messages),
                }
            )
            for msg in messages:
                message_facts.append(
                    {
                        "thread_id": thread.get("thread_id"),
                        "message_id": msg.get("message_id"),
                        "subject": msg.get("subject"),
                        "from": msg.get("from"),
                        "date": msg.get("date"),
                        "snippet": msg.get("snippet"),
                    }
                )
        return {"threads": thread_facts, "messages": message_facts}

    def _tool_aggregate_facts(self, *, facts: Dict[str, Any]) -> Dict[str, Any]:
        threads = facts.get("threads", []) if isinstance(facts.get("threads"), list) else []
        messages = facts.get("messages", []) if isinstance(facts.get("messages"), list) else []
        sender_counter: Dict[str, int] = {}
        domain_counter: Dict[str, int] = {}
        for item in messages:
            sender = self._extract_email_address(str(item.get("from", "") or ""))
            if sender:
                sender_counter[sender] = sender_counter.get(sender, 0) + 1
                domain = sender.split("@", 1)[1].lower() if "@" in sender else ""
                if domain:
                    domain_counter[domain] = domain_counter.get(domain, 0) + 1
        return {
            "total_threads": len(threads),
            "total_messages": len(messages),
            "top_senders": self._top_items(sender_counter, topk=5),
            "top_domains": self._top_items(domain_counter, topk=5),
        }

    def _tool_draft_outbound(self, *, payload: Dict[str, Any]) -> Dict[str, Any]:
        to = self._normalize_recipients(payload.get("to"))
        cc = self._normalize_recipients(payload.get("cc"))
        bcc = self._normalize_recipients(payload.get("bcc"))
        subject = str(payload.get("subject", "") or "").strip()
        body_text = str(payload.get("body_text", "") or "").strip()
        body_html = str(payload.get("body_html", "") or "").strip() or None
        reply_to = str(payload.get("reply_to", "") or "").strip() or None
        if not to:
            raise ValueError("`to` must contain at least one recipient.")
        if not body_text and not body_html:
            raise ValueError("At least one of `content` or `body_html` must be provided.")
        return {
            "to": to,
            "cc": cc,
            "bcc": bcc,
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "reply_to": reply_to,
            "attachments": [],
        }

    def _tool_policy_check(self, *, outbound: Dict[str, Any]) -> Dict[str, Any]:
        recipients = self._flatten_recipients(outbound)
        domains = sorted({self._extract_domain(x) for x in recipients if self._extract_domain(x)})
        allowed_domains = set(self.execution_config.get("allowed_recipient_domains", []))
        allow_external = bool(self.execution_config.get("allow_external_recipient", True))

        blocked: List[str] = []
        risks: List[str] = []
        if any(not _EMAIL_PATTERN.match(x) for x in recipients):
            blocked.append("invalid_recipient_address")
        if allowed_domains:
            if any(d not in allowed_domains for d in domains):
                blocked.append("recipient_domain_not_allowed")
                risks.append("external_domain_detected")
        elif not allow_external and domains:
            blocked.append("external_recipients_disabled")

        body_text = str(outbound.get("body_text", "") or "")
        if len(body_text) > 12000:
            risks.append("very_long_body")
        if _SENSITIVE_PATTERN.search(body_text):
            risks.append("possible_sensitive_data")
        return {
            "ok": not blocked,
            "blocked_reasons": blocked,
            "risk_flags": sorted(set(risks)),
        }

    def _tool_send_direct(self, *, outbound: Dict[str, Any]) -> Dict[str, Any]:
        entry = self._start_tool_call("email_send_direct", {"to_count": len(outbound.get("to", [])), "subject": outbound.get("subject", "")})
        try:
            raw = GmailApiClient.build_raw_message(
                to=outbound.get("to", []),
                cc=outbound.get("cc", []),
                bcc=outbound.get("bcc", []),
                subject=str(outbound.get("subject", "") or ""),
                body_text=str(outbound.get("body_text", "") or ""),
                body_html=outbound.get("body_html"),
                reply_to=outbound.get("reply_to"),
                attachments=outbound.get("attachments"),
            )
            api_result = self.gmail_client.send_raw_message(raw_message=raw)
            result = {
                "gmail_message_id": str(api_result.get("id", "") or ""),
                "thread_id": str(api_result.get("threadId", "") or ""),
                "label_ids": api_result.get("labelIds", []),
                "sent_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            self._finish_tool_call(entry, result=result)
            return result
        except Exception as exc:
            self._finish_tool_call(entry, error=exc)
            raise

    def _normalize_thread(self, raw_thread: Dict[str, Any], *, message_limit: int) -> Dict[str, Any]:
        thread_id = str(raw_thread.get("id", "") or "").strip()
        raw_messages = raw_thread.get("messages")
        raw_messages = raw_messages if isinstance(raw_messages, list) else []
        messages = [self._normalize_message(item) for item in raw_messages[: max(1, int(message_limit))]]
        return {"thread_id": thread_id, "messages": messages}

    def _normalize_message(self, raw_message: Dict[str, Any]) -> Dict[str, Any]:
        payload = raw_message.get("payload") if isinstance(raw_message.get("payload"), dict) else {}
        headers = payload.get("headers") if isinstance(payload.get("headers"), list) else []
        header_map: Dict[str, str] = {}
        for item in headers:
            if isinstance(item, dict):
                name = str(item.get("name", "") or "").strip()
                if name:
                    header_map[name] = str(item.get("value", "") or "").strip()
        return {
            "message_id": str(raw_message.get("id", "") or "").strip(),
            "thread_id": str(raw_message.get("threadId", "") or "").strip(),
            "subject": header_map.get("Subject", ""),
            "from": header_map.get("From", ""),
            "date": header_map.get("Date", ""),
            "snippet": str(raw_message.get("snippet", "") or "").strip(),
        }

    @staticmethod
    def _normalize_recipients(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = re.split(r"[;,]", value)
        elif isinstance(value, list):
            raw_items = [str(x) for x in value]
        else:
            raise ValueError("Recipients must be a string or a list of strings.")
        recipients: List[str] = []
        seen: set[str] = set()
        for raw in raw_items:
            text = str(raw).strip()
            if not text:
                continue
            _, parsed = parseaddr(text)
            address = (parsed.strip() or text).strip()
            lowered = address.lower()
            if lowered not in seen:
                seen.add(lowered)
                recipients.append(address)
        return recipients

    @staticmethod
    def _flatten_recipients(outbound: Dict[str, Any]) -> List[str]:
        values: List[str] = []
        for key in ("to", "cc", "bcc"):
            seq = outbound.get(key)
            if isinstance(seq, list):
                for item in seq:
                    value = str(item or "").strip()
                    if value:
                        values.append(value)
        return values

    @staticmethod
    def _extract_email_address(raw: str) -> str:
        _, parsed = parseaddr(str(raw or "").strip())
        candidate = parsed.strip() or str(raw or "").strip()
        return candidate if _EMAIL_PATTERN.match(candidate) else ""

    @staticmethod
    def _extract_domain(address: str) -> str:
        safe = str(address or "").strip().lower()
        return safe.split("@", 1)[1] if "@" in safe else ""

    @staticmethod
    def _top_items(counter: Dict[str, int], *, topk: int) -> List[Dict[str, Any]]:
        items = list(counter.items())
        items.sort(key=lambda x: (-x[1], x[0]))
        return [{"key": key, "count": value} for key, value in items[: max(1, int(topk))]]

    @staticmethod
    def _build_evidence_index(*, facts: Dict[str, Any], limit: int = 20) -> List[Dict[str, Any]]:
        messages = facts.get("messages") if isinstance(facts.get("messages"), list) else []
        result: List[Dict[str, Any]] = []
        for idx, item in enumerate(messages[: max(1, int(limit))], start=1):
            result.append(
                {
                    "idx": f"M{idx}",
                    "subject": str(item.get("subject", "") or "").strip() or "(no subject)",
                    "from": str(item.get("from", "") or "").strip(),
                    "date": str(item.get("date", "") or "").strip(),
                    "snippet": str(item.get("snippet", "") or "").strip(),
                    "thread_id": str(item.get("thread_id", "") or "").strip(),
                    "message_id": str(item.get("message_id", "") or "").strip(),
                }
            )
        return result

    @staticmethod
    def _build_evidence_summary(*, metrics: Dict[str, Any], evidence_index: List[Dict[str, Any]]) -> str:
        total_threads = int(metrics.get("total_threads", 0) or 0)
        total_messages = int(metrics.get("total_messages", 0) or 0)
        if total_messages <= 0:
            return "No evidence emails found."

        top_senders = metrics.get("top_senders") if isinstance(metrics.get("top_senders"), list) else []
        sender_text = ", ".join(str(x.get("key", "") or "") for x in top_senders[:3] if str(x.get("key", "") or "").strip())
        top_subjects = [str(x.get("subject", "") or "").strip() for x in evidence_index[:3]]
        subject_text = "; ".join(x for x in top_subjects if x)

        parts = [f"{total_threads} thread(s), {total_messages} message(s)"]
        if sender_text:
            parts.append(f"top senders: {sender_text}")
        if subject_text:
            parts.append(f"sample subjects: {subject_text}")
        return " | ".join(parts)

    @staticmethod
    def _build_recall_answer(*, instruction: str, facts: Dict[str, Any], metrics: Dict[str, Any]) -> str:
        threads = facts.get("threads") if isinstance(facts.get("threads"), list) else []
        total_threads = int(metrics.get("total_threads", 0) or 0)
        total_messages = int(metrics.get("total_messages", 0) or 0)
        if total_messages <= 0:
            return f'未找到与"{instruction}"相关的邮件。'

        parts: List[str] = []
        for item in threads[:3]:
            subject = str(item.get("subject", "") or "").strip() or "(无主题)"
            sender = str(item.get("latest_from", "") or "").strip() or "未知发件人"
            parts.append(f"{subject} | {sender}")

        if parts:
            return f"找到 {total_threads} 个会话、{total_messages} 封相关邮件。重点：{'；'.join(parts)}"
        return f"找到 {total_threads} 个会话、{total_messages} 封相关邮件。"


def create_email_agent(config_path: str | Path = DEFAULT_CONFIG_PATH) -> EmailAgent:
    return EmailAgent(config_path=config_path)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    config_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 and sys.argv[1].endswith(".yaml") else DEFAULT_CONFIG_PATH
    instruction = " ".join(sys.argv[2:] if len(sys.argv) > 1 and sys.argv[1].endswith(".yaml") else sys.argv[1:]).strip()
    agent = EmailAgent(config_path=config_path)
    if not instruction:
        instruction = "\u5e2e\u6211\u770b\u770b\u6709\u6ca1\u6709\u5b9e\u4e60\u62db\u8058\u76f8\u5173\u90ae\u4ef6"
    print(json.dumps(agent.ask(instruction=instruction, mail_scope="unread"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

