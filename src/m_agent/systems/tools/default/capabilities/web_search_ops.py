"""Web search capability — delegates to You.com / Tavily via ``WebSearchClient``."""
from __future__ import annotations

from typing import Annotated, Any, Dict, Literal, Optional

from langchain.tools import tool
from pydantic import Field

from m_agent.integrations.web_search_client import (
    WebSearchApiError,
    WebSearchAuthError,
    WebSearchClient,
    WebSearchDependencyError,
    format_web_search_setup_hint,
)

from ...base import ControllerCapabilityContext, ControllerCapabilitySpec

_DEFAULT_CLIENT: WebSearchClient | None = None
_WEB_SEARCH_PROVIDERS = ("auto", "youcom", "tavily")


def _get_default_client() -> WebSearchClient:
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = WebSearchClient()
    return _DEFAULT_CLIENT


def _auth_required_result(query: str, url: str, exc: BaseException) -> Dict[str, Any]:
    message = str(exc).strip() or "Web search authentication is required."
    provider_status = getattr(exc, "provider_status", None)
    if not isinstance(provider_status, dict) or not provider_status:
        provider_status = _get_default_client().provider_status()
    hint = format_web_search_setup_hint(provider_status)
    return {
        "auth_required": True,
        "action_required": "web_search_api_key",
        "provider": provider_status.get("selected_provider"),
        "provider_status": provider_status,
        "error": message,
        "query": str(query or "").strip(),
        "url": str(url or "").strip(),
        "answer": hint,
        "results": [],
        "result_count": 0,
        "insufficient": True,
    }


def _api_error_result(
    query: str,
    url: str,
    exc: BaseException,
    *,
    provider: Optional[str] = None,
) -> Dict[str, Any]:
    message = str(exc).strip() or "Web search failed."
    payload: Dict[str, Any] = {
        "success": False,
        "query": str(query or "").strip(),
        "url": str(url or "").strip(),
        "answer": message,
        "results": [],
        "result_count": 0,
        "insufficient": True,
        "error": message,
        "provider_status": _get_default_client().provider_status(),
    }
    if provider:
        payload["provider"] = provider
    return payload


def _resolve_tool_provider(
    context: ControllerCapabilityContext,
    explicit_provider: Optional[str],
) -> Optional[str]:
    configured = str(context.tool_default("web_search", "provider", "auto") or "auto").strip().lower()
    chosen = str(explicit_provider or configured or "auto").strip().lower()
    if chosen == "auto":
        return None
    if chosen in {"youcom", "tavily"}:
        return chosen
    return None


def _build_web_search_tool(context: ControllerCapabilityContext, description: str):
    @tool("web_search", description=description)
    def web_search(
        query: Annotated[
            Optional[str],
            Field(
                description=(
                    "Short web search query, title, or question for up-to-date public information. "
                    "Use concise keywords. Optional when url is provided."
                ),
            ),
        ] = None,
        url: Annotated[
            Optional[str],
            Field(
                description=(
                    "Specific webpage URL to inspect and extract detailed page content from. "
                    "Use with query when you need query-relevant details from that page."
                ),
            ),
        ] = None,
        max_results: Annotated[
            Optional[int],
            Field(
                description=(
                    "Maximum number of search hits to return. "
                    "Omit to use the configured default."
                ),
            ),
        ] = None,
        provider: Annotated[
            Optional[Literal["auto", "youcom", "tavily"]],
            Field(
                description=(
                    "Search backend: auto (pick first configured provider), "
                    "youcom (YDC_API_KEY), or tavily (TAVILY_API_KEY). "
                    "Omit to use configured default (usually auto)."
                ),
            ),
        ] = None,
        include_content: Annotated[
            Optional[bool],
            Field(
                description=(
                    "When true for query-only search, request page content for search results "
                    "if the provider supports it. URL extraction always returns content when available."
                ),
            ),
        ] = None,
    ) -> Dict[str, Any]:
        """Search the public web or inspect a specific webpage URL."""

        safe_query = str(query or "").strip()
        safe_url = str(url or "").strip()
        selected_provider = _resolve_tool_provider(context, provider)
        params: Dict[str, Any] = {"query": safe_query, "url": safe_url}
        if max_results is not None:
            params["max_results"] = int(max_results)
        if provider is not None:
            params["provider"] = provider
        if include_content is not None:
            params["include_content"] = bool(include_content)

        call_id = context.start_tool_call("web_search", params)
        limit_result = context.check_tool_call_limits("web_search")
        if limit_result is not None:
            context.finish_tool_call(call_id, "web_search", result=limit_result)
            return limit_result

        if not safe_query and not safe_url:
            result = {
                "query": "",
                "url": "",
                "mode": "clarify",
                "answer": "",
                "results": [],
                "result_count": 0,
                "insufficient": True,
                "needs_clarification": True,
                "message": "Search query or URL is required.",
                "provider_status": _get_default_client().provider_status(),
            }
            context.record_tool_use("web_search", params, result)
            context.finish_tool_call(call_id, "web_search", result=result)
            return result

        effective_max_results = max_results
        if effective_max_results is None:
            default_max = context.tool_default("web_search", "max_results")
            if default_max is not None:
                try:
                    effective_max_results = int(default_max)
                except (TypeError, ValueError):
                    effective_max_results = None

        client = _get_default_client()
        try:
            result = client.search(
                safe_query or None,
                url=safe_url or None,
                max_results=effective_max_results,
                provider=selected_provider,
                include_content=include_content,
            )
        except (WebSearchAuthError, WebSearchDependencyError) as exc:
            result = _auth_required_result(safe_query, safe_url, exc)
            context.record_tool_use("web_search", params, result)
            context.finish_tool_call(call_id, "web_search", result=result)
            return result
        except WebSearchApiError as exc:
            result = _api_error_result(safe_query, safe_url, exc, provider=selected_provider or None)
            context.record_tool_use("web_search", params, result)
            context.finish_tool_call(call_id, "web_search", result=result)
            return result
        except Exception as exc:
            context.finish_tool_call(call_id, "web_search", error=str(exc))
            raise

        context.record_tool_use("web_search", params, result)
        context.finish_tool_call(call_id, "web_search", result=result)
        return result

    return web_search


WEB_SEARCH_CAPABILITY = ControllerCapabilitySpec(
    name="web_search",
    build_tool=_build_web_search_tool,
    side_effect="read",
    delivery_guarantee="at_most_once",
)


__all__ = ["WEB_SEARCH_CAPABILITY"]
