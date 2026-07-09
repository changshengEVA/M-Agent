from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from m_agent.config_paths import WEB_SEARCH_CONFIG_PATH, resolve_config_path


logger = logging.getLogger(__name__)

_DEFAULT_TAVILY_BASE_URL = "https://api.tavily.com"
_DEFAULT_TAVILY_SEARCH_PATH = "/search"
_DEFAULT_TAVILY_EXTRACT_PATH = "/extract"
_DEFAULT_YOUCOM_BASE_URL = "https://ydc-index.io"
_DEFAULT_YOUCOM_SEARCH_PATH = "/v1/search"
_DEFAULT_YOUCOM_CONTENTS_PATH = "/v1/contents"
_SUPPORTED_PROVIDERS = ("youcom", "tavily")


class WebSearchDependencyError(RuntimeError):
    """Raised when HTTP dependencies are not installed."""


class WebSearchAuthError(RuntimeError):
    """Raised when no usable web-search API key is configured."""

    def __init__(
        self,
        message: str,
        *,
        provider_status: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.provider_status = provider_status or {}


class WebSearchApiError(RuntimeError):
    """Raised when the search provider returns an HTTP or API error."""


@dataclass
class YouComClientConfig:
    base_url: str = _DEFAULT_YOUCOM_BASE_URL
    search_path: str = _DEFAULT_YOUCOM_SEARCH_PATH
    contents_path: str = _DEFAULT_YOUCOM_CONTENTS_PATH
    max_results: int = 5
    api_key_env: str = "YDC_API_KEY"


@dataclass
class TavilyClientConfig:
    base_url: str = _DEFAULT_TAVILY_BASE_URL
    search_path: str = _DEFAULT_TAVILY_SEARCH_PATH
    extract_path: str = _DEFAULT_TAVILY_EXTRACT_PATH
    search_depth: str = "basic"
    extract_depth: str = "basic"
    content_format: str = "markdown"
    max_results: int = 5
    include_answer: bool = True
    api_key_env: str = "TAVILY_API_KEY"


@dataclass
class WebSearchClientConfig:
    provider: str = "auto"
    provider_priority: List[str] = field(default_factory=lambda: ["youcom", "tavily"])
    youcom: YouComClientConfig = field(default_factory=YouComClientConfig)
    tavily: TavilyClientConfig = field(default_factory=TavilyClientConfig)


def _normalize_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _looks_like_youcom_key(api_key: str) -> bool:
    lowered = str(api_key or "").strip().lower()
    return lowered.startswith("ydc-")


def _looks_like_tavily_key(api_key: str) -> bool:
    lowered = str(api_key or "").strip().lower()
    return lowered.startswith("tvly-")


def _env_key(env_name: str) -> str:
    return str(os.environ.get(str(env_name or "").strip(), "") or "").strip()


def _provider_key_info(provider: str, config: WebSearchClientConfig) -> Dict[str, Any]:
    name = str(provider or "").strip().lower()
    if name == "youcom":
        env_name = config.youcom.api_key_env
        api_key = _env_key(env_name)
        legacy_env = config.tavily.api_key_env
        legacy_key = _env_key(legacy_env)
        legacy_usable = bool(legacy_key) and _looks_like_youcom_key(legacy_key)
        configured = bool(api_key) or legacy_usable
        return {
            "name": "youcom",
            "label": "You.com",
            "configured": configured,
            "env_var": env_name,
            "legacy_env_var": legacy_env if legacy_usable else None,
            "key_hint": "ydc-...",
        }
    if name == "tavily":
        env_name = config.tavily.api_key_env
        api_key = _env_key(env_name)
        configured = bool(api_key) and not _looks_like_youcom_key(api_key)
        return {
            "name": "tavily",
            "label": "Tavily",
            "configured": configured,
            "env_var": env_name,
            "legacy_env_var": None,
            "key_hint": "tvly-...",
        }
    raise ValueError(f"Unsupported web search provider: {provider}")


def list_web_search_provider_status(
    config: Optional[WebSearchClientConfig] = None,
) -> Dict[str, Any]:
    """Return configured/available providers for setup hints and auto selection."""
    cfg = config or load_web_search_config()
    providers = [_provider_key_info(name, cfg) for name in _SUPPORTED_PROVIDERS]
    available = [item["name"] for item in providers if item["configured"]]
    priority = resolve_provider_priority(cfg)
    selected = None
    mode = str(cfg.provider or "auto").strip().lower() or "auto"
    if mode in _SUPPORTED_PROVIDERS:
        info = _provider_key_info(mode, cfg)
        selected = mode if info["configured"] else None
    elif mode == "auto":
        for name in priority:
            if name in available:
                selected = name
                break
    return {
        "mode": mode,
        "providers": providers,
        "available": available,
        "selected_provider": selected,
        "provider_priority": priority,
    }


def format_web_search_setup_hint(status: Dict[str, Any]) -> str:
    providers = status.get("providers")
    if not isinstance(providers, list):
        providers = []
    available = status.get("available")
    if not isinstance(available, list):
        available = []

    if available:
        labels = []
        for item in providers:
            if isinstance(item, dict) and item.get("name") in available:
                labels.append(str(item.get("label") or item.get("name") or "").strip())
        joined = ", ".join(label for label in labels if label)
        return f"Configured web search provider(s): {joined}."

    lines = [
        "No web search API key is configured. Set one of:",
        "YDC_API_KEY for You.com (key prefix ydc-...),",
        "or TAVILY_API_KEY for Tavily (key prefix tvly-...).",
    ]
    for item in providers:
        if not isinstance(item, dict):
            continue
        legacy_env = str(item.get("legacy_env_var") or "").strip()
        env_var = str(item.get("env_var") or "").strip()
        if legacy_env:
            lines.append(
                f"A You.com key was detected in {legacy_env}; move it to {env_var}."
            )
    return " ".join(lines)


def resolve_provider_priority(config: WebSearchClientConfig) -> List[str]:
    priority: List[str] = []
    for raw in config.provider_priority or []:
        name = str(raw or "").strip().lower()
        if name in _SUPPORTED_PROVIDERS and name not in priority:
            priority.append(name)
    if not priority:
        priority = list(_SUPPORTED_PROVIDERS)
    return priority


def resolve_web_search_provider(
    config: Optional[WebSearchClientConfig] = None,
) -> str:
    cfg = config or load_web_search_config()
    status = list_web_search_provider_status(cfg)
    selected = status.get("selected_provider")
    if isinstance(selected, str) and selected.strip():
        return selected.strip()
    raise WebSearchAuthError(
        format_web_search_setup_hint(status),
        provider_status=status,
    )


def load_web_search_config(config_path: str | Path | None = None) -> WebSearchClientConfig:
    path = resolve_config_path(config_path or WEB_SEARCH_CONFIG_PATH)
    if not path.exists():
        return WebSearchClientConfig()

    with open(path, "r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Web search config must be a dict: {path}")

    provider = str(payload.get("provider", "auto") or "auto").strip().lower() or "auto"
    priority_raw = payload.get("provider_priority")
    priority = (
        [str(item or "").strip().lower() for item in priority_raw]
        if isinstance(priority_raw, list)
        else ["youcom", "tavily"]
    )

    youcom_raw = payload.get("youcom")
    youcom_payload = youcom_raw if isinstance(youcom_raw, dict) else {}
    tavily_raw = payload.get("tavily")
    tavily_payload = tavily_raw if isinstance(tavily_raw, dict) else {}

    youcom = YouComClientConfig(
        base_url=str(youcom_payload.get("base_url", _DEFAULT_YOUCOM_BASE_URL) or _DEFAULT_YOUCOM_BASE_URL).strip()
        or _DEFAULT_YOUCOM_BASE_URL,
        search_path=str(
            youcom_payload.get("search_path", _DEFAULT_YOUCOM_SEARCH_PATH) or _DEFAULT_YOUCOM_SEARCH_PATH
        ).strip()
        or _DEFAULT_YOUCOM_SEARCH_PATH,
        contents_path=str(
            youcom_payload.get("contents_path", _DEFAULT_YOUCOM_CONTENTS_PATH) or _DEFAULT_YOUCOM_CONTENTS_PATH
        ).strip()
        or _DEFAULT_YOUCOM_CONTENTS_PATH,
        max_results=_normalize_positive_int(youcom_payload.get("max_results"), default=5),
        api_key_env=str(youcom_payload.get("api_key_env", "YDC_API_KEY") or "YDC_API_KEY").strip()
        or "YDC_API_KEY",
    )
    tavily = TavilyClientConfig(
        base_url=str(tavily_payload.get("base_url", _DEFAULT_TAVILY_BASE_URL) or _DEFAULT_TAVILY_BASE_URL).strip()
        or _DEFAULT_TAVILY_BASE_URL,
        search_path=str(tavily_payload.get("search_path", _DEFAULT_TAVILY_SEARCH_PATH) or _DEFAULT_TAVILY_SEARCH_PATH).strip()
        or _DEFAULT_TAVILY_SEARCH_PATH,
        extract_path=str(tavily_payload.get("extract_path", _DEFAULT_TAVILY_EXTRACT_PATH) or _DEFAULT_TAVILY_EXTRACT_PATH).strip()
        or _DEFAULT_TAVILY_EXTRACT_PATH,
        search_depth=str(tavily_payload.get("search_depth", "basic") or "basic").strip() or "basic",
        extract_depth=str(tavily_payload.get("extract_depth", "basic") or "basic").strip() or "basic",
        content_format=str(tavily_payload.get("content_format", "markdown") or "markdown").strip() or "markdown",
        max_results=_normalize_positive_int(tavily_payload.get("max_results"), default=5),
        include_answer=bool(tavily_payload.get("include_answer", True)),
        api_key_env=str(tavily_payload.get("api_key_env", "TAVILY_API_KEY") or "TAVILY_API_KEY").strip()
        or "TAVILY_API_KEY",
    )
    return WebSearchClientConfig(
        provider=provider,
        provider_priority=priority,
        youcom=youcom,
        tavily=tavily,
    )


def _normalize_results(raw_results: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_results, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "") or "").strip()
        url = str(item.get("url", "") or "").strip()
        snippet = str(item.get("snippet", item.get("content", item.get("description", ""))) or "").strip()
        if not snippet:
            snippets = item.get("snippets")
            if isinstance(snippets, list) and snippets:
                snippet = str(snippets[0] or "").strip()
        content = str(
            item.get("raw_content", item.get("markdown", item.get("html", ""))) or ""
        ).strip()
        if not title and not url and not snippet and not content:
            continue
        result: Dict[str, Any] = {"title": title, "url": url, "snippet": snippet}
        if content:
            result["content"] = content
            result["content_chars"] = len(content)
        metadata = item.get("metadata")
        if isinstance(metadata, dict) and metadata:
            result["metadata"] = metadata
        normalized.append(result)
    return normalized


