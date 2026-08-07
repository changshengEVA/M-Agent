from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from m_agent.runtime.domain.contracts import TransactionKind
from m_agent.runtime.host import RuntimeHost, create_runtime_host
from m_agent.runtime.host.flush_journal import FlushJournal
from m_agent.runtime.langgraph.runtime import LangGraphRuntime
from m_agent.runtime.routing import (
    LANGGRAPH_RUNTIME_ENGINE,
    resolve_runtime_engine_from_config,
)
from m_agent.runtime.transaction.store import SQLiteRuntimeStore


class _FakeThinkingAgent:
    def resolve_transaction(self, *_args, **_kwargs):
        return None


def _fake_agent(runtime: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        thinking_agent=_FakeThinkingAgent(),
        execution_agent=None,
        systems=SimpleNamespace(wm=None),
        config={
            "runtime": runtime
            or {
                "common": {},
                "langgraph": {"turn_loop": False},
            }
        },
        user_name="User",
        assistant_name="Assistant",
        default_thread_id="host-test-thread",
        persist_memory=False,
    )


def test_factory_returns_the_runtime_protocol(tmp_path: Path) -> None:
    host = create_runtime_host(
        agent=_fake_agent(),  # type: ignore[arg-type]
        owner_id="host-user",
        persist_root=tmp_path / "runtime",
    )
    try:
        assert isinstance(host, LangGraphRuntime)
        assert isinstance(host, RuntimeHost)
        assert host.runtime_engine_id == LANGGRAPH_RUNTIME_ENGINE
        assert host.turn_loop_enabled is False
    finally:
        host.shutdown()


def test_factory_rejects_removed_selector_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("M_AGENT_DEFAULT_RUNTIME_ENGINE", LANGGRAPH_RUNTIME_ENGINE)
    with pytest.raises(ValueError, match="was removed"):
        resolve_runtime_engine_from_config({})


@pytest.mark.parametrize(
    "runtime_config",
    [
        {"default_engine": LANGGRAPH_RUNTIME_ENGINE},
        {"runtime_engine": LANGGRAPH_RUNTIME_ENGINE},
        {"_".join(("think", "life")): {}},
        {"selected": "_".join(("think", "life", "v1"))},
    ],
)
def test_factory_rejects_removed_runtime_configuration(
    runtime_config: dict,
) -> None:
    with pytest.raises(ValueError, match="removed runtime configuration"):
        resolve_runtime_engine_from_config(runtime_config)


def test_factory_rejects_unknown_explicit_runtime() -> None:
    with pytest.raises(ValueError, match="only 'langgraph_v1'"):
        resolve_runtime_engine_from_config({}, explicit="unknown_v2")


def test_final_runtime_rejects_online_retired_database(tmp_path: Path) -> None:
    persistence_root = tmp_path / "runtime-user"
    runtime_dir = persistence_root / "runtime"
    runtime_dir.mkdir(parents=True)
    retired_name = "".join(("think", "_", "life", ".sqlite3"))
    (runtime_dir / retired_name).write_bytes(b"retired")

    with pytest.raises(RuntimeError, match="retired runtime database remains"):
        LangGraphRuntime(
            _fake_agent(),  # type: ignore[arg-type]
            owner_id="retired-data-user",
            persist_root=persistence_root,
        )


def test_langgraph_runtime_advance_transaction_mvp(tmp_path: Path) -> None:
    runtime = LangGraphRuntime(
        _fake_agent(),  # type: ignore[arg-type]
        owner_id="mvp-user",
        persist_root=tmp_path / "runtime",
    )
    try:
        record = runtime.registry.create(
            thread_id="thread-a",
            conversation_id="thread-a::0",
            kind=TransactionKind.USER_TASK,
        )
        assert record.runtime_engine == LANGGRAPH_RUNTIME_ENGINE
        result = runtime.advance_transaction(
            record.transaction_id,
            user_text="hello from MVP host",
        )
        assert result["success"] is True
        assert result["graph_phase"] == "step_committed"
        loaded = runtime.registry.get(record.transaction_id)
        assert loaded is not None
        assert loaded.wm_entries
        assert loaded.task_state.goal == "hello from MVP host"
    finally:
        runtime.shutdown()


