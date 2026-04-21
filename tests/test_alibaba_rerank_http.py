"""HTTP payload shape for DashScope rerank (gte vs qwen3-rerank)."""

from __future__ import annotations

import json
from unittest.mock import patch

from m_agent.load_model.AlibabaRerankCall import _rerank_via_http


class _FakeResp:
    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def read(self) -> bytes:
        return json.dumps(
            {
                "output": {
                    "results": [
                        {"index": 0, "relevance_score": 0.9},
                    ]
                }
            },
            ensure_ascii=False,
        ).encode("utf-8")


def test_rerank_http_gte_rerank_v2_uses_legacy_url_and_nested_input() -> None:
    captured: dict[str, object] = {}

    def _capture_urlopen(req, timeout: int = 30):
        captured["full_url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp()

    with patch("urllib.request.urlopen", side_effect=_capture_urlopen):
        out = _rerank_via_http(
            query="q",
            documents=["a", "b"],
            top_n=2,
            model="gte-rerank-v2",
            api_key="sk-test",
        )

    assert out == [{"index": 0, "relevance_score": 0.9}]
    assert captured["full_url"] == (
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    )
    payload = captured["payload"]
    assert payload["model"] == "gte-rerank-v2"
    assert payload["input"] == {"query": "q", "documents": ["a", "b"]}
    assert payload["parameters"] == {"return_documents": False, "top_n": 2}


def test_rerank_http_qwen3_rerank_uses_compat_url_and_flat_body() -> None:
    captured: dict[str, object] = {}

    def _capture_urlopen(req, timeout: int = 30):
        captured["full_url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp()

    with patch("urllib.request.urlopen", side_effect=_capture_urlopen):
        out = _rerank_via_http(
            query="什么是文本排序模型",
            documents=["doc1", "doc2"],
            top_n=2,
            model="qwen3-rerank",
            api_key="sk-test",
        )

    assert out == [{"index": 0, "relevance_score": 0.9}]
    assert captured["full_url"] == "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"
    payload = captured["payload"]
    assert payload == {
        "model": "qwen3-rerank",
        "query": "什么是文本排序模型",
        "documents": ["doc1", "doc2"],
        "top_n": 2,
    }