def _first_content_result(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    for item in results:
        content = str(item.get("content", "") or "").strip()
        if content:
            return item
    return results[0] if results else {}


def _resolve_youcom_api_key(config: YouComClientConfig, tavily_env: str) -> str:
    api_key = _env_key(config.api_key_env)
    if api_key:
        return api_key
    legacy_key = _env_key(tavily_env)
    if legacy_key and _looks_like_youcom_key(legacy_key):
        logger.warning(
            "Using You.com API key from %s; prefer setting %s instead.",
            tavily_env,
            config.api_key_env,
        )
        return legacy_key
    raise WebSearchAuthError(
        f"You.com API key is missing. Set `{config.api_key_env}` in the environment.",
    )


def _resolve_tavily_api_key(config: TavilyClientConfig) -> str:
    api_key = _env_key(config.api_key_env)
    if not api_key:
        raise WebSearchAuthError(
            f"Tavily API key is missing. Set `{config.api_key_env}` in the environment."
        )
    if _looks_like_youcom_key(api_key):
        raise WebSearchAuthError(
            f"`{config.api_key_env}` contains a You.com key (ydc-...). "
            f"Use `{config.api_key_env}` for Tavily keys (tvly-...) or set YDC_API_KEY for You.com."
        )
    return api_key


class WebSearchClient:
    """Thin wrapper around configured web search providers."""

    def __init__(
        self,
        *,
        config: Optional[WebSearchClientConfig] = None,
        http_client: Any = None,
    ) -> None:
        self.config = config or load_web_search_config()
        self._http_client = http_client

    @staticmethod
    def _import_httpx() -> Any:
        try:
            import httpx
        except ModuleNotFoundError as exc:
            raise WebSearchDependencyError(
                "HTTP client dependency is missing. Install: httpx"
            ) from exc
        return httpx

    def _get_http_client(self) -> Any:
        if self._http_client is not None:
            return self._http_client
        httpx = self._import_httpx()
        self._http_client = httpx.Client(timeout=30.0)
        return self._http_client

    def provider_status(self) -> Dict[str, Any]:
        return list_web_search_provider_status(self.config)

    def search(
        self,
        query: Optional[str] = None,
        *,
        url: Optional[str] = None,
        max_results: Optional[int] = None,
        provider: Optional[str] = None,
        include_content: Optional[bool] = None,
    ) -> Dict[str, Any]:
        safe_query = str(query or "").strip()
        safe_url = str(url or "").strip()
        if not safe_query and not safe_url:
            return {
                "query": "",
                "url": "",
                "mode": "clarify",
                "answer": "",
                "results": [],
                "result_count": 0,
                "insufficient": True,
                "needs_clarification": True,
                "message": "Search query or URL is required.",
                "provider_status": self.provider_status(),
            }

        selected_provider = str(provider or "").strip().lower()
        if not selected_provider:
            selected_provider = resolve_web_search_provider(self.config)

        if selected_provider == "youcom":
            if safe_url:
                result = self._extract_youcom(safe_url, query=safe_query or None)
            else:
                result = self._search_youcom(
                    safe_query,
                    max_results=max_results,
                    include_content=include_content,
                )
        elif selected_provider == "tavily":
            if safe_url:
                result = self._extract_tavily(safe_url, query=safe_query or None)
            else:
                result = self._search_tavily(
                    safe_query,
                    max_results=max_results,
                    include_content=include_content,
                )
        else:
            raise WebSearchApiError(f"Unsupported web search provider: {selected_provider}")

        result["provider"] = selected_provider
        result["provider_status"] = self.provider_status()
        return result

    def _search_tavily(
        self,
        safe_query: str,
        *,
        max_results: Optional[int],
        include_content: Optional[bool],
    ) -> Dict[str, Any]:
        tavily = self.config.tavily
        effective_max_results = _normalize_positive_int(
            max_results if max_results is not None else tavily.max_results,
            default=tavily.max_results,
        )
        api_key = _resolve_tavily_api_key(tavily)

        base_url = tavily.base_url.rstrip("/")
        search_path = tavily.search_path if tavily.search_path.startswith("/") else f"/{tavily.search_path}"
        url = f"{base_url}{search_path}"
        payload = {
            "api_key": api_key,
            "query": safe_query,
            "search_depth": tavily.search_depth,
            "max_results": effective_max_results,
            "include_answer": bool(tavily.include_answer),
        }
        if include_content is not None:
            payload["include_raw_content"] = bool(include_content)

        response = self._request("POST", url, json_body=payload)
        body = self._parse_json_response(response)
        results = _normalize_results(body.get("results"))
        answer = str(body.get("answer", "") or "").strip()
        if not answer and results:
            answer = results[0].get("snippet", "")

        return {
            "mode": "search_with_content" if include_content else "search",
            "query": safe_query,
            "url": "",
            "answer": answer,
            "results": results,
            "result_count": len(results),
            "insufficient": len(results) == 0 and not answer,
        }

    def _search_youcom(
        self,
        safe_query: str,
        *,
        max_results: Optional[int],
        include_content: Optional[bool],
    ) -> Dict[str, Any]:
        youcom = self.config.youcom
        effective_max_results = _normalize_positive_int(
            max_results if max_results is not None else youcom.max_results,
            default=youcom.max_results,
        )
        api_key = _resolve_youcom_api_key(youcom, self.config.tavily.api_key_env)

        base_url = youcom.base_url.rstrip("/")
        search_path = youcom.search_path if youcom.search_path.startswith("/") else f"/{youcom.search_path}"
        url = f"{base_url}{search_path}"
        params = {"query": safe_query, "count": effective_max_results}
        if include_content:
            params["livecrawl"] = "web"
            params["livecrawl_formats"] = "markdown"

        response = self._request(
            "GET",
            url,
            params=params,
            headers={"X-API-Key": api_key},
        )
        body = self._parse_json_response(response)
        raw_results = body.get("results")
        web_items: List[Any] = []
        news_items: List[Any] = []
        if isinstance(raw_results, dict):
            if isinstance(raw_results.get("web"), list):
                web_items = raw_results["web"]
            if isinstance(raw_results.get("news"), list):
                news_items = raw_results["news"]
        combined = web_items + news_items
        results = _normalize_results(combined)
        answer = ""
        if results:
            answer = results[0].get("snippet", "") or results[0].get("title", "")

        return {
            "mode": "search_with_content" if include_content else "search",
            "query": safe_query,
            "url": "",
            "answer": answer,
            "results": results,
            "result_count": len(results),
            "insufficient": len(results) == 0,
        }

    def _extract_tavily(self, safe_url: str, *, query: Optional[str]) -> Dict[str, Any]:
        tavily = self.config.tavily
        api_key = _resolve_tavily_api_key(tavily)
        base_url = tavily.base_url.rstrip("/")
        extract_path = tavily.extract_path if tavily.extract_path.startswith("/") else f"/{tavily.extract_path}"
        endpoint = f"{base_url}{extract_path}"
        payload: Dict[str, Any] = {
            "urls": safe_url,
            "extract_depth": tavily.extract_depth,
            "format": tavily.content_format,
        }
        safe_query = str(query or "").strip()
        if safe_query:
            payload["query"] = safe_query

        response = self._request(
            "POST",
            endpoint,
            json_body=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        body = self._parse_json_response(response)
        results = _normalize_results(body.get("results"))
        first = _first_content_result(results)
        content = str(first.get("content", "") or "").strip()
        answer = content[:1000] if content else str(first.get("snippet", "") or "").strip()

        return {
            "mode": "extract",
            "query": safe_query,
            "url": safe_url,
            "answer": answer,
            "content": content,
            "content_chars": len(content),
            "results": results,
            "result_count": len(results),
            "failed_results": body.get("failed_results") if isinstance(body.get("failed_results"), list) else [],
            "insufficient": not bool(content),
        }

    def _extract_youcom(self, safe_url: str, *, query: Optional[str]) -> Dict[str, Any]:
        youcom = self.config.youcom
        api_key = _resolve_youcom_api_key(youcom, self.config.tavily.api_key_env)
        base_url = youcom.base_url.rstrip("/")
        contents_path = youcom.contents_path if youcom.contents_path.startswith("/") else f"/{youcom.contents_path}"
        endpoint = f"{base_url}{contents_path}"
        payload = {
            "urls": [safe_url],
            "formats": ["markdown", "metadata"],
        }

        response = self._request(
            "POST",
            endpoint,
            json_body=payload,
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        )
        body = self._parse_json_response(response)
        raw_results: Any = body.get("results") if isinstance(body, dict) and isinstance(body.get("results"), list) else None
        if raw_results is None:
            raw_results = body if isinstance(body, list) else []
        results = _normalize_results(raw_results)
        first = _first_content_result(results)
        content = str(first.get("content", "") or "").strip()
        answer = content[:1000] if content else str(first.get("snippet", "") or "").strip()
        safe_query = str(query or "").strip()

        return {
            "mode": "extract",
            "query": safe_query,
            "url": safe_url,
            "answer": answer,
            "content": content,
            "content_chars": len(content),
            "results": results,
            "result_count": len(results),
            "insufficient": not bool(content),
        }

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        client = self._get_http_client()
        try:
            if method.upper() == "GET":
                return client.get(url, params=params, headers=headers)
            return client.post(url, json=json_body, headers=headers)
        except Exception as exc:
            logger.warning("Web search request failed: %s", exc, exc_info=True)
            raise WebSearchApiError(f"Web search request failed: {exc}") from exc

    @staticmethod
    def _parse_json_response(response: Any) -> Any:
        if response.status_code in {401, 403}:
            raise WebSearchAuthError("Web search API key was rejected by the provider.")
        if response.status_code >= 400:
            detail = str(getattr(response, "text", "") or "").strip()
            raise WebSearchApiError(
                f"Web search provider returned HTTP {response.status_code}"
                + (f": {detail}" if detail else "")
            )
        try:
            body = response.json()
        except Exception as exc:
            raise WebSearchApiError("Web search provider returned invalid JSON.") from exc
        if not isinstance(body, (dict, list)):
            raise WebSearchApiError("Web search provider returned an unexpected payload.")
        return body


__all__ = [
    "WebSearchApiError",
    "WebSearchAuthError",
    "WebSearchClient",
    "WebSearchClientConfig",
    "WebSearchDependencyError",
    "YouComClientConfig",
    "TavilyClientConfig",
    "format_web_search_setup_hint",
    "list_web_search_provider_status",
    "load_web_search_config",
    "resolve_web_search_provider",
]
