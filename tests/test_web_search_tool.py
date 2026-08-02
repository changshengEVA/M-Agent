from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest

from m_agent.integrations.web_search_client import (
    WebSearchAuthError,
    WebSearchClient,
    WebSearchClientConfig,
    TavilyClientConfig,
    YouComClientConfig,
    list_web_search_provider_status,
    resolve_web_search_provider,
)
from m_agent.runtime.turn_support.delegate import uses_param_llm
from m_agent.runtime.turn_support.tool_runner import SKIP_PARAM_LLM_TOOLS
from m_agent.systems.tools.base import ControllerCapabilityContext
from m_agent.systems.tools.default.capabilities import web_search_ops
from m_agent.systems.tools.default.capabilities.web_search_ops import _build_web_search_tool


class _FakeHttpResponse:
    def __init__(self, *, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self) -> Any:
        return self._payload


class _FakeHttpClient:
    def __init__(
        self,
        *,
        post_response: Optional[_FakeHttpResponse] = None,
        get_response: Optional[_FakeHttpResponse] = None,
    ) -> None:
        self.post_response = post_response
        self.get_response = get_response
        self.last_url: Optional[str] = None
        self.last_payload: Optional[Dict[str, Any]] = None
        self.last_params: Optional[Dict[str, Any]] = None
        self.last_headers: Optional[Dict[str, str]] = None

    def post(self, url: str, *, json: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> _FakeHttpResponse:
        self.last_url = url
        self.last_payload = json
        self.last_headers = headers
        assert self.post_response is not None
        return self.post_response

    def get(
        self,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> _FakeHttpResponse:
        self.last_url = url
        self.last_params = params
        self.last_headers = headers
        assert self.get_response is not None
        return self.get_response


def _build_context(*, tool_defaults: dict | None = None) -> ControllerCapabilityContext:
    return ControllerCapabilityContext(
        active_thread_id="thread-1",
        recall_state={"mode": None, "result": None, "history": []},
        controller_state={"history": [], "call_seq": 0},
        tool_defaults=tool_defaults or {"web_search": {"max_results": 5, "max_calls_per_turn": 3}},
        logger=logging.getLogger("test.web_search_tool"),
    )


def test_web_search_client_normalizes_tavily_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    monkeypatch.delenv("YDC_API_KEY", raising=False)
    fake_http = _FakeHttpClient(
        post_response=_FakeHttpResponse(
            status_code=200,
            payload={
                "answer": "Sample answer",
                "results": [
                    {
                        "title": "Result A",
                        "url": "https://example.com/a",
                        "content": "Snippet A",
                    }
                ],
            },
        )
    )
    client = WebSearchClient(
        config=WebSearchClientConfig(provider="tavily", tavily=TavilyClientConfig()),
        http_client=fake_http,
    )

    result = client.search("sample query")

    assert result["provider"] == "tavily"
    assert result["query"] == "sample query"
    assert result["answer"] == "Sample answer"
    assert result["result_count"] == 1
    assert result["results"][0]["snippet"] == "Snippet A"
    assert fake_http.last_url == "https://api.tavily.com/search"
    assert fake_http.last_payload is not None
    assert fake_http.last_payload["api_key"] == "tvly-test-key"


def test_web_search_client_normalizes_youcom_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YDC_API_KEY", "ydc-test-key")
    fake_http = _FakeHttpClient(
        get_response=_FakeHttpResponse(
            status_code=200,
            payload={
                "results": {
                    "web": [
                        {
                            "title": "You Result",
                            "url": "https://example.com/you",
                            "snippets": ["You snippet"],
                        }
                    ]
                }
            },
        )
    )
    client = WebSearchClient(
        config=WebSearchClientConfig(provider="youcom", youcom=YouComClientConfig()),
        http_client=fake_http,
    )

    result = client.search("sample query")

    assert result["provider"] == "youcom"
    assert result["result_count"] == 1
    assert result["results"][0]["snippet"] == "You snippet"
    assert fake_http.last_url == "https://ydc-index.io/v1/search"
    assert fake_http.last_params == {"query": "sample query", "count": 5}
    assert fake_http.last_headers == {"X-API-Key": "ydc-test-key"}


def test_web_search_client_extracts_tavily_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    fake_http = _FakeHttpClient(
        post_response=_FakeHttpResponse(
            status_code=200,
            payload={
                "results": [
                    {
                        "url": "https://example.com/page",
                        "raw_content": "Detailed page content",
                    }
                ],
                "failed_results": [],
            },
        )
    )
    client = WebSearchClient(
        config=WebSearchClientConfig(provider="tavily", tavily=TavilyClientConfig()),
        http_client=fake_http,
    )

    result = client.search(query="topic", url="https://example.com/page")

    assert result["mode"] == "extract"
    assert result["provider"] == "tavily"
    assert result["url"] == "https://example.com/page"
    assert result["content"] == "Detailed page content"
    assert result["content_chars"] == len("Detailed page content")
    assert fake_http.last_url == "https://api.tavily.com/extract"
    assert fake_http.last_payload is not None
    assert fake_http.last_payload["urls"] == "https://example.com/page"
    assert fake_http.last_payload["query"] == "topic"
    assert fake_http.last_headers == {"Authorization": "Bearer tvly-test-key"}


def test_web_search_client_extracts_youcom_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YDC_API_KEY", "ydc-test-key")
    fake_http = _FakeHttpClient(
        post_response=_FakeHttpResponse(
            status_code=200,
            payload=[
                {
                    "url": "https://example.com/page",
                    "title": "Example",
                    "markdown": "Detailed markdown content",
                    "metadata": {"site_name": "Example"},
                }
            ],
        )
    )
    client = WebSearchClient(
        config=WebSearchClientConfig(provider="youcom", youcom=YouComClientConfig()),
        http_client=fake_http,
    )

    result = client.search(url="https://example.com/page")

    assert result["mode"] == "extract"
    assert result["provider"] == "youcom"
    assert result["content"] == "Detailed markdown content"
    assert result["results"][0]["metadata"] == {"site_name": "Example"}
    assert fake_http.last_url == "https://ydc-index.io/v1/contents"
    assert fake_http.last_payload == {
        "urls": ["https://example.com/page"],
        "formats": ["markdown", "metadata"],
    }
    assert fake_http.last_headers == {
        "X-API-Key": "ydc-test-key",
        "Content-Type": "application/json",
    }


def test_auto_provider_uses_youcom_key_from_legacy_tavily_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YDC_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "ydc-legacy-key")
    status = list_web_search_provider_status(
        WebSearchClientConfig(provider="auto", provider_priority=["youcom", "tavily"])
    )
    assert status["available"] == ["youcom"]
    assert resolve_web_search_provider(WebSearchClientConfig(provider="auto")) == "youcom"


def test_auto_provider_prefers_youcom_when_both_keys_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YDC_API_KEY", "ydc-test-key")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    assert resolve_web_search_provider(WebSearchClientConfig(provider="auto")) == "youcom"


def test_web_search_client_raises_auth_error_when_no_provider_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("YDC_API_KEY", raising=False)
    client = WebSearchClient(
        config=WebSearchClientConfig(provider="auto"),
        http_client=_FakeHttpClient(post_response=_FakeHttpResponse(status_code=200, payload={})),
    )

    with pytest.raises(WebSearchAuthError):
        client.search("missing key")


def test_web_search_tool_returns_auth_required_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("YDC_API_KEY", raising=False)
    web_search_ops._DEFAULT_CLIENT = None
    context = _build_context()
    tool = _build_web_search_tool(context, "web search")

    result = tool.invoke({"query": "today weather"})

    assert result["auth_required"] is True
    assert result["action_required"] == "web_search_api_key"
    assert result["insufficient"] is True
    assert "YDC_API_KEY" in result["answer"]
    assert isinstance(result.get("provider_status"), dict)


def test_web_search_tool_respects_per_tool_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    fake_client = MagicMock()
    fake_client.search.return_value = {
        "query": "q",
        "answer": "ok",
        "results": [],
        "result_count": 0,
        "insufficient": False,
        "provider": "tavily",
    }
    web_search_ops._DEFAULT_CLIENT = fake_client
    context = _build_context(tool_defaults={"web_search": {"max_calls_per_turn": 1}})
    tool = _build_web_search_tool(context, "web search")

    first = tool.invoke({"query": "first"})
    blocked = tool.invoke({"query": "second"})

    assert first["answer"] == "ok"
    assert blocked["limit_reached"] is True
    assert blocked["limit_scope"] == "tool"
    assert fake_client.search.call_count == 1


def test_web_search_tool_can_pass_url_to_client(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = MagicMock()
    fake_client.search.return_value = {
        "mode": "extract",
        "query": "topic",
        "url": "https://example.com/page",
        "answer": "details",
        "content": "details",
        "content_chars": 7,
        "results": [],
        "result_count": 0,
        "insufficient": False,
        "provider": "tavily",
    }
    web_search_ops._DEFAULT_CLIENT = fake_client
    context = _build_context()
    tool = _build_web_search_tool(context, "web search")

    result = tool.invoke(
        {
            "query": "topic",
            "url": "https://example.com/page",
            "provider": "tavily",
            "include_content": True,
        }
    )

    assert result["mode"] == "extract"
    fake_client.search.assert_called_once_with(
        "topic",
        url="https://example.com/page",
        max_results=5,
        provider="tavily",
        include_content=True,
    )


def test_web_search_uses_runtime_param_model() -> None:
    assert "web_search" not in SKIP_PARAM_LLM_TOOLS
    assert uses_param_llm("web_search") is True
