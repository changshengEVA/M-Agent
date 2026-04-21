"""Alibaba Cloud (DashScope) rerank API wrapper.

Uses the same ``ALIBABA_API_KEY`` environment variable as
``AlibabaEmbeddingCall``.  The returned callable has the signature::

    rerank_func(query: str, documents: list[str], top_n: int) -> list[dict]

Each dict in the result list contains ``index`` (int, 0-based position in the
original *documents* list) and ``relevance_score`` (float).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from m_agent.paths import ENV_PATH

logger = logging.getLogger(__name__)

_RERANK_API_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
)
# qwen3-rerank uses OpenAI-compatible rerank API (flat body); see Alibaba Model Studio docs.
_QWEN3_TEXT_RERANK_COMPAT_URL_DEFAULT = (
    "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"
)
_DEFAULT_MODEL = "gte-rerank-v2"


def _is_qwen3_text_rerank(model: str) -> bool:
    return model.strip().lower() == "qwen3-rerank"


def _qwen3_text_rerank_compat_url() -> str:
    return (
        os.getenv("ALIBABA_RERANK_COMPAT_URL", "").strip() or _QWEN3_TEXT_RERANK_COMPAT_URL_DEFAULT
    )


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
    except ModuleNotFoundError:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    else:
        load_dotenv(dotenv_path=path)


_load_env_file(ENV_PATH)


RerankResult = Dict[str, Any]
RerankFunc = Callable[[str, List[str], int], List[RerankResult]]


def _rerank_via_dashscope_sdk(
    query: str,
    documents: List[str],
    top_n: int,
    model: str,
) -> List[RerankResult]:
    import dashscope  # type: ignore
    from dashscope import TextReRank  # type: ignore

    resp = TextReRank.call(
        model=model,
        query=query,
        documents=documents,
        top_n=top_n,
        return_documents=False,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"DashScope rerank failed (status={resp.status_code}): "
            f"{getattr(resp, 'message', resp)}"
        )
    return [
        {"index": int(item.index), "relevance_score": float(item.relevance_score)}
        for item in resp.output.results
    ]


def _rerank_via_http(
    query: str,
    documents: List[str],
    top_n: int,
    model: str,
    api_key: str,
) -> List[RerankResult]:
    import urllib.request

    if _is_qwen3_text_rerank(model):
        url = _qwen3_text_rerank_compat_url()
        payload: Dict[str, Any] = {
            "model": model.strip(),
            "query": query,
            "documents": documents,
            "top_n": top_n,
        }
    else:
        url = _RERANK_API_URL
        payload = {
            "model": model,
            "input": {"query": query, "documents": documents},
            "parameters": {"return_documents": False, "top_n": top_n},
        }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    output = data.get("output", {})
    results = output.get("results", [])
    return [
        {
            "index": int(item["index"]),
            "relevance_score": float(item["relevance_score"]),
        }
        for item in results
    ]


def get_rerank_func(model_name: str = "") -> RerankFunc:
    """Return a rerank callable backed by Alibaba DashScope.

    Tries the ``dashscope`` SDK first; falls back to raw HTTP if the SDK is
    not installed.
    """
    resolved_model = (
        (model_name or os.getenv("ALIBABA_RERANK_MODEL", _DEFAULT_MODEL)).strip()
        or _DEFAULT_MODEL
    )
    api_key = os.getenv("ALIBABA_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ALIBABA_API_KEY is not set. Please configure it in .env")

    use_sdk: Optional[bool] = None
    try:
        import dashscope  # type: ignore  # noqa: F401

        dashscope.api_key = api_key
        use_sdk = True
        logger.info("Rerank: using dashscope SDK, model=%s", resolved_model)
    except ImportError:
        use_sdk = False
        logger.info("Rerank: dashscope SDK not found, using HTTP fallback, model=%s", resolved_model)

    def rerank_func(query: str, documents: List[str], top_n: int = 16) -> List[RerankResult]:
        if not documents:
            return []
        safe_top_n = min(max(1, top_n), len(documents))
        if use_sdk:
            return _rerank_via_dashscope_sdk(query, documents, safe_top_n, resolved_model)
        return _rerank_via_http(query, documents, safe_top_n, resolved_model, api_key)

    return rerank_func
