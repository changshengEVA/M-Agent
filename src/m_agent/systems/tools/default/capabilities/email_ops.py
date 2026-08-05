"""Email capabilities — delegate to ``EmailAgent`` via the email provider closure."""
from __future__ import annotations

from typing import Annotated, Any, Dict, Optional

from langchain.tools import tool
from pydantic import Field

from m_agent.integrations.gmail_client import GmailAuthError, GmailDependencyError

from ...base import ControllerCapabilityContext, ControllerCapabilitySpec


def _gmail_auth_required_result(tool_name: str, exc: BaseException) -> Dict[str, Any]:
    message = str(exc).strip() or "Gmail authentication is required."
    base: Dict[str, Any] = {
        "auth_required": True,
        "action_required": "gmail_oauth",
        "provider": "gmail",
        "error": message,
        "answer": (
            "Gmail needs authentication before I can use mail tools. "
            "Please complete the OAuth sign-in prompt, then retry this email action."
        ),
        "insufficient": True,
    }
    if tool_name == "email_ask":
        base.update(
            {
                "search_query": "",
                "evidence_summary": "Gmail authentication required",
                "evidence_index": [],
            }
        )
    elif tool_name == "email_read":
        base.update(
            {
                "source": "",
                "message_count": 0,
                "messages": [],
            }
        )
    elif tool_name == "email_send":
        base.update(
            {
                "success": False,
                "type": "send",
                "status": "auth_required",
                "result": {},
            }
        )
    return base


def _controller_tool_count(context: ControllerCapabilityContext, *, tool_name: str) -> int:
    history = context.controller_state.get("history")
    if not isinstance(history, list):
        return 0
    count = 0
    for item in history:
        if not isinstance(item, dict):
            continue
        if str(item.get("tool_name", "") or "").strip() == tool_name:
            count += 1
    return count


def _build_email_ask_tool(context: ControllerCapabilityContext, description: str):
    @tool("email_ask", description=description)
    def email_ask(
        keywords: Annotated[
            str,
            Field(
                default="",
                description=(
                    "Gmail search keywords: short tokens or phrases that should appear in matching mail "
                    "(subject/body). Leave empty to list mail under mail_scope only "
                    "(e.g. all unread when mail_scope is unread). "
                    "Do not paste polite full sentences—split topics into keywords."
                ),
            ),
        ] = "",
        mail_scope: Annotated[
            Optional[str],
            Field(
                description=(
                    "Mailbox slice before keyword match: `unread` (default) or `all`. "
                    "Omit to use the configured default (usually unread)."
                ),
            ),
        ] = None,
        debug: Annotated[
            bool,
            Field(description="When true, include internal EmailAgent trace in the tool result."),
        ] = False,
    ) -> Dict[str, Any]:
        """Delegate email recall to EmailAgent.ask."""

        safe_keywords = str(keywords or "").strip()
        effective_scope = (
            str(mail_scope or context.tool_default("email_ask", "mail_scope", "unread") or "unread").strip().lower()
        )
        params = {
            "keywords": safe_keywords,
            "mail_scope": effective_scope,
            "debug": bool(debug),
        }
        call_id = context.start_tool_call("email_ask", params)
        limit_result = context.check_tool_call_limits("email_ask")
        if limit_result is not None:
            context.finish_tool_call(call_id, "email_ask", result=limit_result)
            return limit_result
        email_agent = context.get_email_agent()
        max_ask_calls = int(getattr(email_agent, "execution_config", {}).get("max_ask_calls_per_turn", 5) or 5)
        ask_count = _controller_tool_count(context, tool_name="email_ask")
        if ask_count >= max_ask_calls:
            limit_result = {
                "answer": (
                    f"email_ask 调用次数已达到上限（{max_ask_calls}）。"
                    "请基于现有结果直接回答，或请用户缩小检索范围后再试。"
                ),
                "insufficient": True,
                "limit_reached": True,
                "max_ask_calls_per_turn": max_ask_calls,
                "evidence_summary": "email_ask call limit reached",
                "evidence_index": [],
            }
            context.record_tool_use("email_ask", params, limit_result)
            context.finish_tool_call(call_id, "email_ask", result=limit_result)
            return limit_result

        try:
            result = email_agent.ask(
                keywords=safe_keywords,
                mail_scope=effective_scope,
                debug=bool(debug),
            )
        except (GmailAuthError, GmailDependencyError) as exc:
            result = _gmail_auth_required_result("email_ask", exc)
            context.record_tool_use("email_ask", params, result)
            context.finish_tool_call(call_id, "email_ask", result=result)
            return result
        except Exception as exc:
            context.finish_tool_call(call_id, "email_ask", error=str(exc))
            raise

        context.record_tool_use("email_ask", params, result)
        context.finish_tool_call(call_id, "email_ask", result=result)
        return result

    return email_ask