def test_langgraph_runtime_submit_and_run_thread(tmp_path: Path) -> None:
    runtime = LangGraphRuntime(
        _fake_agent(),  # type: ignore[arg-type]
        owner_id="thread-user",
        persist_root=tmp_path / "runtime",
    )
    try:
        stimulus_id = runtime.submit_user_message(
            thread_id="thread-b",
            conversation_id="thread-b::0",
            text="queue me",
            schedule_drainer=False,
        )
        assert stimulus_id
        result = runtime.run_thread("thread-b")
        assert result["success"] is True
        assert result["runtime_engine_id"] == LANGGRAPH_RUNTIME_ENGINE
        assert result["results"][0]["graph_phase"] == "step_committed"
        assert runtime.pending_count("thread-b") == 0
    finally:
        runtime.shutdown()


def test_langgraph_runtime_submit_user_message_chat_adapter_facade(
    tmp_path: Path,
) -> None:
    runtime = LangGraphRuntime(
        _fake_agent(),  # type: ignore[arg-type]
        owner_id="thread-user",
        persist_root=tmp_path / "runtime",
    )
    try:
        first = runtime.submit_user_message(
            thread_id="thread-chat",
            conversation_id="thread-chat::0",
            text="hello",
            payload={"user_turn": {"speaker": "thread-user", "text": "hello"}},
            schedule_drainer=False,
            message_id="run_facade_1",
            subject="thread-user",
        )
        second = runtime.submit_user_message(
            thread_id="thread-chat",
            conversation_id="thread-chat::0",
            text="hello again",
            payload={"user_turn": {"speaker": "thread-user", "text": "hello again"}},
            schedule_drainer=False,
            message_id="run_facade_1",
            subject="thread-user",
        )
        assert first == second
        traces = runtime.list_stimulus_trace(ingress_key="chat:thread-chat:run_facade_1")
        assert traces
        assert any(
            item.get("disposition") == "merged"
            or item.get("reason_code") in {"duplicate_ingress", "merged_existing"}
            for item in traces
        )
    finally:
        runtime.shutdown()


