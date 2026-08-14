from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict

import yaml
import pytest

from m_agent.agents.email_agent import EmailAgent
from m_agent.integrations.gmail_client import (
    GmailApiClient,
    GmailAuthError,
    GmailClientConfig,
)


_CN_QUERY = "\u5e2e\u6211\u770b\u770b\u6709\u6ca1\u6709\u5b9e\u4e60\u62db\u8058\u76f8\u5173\u90ae\u4ef6"
_CN_TERM_1 = "\u5b9e\u4e60"
_CN_TERM_2 = "\u62db\u8058"
_CN_SEND_BODY = "\u4f60\u597d\uff0c\u6211\u4e0b\u5468\u53ef\u4ee5\u9762\u8bd5\u3002"
_CN_SEND_SUBJECT = "\u9762\u8bd5\u65f6\u95f4\u786e\u8ba4"


def test_default_secret_alias_uses_writable_secrets_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "agents"
        / "email"
        / "gmail_email_agent.yaml"
    )
    secrets_root = tmp_path / "runtime-secrets"
    monkeypatch.setenv("M_AGENT_SECRETS_DIR", str(secrets_root))

    agent = EmailAgent(config_path=config_path)

    assert agent.gmail_client.config.credentials_path == (
        secrets_root / "gmail" / "client_secret.json"
    ).resolve()
    assert agent.gmail_client.config.token_path == (
        secrets_root / "gmail" / "token-readonly.json"
    ).resolve()
    assert agent.gmail_client.config.request_timeout_seconds == 10.0
    assert agent.gmail_client.config.oauth_flow_timeout_seconds == 300
    assert agent.gmail_client.config.allow_console_flow is False


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
        payload_text = base64.urlsafe_b64encode(
            b"Internship details: please complete the online assessment this week."
        ).decode("ascii")
        return {
            "id": thread_id,
            "messages": [
                {
                    "id": f"{thread_id}-m1",
                    "threadId": thread_id,
                    "snippet": "Internship details and next steps.",
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Intern Recruitment"},
                            {"name": "From", "value": "HR <hr@example.com>"},
                            {"name": "To", "value": "user@example.com"},
                            {"name": "Date", "value": "Mon, 01 Apr 2024 10:00:00 +0000"},
                        ],
                        "mimeType": "text/plain",
                        "body": {"data": payload_text},
                    },
                }
            ],
        }

    def get_message(self, *, message_id: str, **_: Any) -> Dict[str, Any]:
        payload_text = base64.urlsafe_b64encode(
            b"Full message body for internship update."
        ).decode("ascii")
        return {
            "id": message_id,
            "threadId": "th-1",
            "snippet": "Internship update",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Internship Update"},
                    {"name": "From", "value": "HR <hr@example.com>"},
                    {"name": "To", "value": "user@example.com"},
                    {"name": "Date", "value": "Tue, 02 Apr 2024 11:00:00 +0000"},
                ],
                "mimeType": "text/plain",
                "body": {"data": payload_text},
            },
        }

    def send_raw_message(self, *, raw_message: str) -> Dict[str, Any]:
        self.sent_raw_messages.append(raw_message)
        return {"id": "gmail-msg-1", "threadId": "th-send", "labelIds": ["SENT"]}


class _FakeAuthResp:
    status = 401


class _FakeAuthFailure(RuntimeError):
    resp = _FakeAuthResp()


class _FakeRequest:
    def __init__(self, *, result: Dict[str, Any] | None = None, exc: BaseException | None = None) -> None:
        self.result = result or {}
        self.exc = exc

    def execute(self) -> Dict[str, Any]:
        if self.exc is not None:
            raise self.exc
        return self.result


class _RetryingGmailClient(GmailApiClient):
    def __init__(self, services: list[object]) -> None:
        super().__init__(config=GmailClientConfig())
        self.services = list(services)
        self.force_reauth_flags: list[bool] = []

    def _build_service(self, *, force_reauth: bool = False) -> object:
        self.force_reauth_flags.append(force_reauth)
        return self.services.pop(0)


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
            "max_ask_calls_per_turn": 5,
            "read_max_chars": 6000,
            "read_thread_message_limit": 6,
        },
    }
    config_path = tmp_path / "email_agent.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False, allow_unicode=False)
    return config_path


def test_tools_expose_ask_read_and_send(tmp_path: Path) -> None:
    config_path = _write_email_config(tmp_path)
    fake_client = _FakeGmailClient()
    agent = EmailAgent(config_path=config_path, gmail_client=fake_client)

    tool_names = [tool.name for tool in agent.tools]
    assert tool_names == ["ask", "read", "send"]

    result = agent.ask(_CN_QUERY, mail_scope="unread")
    assert isinstance(result["answer"], str) and result["answer"]
    assert isinstance(result["evidence_summary"], str)
    assert isinstance(result["evidence_index"], list)
    assert "trace" not in result
    assert "search_query" in result
    assert "insufficient" in result


