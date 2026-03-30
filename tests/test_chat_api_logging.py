from __future__ import annotations

import io
import logging
from contextlib import contextmanager

from m_agent.api import chat_api


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@contextmanager
def _root_capture(level: int):
    root_logger = logging.getLogger()
    previous_handlers = list(root_logger.handlers)
    previous_level = root_logger.level
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.handlers = [handler]
    root_logger.setLevel(level)
    try:
        yield stream
    finally:
        root_logger.handlers = previous_handlers
        root_logger.setLevel(previous_level)


@contextmanager
def _protocol_capture():
    protocol_logger = chat_api.protocol_logger
    previous_handlers = list(protocol_logger.handlers)
    previous_level = protocol_logger.level
    previous_propagate = protocol_logger.propagate
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    protocol_logger.handlers = [handler]
    protocol_logger.setLevel(logging.INFO)
    protocol_logger.propagate = False
    try:
        yield stream
    finally:
        protocol_logger.handlers = previous_handlers
        protocol_logger.setLevel(previous_level)
        protocol_logger.propagate = previous_propagate


def test_attach_trace_handler_keeps_info_out_of_root_logs_in_concise_mode() -> None:
    target_logger = logging.getLogger("m_agent.agents.chat_controller_agent")
    previous_handlers = list(target_logger.handlers)
    previous_level = target_logger.level
    previous_propagate = target_logger.propagate
    target_logger.handlers = []
    target_logger.setLevel(logging.NOTSET)
    target_logger.propagate = True

    trace_handler = ListHandler()
    try:
        with _root_capture(logging.WARNING) as stream:
            attached = chat_api._attach_trace_handler(trace_handler)
            try:
                target_logger.info("RECALL START: concise mode test")
            finally:
                chat_api._detach_trace_handler(trace_handler, attached)

            assert "RECALL START: concise mode test" in trace_handler.messages
            assert "RECALL START: concise mode test" not in stream.getvalue()
            assert target_logger.level == logging.NOTSET
            assert target_logger.propagate is True
    finally:
        target_logger.handlers = previous_handlers
        target_logger.setLevel(previous_level)
        target_logger.propagate = previous_propagate


def test_protocol_logger_includes_trace_sse_events() -> None:
    with _protocol_capture() as stream:
        chat_api._log_protocol_event(
            "run",
            "run_demo",
            "question_strategy",
            {
                "question": "她上周去了哪里？",
                "decompose_first": False,
                "reason": "single-goal lookup",
            },
        )
        chat_api._log_protocol_event(
            "run",
            "run_demo",
            "tool_call",
            {
                "call_id": 1,
                "tool_name": "search_details",
                "status": "started",
                "params": {"question": "她上周去了哪里？", "topk": 5},
            },
        )
        chat_api._log_protocol_event(
            "run",
            "run_demo",
            "tool_result",
            {
                "call_id": 1,
                "tool_name": "search_details",
                "status": "completed",
                "result": {"answer": "她上周去了杭州。"},
            },
        )
        chat_api._log_protocol_event(
            "run",
            "run_demo",
            "final_answer_payload",
            {
                "answer": "她上周去了杭州。",
                "gold_answer": "杭州",
                "tool_call_count": 1,
            },
        )

    text = stream.getvalue()
    assert "question_strategy" in text
    assert "decompose_first=False" in text
    assert "tool_call" in text
    assert "tool=search_details" in text
    assert "tool_result" in text
    assert "answer=她上周去了杭州。" in text
    assert "final_answer_payload" in text
    assert "tool_calls=1" in text


def test_attach_trace_handler_allows_root_logs_in_debug_mode() -> None:
    target_logger = logging.getLogger("m_agent.agents.chat_controller_agent")
    previous_handlers = list(target_logger.handlers)
    previous_level = target_logger.level
    previous_propagate = target_logger.propagate
    target_logger.handlers = []
    target_logger.setLevel(logging.NOTSET)
    target_logger.propagate = True

    trace_handler = ListHandler()
    try:
        with _root_capture(logging.INFO) as stream:
            attached = chat_api._attach_trace_handler(trace_handler)
            try:
                target_logger.info("RECALL START: debug mode test")
            finally:
                chat_api._detach_trace_handler(trace_handler, attached)

            assert "RECALL START: debug mode test" in trace_handler.messages
            assert "RECALL START: debug mode test" in stream.getvalue()
            assert target_logger.level == logging.NOTSET
            assert target_logger.propagate is True
    finally:
        target_logger.handlers = previous_handlers
        target_logger.setLevel(previous_level)
        target_logger.propagate = previous_propagate