def test_langgraph_runtime_recovers_pending_flush_during_startup(
    tmp_path: Path,
) -> None:
    persistence_root = tmp_path / "startup-flush-runtime"
    runtime_dir = persistence_root / "runtime"
    runtime_dir.mkdir(parents=True)
    journal_path = runtime_dir / "runtime-flush.sqlite3"
    snapshot = {
        "schema_version": 1,
        "thread_id": "startup-thread",
        "conversation_id": "startup-thread::2",
        "source": "chat_api_thread_flush",
        "runtimes": {
            LANGGRAPH_RUNTIME_ENGINE: {
                "conversation_id": "startup-thread::2",
                "flush_watermark": 0,
                "through_seq": 2,
                "scene_entries": [
                    {
                        "seq": 1,
                        "occurred_at": "2026-08-01T09:25:30Z",
                        "entry_type": "utterance",
                        "actor": "user",
                        "text": "remember this after restart",
                        "append_id": "startup-user",
                        "transaction_id": "startup-tx",
                        "delegate_id": None,
                        "tool_name": None,
                        "payload_ref": None,
                    },
                    {
                        "seq": 2,
                        "occurred_at": "2026-08-01T09:25:31Z",
                        "entry_type": "reply",
                        "actor": "assistant",
                        "text": "remembered",
                        "append_id": "startup-reply",
                        "transaction_id": "startup-tx",
                        "delegate_id": None,
                        "tool_name": "reply_to_user",
                        "payload_ref": None,
                    },
                ],
                "eligible_revisions": {},
            }
        },
        "payload_digest": "startup-recovery-test",
        "flush_id": "runtime_flush_startup_recovery_test",
    }
    journal = FlushJournal(journal_path)
    journal.create_or_get_pending(
        "startup-thread::2",
        "startup-thread",
        snapshot["flush_id"],
        snapshot,
    )
    journal.close()
    runtime_store = SQLiteRuntimeStore(runtime_dir / "langgraph.sqlite3")
    runtime_store.commit_flush(
        flush_id=f"{snapshot['flush_id']}:{LANGGRAPH_RUNTIME_ENGINE}",
        conversation_id="startup-thread::2",
        through_seq=2,
        payload={
            "flush_mode": "scene",
            "external_dialogue_written": False,
        },
        eligible_revisions={},
    )
    runtime_store.close()

    write_calls: list[dict] = []
    agent = _fake_agent()

    def persist_dialogue_payload(**kwargs):
        write_calls.append(dict(kwargs))
        dialogue = dict(kwargs["dialogue_payload"])
        return {
            "success": True,
            "dialogue_id": dialogue["dialogue_id"],
            "round_count": 1,
            "turn_count": 2,
            "import_result": None,
        }

    agent.persist_dialogue_payload = persist_dialogue_payload
    runtime = LangGraphRuntime(
        agent,  # type: ignore[arg-type]
        owner_id="startup-flush-user",
        persist_root=persistence_root,
    )
    try:
        recovery = runtime.health()["flush_journal"]["startup_recovery"]
        assert recovery["pending_before"] == 1
        assert recovery["pending_after"] == 0
        assert recovery["failed"] == []
        assert recovery["recovered"][0]["runtime_commit_reconciled"] is True
        assert runtime.load_conversation_seq("startup-thread") == 3
        assert len(write_calls) == 1
        assert write_calls[0]["thread_id"] == "startup-thread"
        assert write_calls[0]["dialogue_payload"]["dialogue_id"]
    finally:
        runtime.shutdown()


def test_failed_startup_flush_recovery_fences_admission_until_retried(
    tmp_path: Path,
) -> None:
    persistence_root = tmp_path / "failed-startup-flush-runtime"
    runtime_dir = persistence_root / "runtime"
    runtime_dir.mkdir(parents=True)
    snapshot = {
        "schema_version": 1,
        "thread_id": "fenced-startup-thread",
        "conversation_id": "fenced-startup-thread::0",
        "source": "chat_api_thread_flush",
        "runtimes": {
            LANGGRAPH_RUNTIME_ENGINE: {
                "conversation_id": "fenced-startup-thread::0",
                "flush_watermark": 0,
                "through_seq": 2,
                "scene_entries": [
                    {
                        "seq": 1,
                        "occurred_at": "2026-08-01T10:00:00Z",
                        "entry_type": "utterance",
                        "actor": "user",
                        "text": "frozen user turn",
                        "append_id": "fenced-user",
                        "transaction_id": "fenced-tx",
                        "delegate_id": None,
                        "tool_name": None,
                        "payload_ref": None,
                    },
                    {
                        "seq": 2,
                        "occurred_at": "2026-08-01T10:00:01Z",
                        "entry_type": "reply",
                        "actor": "assistant",
                        "text": "frozen reply",
                        "append_id": "fenced-reply",
                        "transaction_id": "fenced-tx",
                        "delegate_id": None,
                        "tool_name": "reply_to_user",
                        "payload_ref": None,
                    },
                ],
                "eligible_revisions": {},
            }
        },
        "payload_digest": "failed-startup-recovery-test",
        "flush_id": "runtime_flush_failed_startup_recovery_test",
    }
    journal = FlushJournal(runtime_dir / "runtime-flush.sqlite3")
    journal.create_or_get_pending(
        snapshot["conversation_id"],
        snapshot["thread_id"],
        snapshot["flush_id"],
        snapshot,
    )
    journal.close()

    agent = _fake_agent()
    agent.persist_dialogue_payload = lambda **_kwargs: {
        "success": False,
        "error": "dialogue sink offline",
    }
    runtime = LangGraphRuntime(
        agent,  # type: ignore[arg-type]
        owner_id="failed-startup-flush-user",
        persist_root=persistence_root,
    )
    try:
        recovery = runtime.health()["flush_journal"]["startup_recovery"]
        assert recovery["failure_count"] == 1
        assert runtime.has_pending_flush(snapshot["thread_id"]) is True

        with pytest.raises(RuntimeError, match="unfinished durable flush"):
            runtime.submit_user_message(
                thread_id=snapshot["thread_id"],
                text="must not cross frozen boundary",
                schedule_drainer=False,
            )
        with pytest.raises(RuntimeError, match="unfinished durable flush"):
            runtime.enqueue_schedule(
                thread_id=snapshot["thread_id"],
                schedule_id="sch_fenced",
                text="must also wait",
            )
        assert runtime.pending_count(snapshot["thread_id"]) == 0

        agent.persist_dialogue_payload = lambda **kwargs: {
            "success": True,
            "dialogue_id": kwargs["dialogue_payload"]["dialogue_id"],
        }
        retried = runtime.recover_pending_flushes()
        assert retried["failure_count"] == 0
        assert retried["pending_after"] == 0
        accepted_id = runtime.submit_user_message(
            thread_id=snapshot["thread_id"],
            text="continue after recovery",
            schedule_drainer=False,
        )
        assert accepted_id
    finally:
        runtime.shutdown()


