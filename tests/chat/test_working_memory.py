from __future__ import annotations

from m_agent.chat.working_memory import (
    WorkingMemoryConfig,
    append_tool_history_to_working_memory,
    build_working_memory_api_payload,
    format_working_memory_prompt,
    normalize_working_memory_config,
    project_tool_call_to_entry,
)


def test_normalize_disabled() -> None:
    cfg = normalize_working_memory_config(False)
    assert cfg.enable is False


def test_project_recall() -> None:
    cfg = WorkingMemoryConfig()
    entry = project_tool_call_to_entry(
        {
            "tool_name": "deep_recall",
            "params": {"question": "What happened yesterday?"},
            "result": {"answer": "You discussed travel plans.", "tool_call_count": 3},
        },
        cfg,
    )
    assert entry is not None
    assert entry["kind"] == "recall"
    assert entry["mode"] == "deep_recall"
    assert "yesterday" in entry["question"]
    assert "travel" in entry["answer"]


def test_project_email_ask_caps_items() -> None:
    cfg = WorkingMemoryConfig(max_email_ask_items=3)
    idx = [
        {"message_id": f"id{i}", "thread_id": f"t{i}", "subject": f"S{i}"} for i in range(10)
    ]
    entry = project_tool_call_to_entry(
        {
            "tool_name": "email_ask",
            "params": {"keywords": "invoice", "mail_scope": "unread"},
            "result": {"evidence_index": idx},
        },
        cfg,
    )
    assert entry is not None
    assert len(entry["items"]) == 3


def test_append_and_trim_storage() -> None:
    cfg = WorkingMemoryConfig(max_stored_entries=5)
    entries: list = []
    for i in range(8):
        append_tool_history_to_working_memory(
            entries,
            [
                {
                    "tool_name": "shallow_recall",
                    "params": {"question": f"q{i}"},
                    "result": {"answer": f"a{i}"},
                }
            ],
            cfg,
        )
    assert len(entries) == 5
    assert entries[-1]["question"] == "q7"


def test_format_prompt_tail_inject_max() -> None:
    cfg = WorkingMemoryConfig(enable=True, inject_max_entries=2)
    entries = [
        project_tool_call_to_entry(
            {
                "tool_name": "shallow_recall",
                "params": {"question": f"q{i}"},
                "result": {"answer": f"a{i}"},
            },
            cfg,
        )
        for i in range(5)
    ]
    text = format_working_memory_prompt(entries, cfg, prompt_language="zh")
    assert "q4" in text
    assert "q2" not in text


def test_limit_entry() -> None:
    cfg = WorkingMemoryConfig()
    entry = project_tool_call_to_entry(
        {
            "tool_name": "email_read",
            "params": {},
            "result": {"limit_reached": True, "message": "blocked", "limit_scope": "tool"},
        },
        cfg,
    )
    assert entry is not None
    assert entry["kind"] == "limit"


def test_project_web_search_keeps_source_evidence() -> None:
    cfg = WorkingMemoryConfig(max_web_result_items=2, max_web_snippet_chars=40)
    entry = project_tool_call_to_entry(
        {
            "tool_name": "web_search",
            "params": {"query": "latest model news", "provider": "tavily"},
            "result": {
                "mode": "search",
                "provider": "tavily",
                "query": "latest model news",
                "answer": "The answer synthesized from search results.",
                "results": [
                    {
                        "title": "Source A",
                        "url": "https://example.com/a",
                        "snippet": "Source A says the important thing.",
                    },
                    {
                        "title": "Source B",
                        "url": "https://example.com/b",
                        "snippet": "Source B confirms the detail.",
                    },
                    {
                        "title": "Source C",
                        "url": "https://example.com/c",
                        "snippet": "Should be capped out.",
                    },
                ],
                "result_count": 3,
                "insufficient": False,
            },
        },
        cfg,
    )

    assert entry is not None
    assert entry["kind"] == "web_search"
    assert entry["mode"] == "search"
    assert entry["provider"] == "tavily"
    assert entry["result_count"] == 3
    assert len(entry["items"]) == 2
    assert entry["items"][0]["title"] == "Source A"


def test_format_prompt_renders_web_search_sources() -> None:
    cfg = WorkingMemoryConfig(max_web_result_items=2)
    entry = project_tool_call_to_entry(
        {
            "tool_name": "web_search",
            "params": {"query": "topic"},
            "result": {
                "mode": "extract",
                "provider": "youcom",
                "query": "topic",
                "url": "https://example.com/page",
                "answer": "Detailed extracted page answer.",
                "content_chars": 1234,
                "results": [
                    {
                        "title": "Example Page",
                        "url": "https://example.com/page",
                        "snippet": "Relevant page snippet.",
                        "content_chars": 1234,
                    }
                ],
                "result_count": 1,
            },
        },
        cfg,
    )

    text = format_working_memory_prompt([entry], cfg, prompt_language="en")

    assert "web_search" in text
    assert "mode=extract" in text
    assert "content_chars=1234" in text
    assert "Example Page" in text
    assert "Relevant page snippet" in text


def test_build_working_memory_api_payload_tail_and_cap() -> None:
    cfg = WorkingMemoryConfig(ui_expose_max_entries=3)
    entries = [{"kind": "recall", "n": i} for i in range(10)]
    payload = build_working_memory_api_payload(
        entries,
        cfg,
        task_progress={
            "goal": "answer a two-step request",
            "completion_status": "processing",
            "completed": ["found the event"],
            "remaining": ["reply to user"],
        },
    )
    assert payload["stored_entries"] == 10
    assert len(payload["entries"]) == 3
    assert payload["entries"][-1]["n"] == 9
    assert payload["task_progress"]["goal"] == "answer a two-step request"
    assert payload["task_progress"]["completion_status"] == "processing"
    assert payload["task_progress"]["completed"] == ["found the event"]


def test_format_working_memory_prompt_includes_task_progress_without_tool_entries() -> None:
    cfg = WorkingMemoryConfig()
    text = format_working_memory_prompt(
        [],
        cfg,
        prompt_language="en",
        task_progress={
            "goal": "collect two facts",
            "completion_status": "processing",
            "completed": ["checked email"],
            "remaining": ["check calendar"],
        },
    )

    assert "[Working memory]" in text
    assert "[Task progress]" in text
    assert "goal: collect two facts" in text
    assert "completion_status: processing" in text
    assert "checked email" in text
    assert "check calendar" in text
    assert "[Tool evidence]" not in text
