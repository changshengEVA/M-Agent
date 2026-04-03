from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from m_agent.agents.email_agent import EmailAgent


_CN_QUERY = "\u5e2e\u6211\u770b\u770b\u6709\u6ca1\u6709\u5b9e\u4e60\u62db\u8058\u76f8\u5173\u90ae\u4ef6"
_CN_TERM_1 = "\u5b9e\u4e60"
_CN_TERM_2 = "\u62db\u8058"
_CN_SEND_BODY = "\u4f60\u597d\uff0c\u6211\u4e0b\u5468\u53ef\u4ee5\u9762\u8bd5\u3002"
_CN_SEND_SUBJECT = "\u9762\u8bd5\u65f6\u95f4\u786e\u8ba4"


class _FakeGmailClient:
    def __init__(self) -> None:
        self.sent_raw_messages: list[str] = []
        self.thread_queries: list[str] = []
        self.message_queries: list[str] = []

    def search_threads(self, *, query: str, max_results: int, **_: Any) -> Dict[str, Any]:
        self.thread_queries.append(query)
        return {"threads": [{"id": "th-1"}, {"id": "th-2"}][:max_results]}

    def search_messages(self, *, query: str, max_results: int, **_: Any) -> Dict[str, Any]:
        self.message_queries.append(query)
        return {"messages": [{"id": "msg-1", "threadId": "th-1"}, {"id": "msg-2", "threadId": "th-2"}][:max_results]}

    def get_thread(self, *, thread_id: str, **_: Any) -> Dict[str, Any]:
        return {
            "id": thread_id,
            "messages": [
                {
                    "id": f"{thread_id}-m1",
                    "threadId": thread_id,
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Intern Recruitment"},
                            {"name": "From", "value": "HR <hr@example.com>"},
                            {"name": "To", "value": "user@example.com"},
                            {"name": "Date", "value": "Mon, 01 Apr 2024 10:00:00 +0000"},
                        ]
                    },
                }
            ],
        }

    def send_raw_message(self, *, raw_message: str) -> Dict[str, Any]:
        self.sent_raw_messages.append(raw_message)
        return {"id": "gmail-msg-1", "threadId": "th-send", "labelIds": ["SENT"]}


def _write_email_config(tmp_path: Path) -> Path:
    config = {
        "provider": "gmail",
        "gmail": {
            "user_id": "me",
            "credentials_path": ".secrets/gmail/client_secret.json",
            "token_path": ".secrets/gmail/token.json",
            "scopes": [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.send",
            ],
            "oauth": {"allow_local_webserver_flow": True, "allow_console_flow": False},
        },
        "query_defaults": {
            "recall_thread_topk": 10,
            "recall_message_topk": 10,
            "recall_expand_threads": 2,
            "recall_per_thread_message_limit": 2,
        },
        "execution": {
            "allow_external_recipient": True,
            "allowed_recipient_domains": [],
            "block_on_risk_flags": False,
        },
    }
    config_path = tmp_path / "email_agent.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False, allow_unicode=False)
    return config_path


def test_tools_expose_ask_and_send_only(tmp_path: Path) -> None:
    config_path = _write_email_config(tmp_path)
    fake_client = _FakeGmailClient()
    agent = EmailAgent(config_path=config_path, gmail_client=fake_client)

    tool_names = [tool.name for tool in agent.tools]
    assert tool_names == ["ask", "send"]

    result = agent.ask(_CN_QUERY, mail_scope="unread")
    assert isinstance(result["answer"], str) and result["answer"]
    assert isinstance(result["evidence_summary"], str)
    assert isinstance(result["evidence_index"], list)
    assert "trace" not in result
    assert "search_query" in result
    assert "insufficient" in result


def test_ask_debug_includes_trace(tmp_path: Path) -> None:
    config_path = _write_email_config(tmp_path)
    fake_client = _FakeGmailClient()
    agent = EmailAgent(config_path=config_path, gmail_client=fake_client)

    result = agent.ask(_CN_QUERY, mail_scope="unread", debug=True)

    assert "trace" in result
    assert isinstance(result["trace"], list)
    assert result["trace"]


def test_ask_returns_evidence_index_items(tmp_path: Path) -> None:
    config_path = _write_email_config(tmp_path)
    fake_client = _FakeGmailClient()
    agent = EmailAgent(config_path=config_path, gmail_client=fake_client)

    result = agent.ask(_CN_QUERY, mail_scope="unread")

    evidence_index = result["evidence_index"]
    assert evidence_index
    first = evidence_index[0]
    assert first["idx"] == "M1"
    assert first["thread_id"]
    assert first["message_id"]
    assert isinstance(result["answer"], str) and result["answer"]


def test_ask_rewrites_natural_language_and_applies_mail_scope(tmp_path: Path) -> None:
    config_path = _write_email_config(tmp_path)
    fake_client = _FakeGmailClient()
    agent = EmailAgent(config_path=config_path, gmail_client=fake_client)

    result = agent.ask(_CN_QUERY, mail_scope="unread")

    assert result["search_query"] != _CN_QUERY
    assert fake_client.thread_queries
    assert fake_client.thread_queries[0].startswith("is:unread")
    assert any((_CN_TERM_1 in q) or (_CN_TERM_2 in q) for q in fake_client.thread_queries)


def test_send_directly_sends_message(tmp_path: Path) -> None:
    config_path = _write_email_config(tmp_path)
    fake_client = _FakeGmailClient()
    agent = EmailAgent(config_path=config_path, gmail_client=fake_client)

    result = agent.send(content=_CN_SEND_BODY, to="hr@example.com", subject=_CN_SEND_SUBJECT)

    assert result["success"] is True
    assert result["type"] == "send"
    assert result["status"] == "sent"
    assert result["result"]["gmail_message_id"] == "gmail-msg-1"
    assert len(fake_client.sent_raw_messages) == 1


def test_send_rejects_invalid_recipient(tmp_path: Path) -> None:
    config_path = _write_email_config(tmp_path)
    fake_client = _FakeGmailClient()
    agent = EmailAgent(config_path=config_path, gmail_client=fake_client)

    try:
        agent.send(content="test", to="invalid-email")
    except ValueError as exc:
        assert "blocked by policy" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError for invalid recipient")