def test_schedule_admission_finishes_before_concurrent_flush_prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = LangGraphRuntime(
        _fake_agent(),  # type: ignore[arg-type]
        owner_id="schedule-first-fence-user",
        persist_root=tmp_path / "schedule-first-runtime",
    )
    thread_id = "schedule-first-thread"
    admission_checked = threading.Event()
    release_admission = threading.Event()
    flush_started = threading.Event()
    flush_done = threading.Event()
    schedule_result: dict[str, object] = {}
    schedule_errors: list[BaseException] = []
    retried_flush_result: dict[str, object] = {}
    flush_errors: list[BaseException] = []
    schedule_thread: threading.Thread | None = None
    flush_thread: threading.Thread | None = None
    original_has_pending = runtime.has_pending_flush

    def paused_has_pending(candidate_thread_id: str) -> bool:
        pending = original_has_pending(candidate_thread_id)
        if threading.current_thread().name == "schedule-admission":
            admission_checked.set()
            if not release_admission.wait(timeout=2):
                raise TimeoutError("test did not release schedule admission")
        return pending

    def admit_schedule() -> None:
        try:
            schedule_result.update(
                runtime.enqueue_schedule(
                    thread_id=thread_id,
                    schedule_id="schedule-atomic-first",
                    text="admit before the flush boundary",
                )
            )
        except BaseException as exc:  # pragma: no cover - assertion captures it
            schedule_errors.append(exc)

    def prepare_flush() -> None:
        flush_started.set()
        try:
            runtime.prepare_flush_segment(
                thread_id,
                conversation_id=f"{thread_id}::0",
            )
        except BaseException as exc:  # pragma: no cover - assertion captures it
            flush_errors.append(exc)
        finally:
            flush_done.set()

    monkeypatch.setattr(runtime, "has_pending_flush", paused_has_pending)
    monkeypatch.setattr(runtime.drainer, "ensure_running", lambda _thread_id: None)
    try:
        schedule_thread = threading.Thread(
            target=admit_schedule,
            name="schedule-admission",
        )
        schedule_thread.start()
        assert admission_checked.wait(timeout=2)

        flush_thread = threading.Thread(target=prepare_flush, name="flush-prepare")
        flush_thread.start()
        assert flush_started.wait(timeout=2)
        assert flush_done.wait(timeout=0.1) is False
    finally:
        release_admission.set()
        if schedule_thread is not None:
            schedule_thread.join(timeout=2)
        if flush_thread is not None:
            flush_thread.join(timeout=2)

    try:
        assert schedule_thread is not None and not schedule_thread.is_alive()
        assert flush_thread is not None and not flush_thread.is_alive()
        assert schedule_errors == []
        assert len(flush_errors) == 1
        assert isinstance(flush_errors[0], RuntimeError)
        assert "stimuli_queued" in str(flush_errors[0])
        assert schedule_result["accepted"] is True
        assert runtime.pending_count(thread_id) == 1
        assert runtime.has_pending_flush(thread_id) is False

        drained = runtime.run_thread(thread_id)
        assert drained["success"] is True
        assert runtime.pending_count(thread_id) == 0
        retried_flush_result.update(
            runtime.prepare_flush_segment(
                thread_id,
                conversation_id=f"{thread_id}::0",
            )
        )
        assert retried_flush_result["journal_status"] == "pending"
    finally:
        runtime.shutdown()