def test_read_message_returns_full_body(tmp_path: Path) -> None:
    config_path = _write_email_config(tmp_path)
    fake_client = _FakeGmailClient()
    agent = EmailAgent(config_path=config_path, gmail_client=fake_client)

    long_mid = "abcdef123456789012345678"
    result = agent.read(message_id=long_mid)

    assert result["insufficient"] is False
    assert result["message_count"] == 1
    assert result["messages"]
    first = result["messages"][0]
    assert first["message_id"] == long_mid
    assert "Full message body for internship update." in first["body_text"]


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


def test_ask_empty_keywords_uses_unread_wide_query(tmp_path: Path) -> None:
    config_path = _write_email_config(tmp_path)
    fake_client = _FakeGmailClient()
    agent = EmailAgent(config_path=config_path, gmail_client=fake_client)

    result = agent.ask("", mail_scope="unread")

    assert isinstance(result["answer"], str) and result["answer"]
    assert fake_client.thread_queries
    assert fake_client.thread_queries[0] == "is:unread"
    assert result["search_query"] == "is:unread"


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


def test_gmail_client_reauthenticates_once_after_api_auth_failure() -> None:
    first_service = object()
    second_service = object()
    client = _RetryingGmailClient([first_service, second_service])
    seen_services: list[object] = []

    def request_factory(service: object) -> _FakeRequest:
        seen_services.append(service)
        if len(seen_services) == 1:
            return _FakeRequest(exc=_FakeAuthFailure("invalid credentials"))
        return _FakeRequest(result={"ok": True})

    result = client._execute_request(request_factory)

    assert result == {"ok": True}
    assert seen_services == [first_service, second_service]
    assert client.force_reauth_flags == [False, True]


def test_gmail_client_does_not_reauthenticate_rate_limit_403() -> None:
    class _RateLimitResponse:
        status = 403

    class _RateLimitFailure(RuntimeError):
        resp = _RateLimitResponse()

    assert GmailApiClient._is_auth_failure(
        _RateLimitFailure("rateLimitExceeded")
    ) is False
    assert GmailApiClient._is_auth_failure(
        _RateLimitFailure("insufficientPermissions")
    ) is True


def test_gmail_oauth_local_flow_is_bounded_and_missing_console_is_actionable(
    tmp_path: Path,
) -> None:
    credentials_path = tmp_path / "client-secret.json"
    credentials_path.write_text("{}", encoding="utf-8")
    calls: Dict[str, Any] = {}

    class _Flow:
        @classmethod
        def from_client_secrets_file(cls, path: str, scopes: list[str]):
            calls["setup"] = (path, scopes)
            return cls()

        def run_local_server(self, **kwargs: Any) -> None:
            calls["local"] = kwargs
            raise RuntimeError("browser unavailable")

    client = GmailApiClient(
        config=GmailClientConfig(
            credentials_path=credentials_path,
            allow_local_webserver_flow=True,
            allow_console_flow=True,
            oauth_flow_timeout_seconds=42,
        )
    )

    with pytest.raises(GmailAuthError, match="Console OAuth is unavailable"):
        client._run_oauth_flow(_Flow)

    assert calls["local"]["timeout_seconds"] == 42


def test_gmail_client_builds_authorized_transport_with_hard_timeout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    calls: Dict[str, Any] = {}

    class _ValidCredentials:
        expired = True
        refresh_token = "refresh-token"
        valid = True

        @classmethod
        def from_authorized_user_file(cls, path: str, scopes: list[str]):
            calls["token_load"] = (path, scopes)
            return cls()

        def to_json(self) -> str:
            return "{}"

        def refresh(self, request: Any) -> None:
            calls["refresh_request"] = request

    class _Http:
        def __init__(self, *, timeout: float) -> None:
            calls["timeout"] = timeout

    class _AuthorizedHttp:
        def __init__(self, credentials: Any, *, http: Any) -> None:
            calls["authorized_credentials"] = credentials
            calls["transport"] = http

    class _Request:
        def __init__(self, http: Any) -> None:
            self.http = http
            calls["refresh_transport"] = http

    service = object()

    def _build(api: str, version: str, **kwargs: Any) -> object:
        calls["build"] = (api, version, kwargs)
        return service

    deps = {
        "Request": _Request,
        "Credentials": _ValidCredentials,
        "AuthorizedHttp": _AuthorizedHttp,
        "InstalledAppFlow": object,
        "Http": _Http,
        "build": _build,
    }
    client = GmailApiClient(
        config=GmailClientConfig(
            token_path=token_path,
            request_timeout_seconds=7.5,
        )
    )
    monkeypatch.setattr(client, "_import_google_deps", lambda: deps)

    assert client._build_service() is service
    assert calls["timeout"] == 7.5
    assert calls["refresh_request"].http is calls["transport"]
    assert calls["refresh_transport"] is calls["transport"]
    assert calls["build"][0:2] == ("gmail", "v1")
    assert calls["build"][2]["cache_discovery"] is False
    assert "http" in calls["build"][2]
    assert "credentials" not in calls["build"][2]