def _build_email_read_tool(context: ControllerCapabilityContext, description: str):
    @tool("email_read", description=description)
    def email_read(
        message_id: str = "",
        thread_id: str = "",
        include_html: bool = False,
        max_chars: Optional[int] = None,
        debug: bool = False,
    ) -> Dict[str, Any]:
        """Delegate reading a specific email to EmailAgent.read."""

        safe_message_id = str(message_id or "").strip()
        safe_thread_id = str(thread_id or "").strip()
        params = {
            "message_id": safe_message_id,
            "thread_id": safe_thread_id,
            "include_html": bool(include_html),
            "max_chars": max_chars,
            "debug": bool(debug),
        }
        call_id = context.start_tool_call("email_read", params)
        limit_result = context.check_tool_call_limits("email_read")
        if limit_result is not None:
            context.finish_tool_call(call_id, "email_read", result=limit_result)
            return limit_result
        try:
            result = context.get_email_agent().read(
                message_id=safe_message_id,
                thread_id=safe_thread_id,
                include_html=bool(include_html),
                max_chars=max_chars,
                debug=bool(debug),
            )
        except (GmailAuthError, GmailDependencyError) as exc:
            result = _gmail_auth_required_result("email_read", exc)
            context.record_tool_use("email_read", params, result)
            context.finish_tool_call(call_id, "email_read", result=result)
            return result
        except Exception as exc:
            context.finish_tool_call(call_id, "email_read", error=str(exc))
            raise

        context.record_tool_use("email_read", params, result)
        context.finish_tool_call(call_id, "email_read", result=result)
        return result

    return email_read


def _build_email_send_tool(context: ControllerCapabilityContext, description: str):
    @tool("email_send", description=description)
    def email_send(
        content: str,
        to: str,
        subject: str = "",
        cc: str = "",
        bcc: str = "",
        body_html: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delegate email send to EmailAgent.send."""

        safe_to = str(to or "").strip()
        safe_subject = str(subject or "").strip()
        safe_cc = str(cc or "").strip()
        safe_bcc = str(bcc or "").strip()
        safe_content = str(content or "")
        params = {
            "to": safe_to,
            "subject": safe_subject,
            "cc": safe_cc,
            "bcc": safe_bcc,
            "has_body_html": bool(str(body_html or "").strip()),
            "has_reply_to": bool(str(reply_to or "").strip()),
            "content_length": len(safe_content),
        }
        call_id = context.start_tool_call("email_send", params)
        limit_result = context.check_tool_call_limits("email_send")
        if limit_result is not None:
            context.finish_tool_call(call_id, "email_send", result=limit_result)
            return limit_result
        try:
            result = context.get_email_agent().send(
                content=safe_content,
                to=safe_to,
                subject=safe_subject,
                cc=safe_cc,
                bcc=safe_bcc,
                body_html=body_html,
                reply_to=reply_to,
            )
        except (GmailAuthError, GmailDependencyError) as exc:
            result = _gmail_auth_required_result("email_send", exc)
            context.record_tool_use("email_send", params, result)
            context.finish_tool_call(call_id, "email_send", result=result)
            return result
        except Exception as exc:
            context.finish_tool_call(call_id, "email_send", error=str(exc))
            raise

        context.record_tool_use("email_send", params, result)
        context.finish_tool_call(call_id, "email_send", result=result)
        return result

    return email_send


EMAIL_ASK_CAPABILITY = ControllerCapabilitySpec(
    name="email_ask",
    build_tool=_build_email_ask_tool,
    side_effect="read",
    delivery_guarantee="at_least_once",
)

EMAIL_READ_CAPABILITY = ControllerCapabilitySpec(
    name="email_read",
    build_tool=_build_email_read_tool,
    side_effect="read",
    delivery_guarantee="at_least_once",
)

EMAIL_SEND_CAPABILITY = ControllerCapabilitySpec(
    name="email_send",
    build_tool=_build_email_send_tool,
    side_effect="send",
    delivery_guarantee="at_most_once",
)


__all__ = [
    "EMAIL_ASK_CAPABILITY",
    "EMAIL_READ_CAPABILITY",
    "EMAIL_SEND_CAPABILITY",
]