def test_flush_prepare_fences_concurrent_schedule_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = LangGraphRuntime(
        _fake_agent(),  # type: ignore[arg-type]
        owner_id="flush-first-fence-user",
        persist_root=tmp_path / "flush-first-runtime",
    )
    thread_id = "flush-first-thread"
    prepare_entered = threading.Event()
    release_prepare = threading.Event()
    schedule_started = threading.Event()
    schedule_done = threading.Event()
    flush_errors: list[BaseException] = []
    schedule_errors: list[BaseException] = []
    flush_result: dict[str, object] = {}
    schedule_thread: threading.Thread | None = None
    flush_thread: threading.Thread | None = None
    original_prepare = runtime.flush_orchestrator.prepare

    def paused_prepare(*args, **kwargs):
        prepare_entered.set()
        if not release_prepare.wait(timeout=2):
            raise TimeoutError("test did not release flush preparation")
        return original_prepare(*args, **kwargs)

    def prepare_flush() -> None:
        try:
            flush_result.update(
                runtime.prepare_flush_segment(
                    thread_id,
                    conversation_id=f"{thread_id}::0",
                )
            )
        except BaseException as exc:  # pragma: no cover - assertion captures it
            flush_errors.append(exc)

    def admit_schedule() -> None:
        schedule_started.set()
        try:
            runtime.enqueue_schedule(
                thread_id=thread_id,
                schedule_id="schedule-atomic-second",
                text="must stay behind the frozen boundary",
            )
        except BaseException as exc:
            schedule_errors.append(exc)
        finally:
            schedule_done.set()

    monkeypatch.setattr(runtime.flush_orchestrator, "prepare", paused_prepare)
    monkeypatch.setattr(runtime.drainer, "ensure_running", lambda _thread_id: None)
    try:
        flush_thread = threading.Thread(target=prepare_flush, name="flush-prepare")
        flush_thread.start()
        assert prepare_entered.wait(timeout=2)

        schedule_thread = threading.Thread(
            target=admit_schedule,
            name="schedule-admission",
        )
        schedule_thread.start()
        assert schedule_started.wait(timeout=2)
        assert schedule_done.wait(timeout=0.1) is False
    finally:
        release_prepare.set()
        if flush_thread is not None:
            flush_thread.join(timeout=2)
        if schedule_thread is not None:
            schedule_thread.join(timeout=2)

    try:
        assert flush_thread is not None and not flush_thread.is_alive()
        assert schedule_thread is not None and not schedule_thread.is_alive()
        assert flush_errors == []
        assert flush_result["journal_status"] == "pending"
        assert len(schedule_errors) == 1
        assert isinstance(schedule_errors[0], RuntimeError)
        assert "unfinished durable flush" in str(schedule_errors[0])
        assert runtime.pending_count(thread_id) == 0
    finally:
        runtime.shutdown()
