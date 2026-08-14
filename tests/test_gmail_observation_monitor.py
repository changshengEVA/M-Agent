from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from m_agent.api.heartbeat import (
    HeartbeatContext,
    HeartbeatMonitorError,
    HeartbeatTarget,
)
from m_agent.integrations.gmail_client import GmailApiClient, GmailClientConfig
from m_agent.integrations.gmail_observation_monitor import (
    GmailObservationMonitorConfig,
    GmailObservationStateStore,
    GmailObservationTriggerMonitor,
)
from m_agent.sdk.stimulus.contracts import IngestResult


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class _RuntimeHost:
    def __init__(self) -> None:
        self.observations = []

    def ingest(self, observation, *, schedule_drainer: bool = True) -> IngestResult:
        self.observations.append((observation, schedule_drainer))
        return IngestResult(
            stimulus_id=f"stim-{len(self.observations)}",
            pool_state="ready",
            created=True,
        )


class _GmailFake:
    def __init__(self) -> None:
        self.profile: Dict[str, Any] = {
            "emailAddress": "alice@example.com",
            "historyId": "100",
        }
        self.profile_responses: list[Dict[str, Any] | BaseException] = []
        self.history_responses: list[Dict[str, Any] | BaseException] = []
        self.messages: Dict[str, Dict[str, Any]] = {}
        self.profile_calls = 0
        self.history_calls = 0
        self.history_requests: list[Dict[str, Any]] = []
        self.message_calls: list[str] = []
        self.message_failures: Dict[str, list[BaseException]] = {}

    def get_profile(self) -> Dict[str, Any]:
        self.profile_calls += 1
        if self.profile_responses:
            response = self.profile_responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return dict(response)
        return dict(self.profile)

    def list_history(self, **kwargs: Any) -> Dict[str, Any]:
        self.history_calls += 1
        self.history_requests.append(dict(kwargs))
        response = self.history_responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def get_message(self, *, message_id: str, **_: Any) -> Dict[str, Any]:
        self.message_calls.append(message_id)
        failures = self.message_failures.get(message_id, [])
        if failures:
            raise failures.pop(0)
        return dict(self.messages[message_id])


def _message(message_id: str, *, subject: str = "Status update") -> Dict[str, Any]:
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "labelIds": ["INBOX", "UNREAD"],
        "internalDate": "1786492860000",
        "snippet": "private body-like preview",
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": "Build Bot <bot@example.com>"},
                {"name": "Date", "value": "Wed, 12 Aug 2026 00:01:00 +0000"},
            ]
        },
    }


def _history(*message_ids: str, history_id: str = "150") -> Dict[str, Any]:
    return {
        "historyId": "200",
        "history": [
            {
                "id": history_id,
                "messagesAdded": [
                    {"message": {"id": message_id, "threadId": f"thread-{message_id}"}}
                    for message_id in message_ids
                ],
            }
        ],
    }


def _target(owner_id: str = "alice") -> tuple[HeartbeatTarget, _RuntimeHost]:
    host = _RuntimeHost()
    runtime = SimpleNamespace(runtime_host=host, default_thread_id="thread-1")
    return HeartbeatTarget(owner_id=owner_id, service_runtime=runtime), host


def _context(*targets: HeartbeatTarget) -> HeartbeatContext:
    return HeartbeatContext(
        beat_started_at="2026-08-12T00:00:00Z",
        targets=tuple(targets),
    )


def _monitor(tmp_path: Path, fake: _GmailFake, clock: _Clock, **config: Any):
    config.setdefault("owner_ids", ("alice",))
    cfg = GmailObservationMonitorConfig(
        state_path=tmp_path / "gmail-state.json",
        poll_interval_seconds=60,
        **config,
    )
    return GmailObservationTriggerMonitor(
        cfg,
        gmail_client_provider=lambda _target: fake,  # type: ignore[arg-type]
        clock=clock,
    )


def test_bootstrap_does_not_replay_and_cursor_commits_only_after_ingest(tmp_path: Path) -> None:
    fake = _GmailFake()
    fake.messages["gmail-message-1"] = _message("gmail-message-1")
    clock = _Clock()
    target, host = _target()
    monitor = _monitor(tmp_path, fake, clock)

    assert tuple(monitor.observe(_context(target))) == ()
    assert monitor.state_store.load("alice")["cursor"] == "100"

    clock.advance(61)
    fake.profile["historyId"] = "200"
    fake.history_responses.append(_history("gmail-message-1"))
    first = tuple(monitor.observe(_context(target)))

    assert len(first) == 1
    assert fake.profile_calls == 1
    before_ingest = monitor.state_store.load("alice")
    assert before_ingest["cursor"] == "100"
    assert before_ingest["staged"] is not None
    assert first[0].observation.payload["message_id"] == "gmail-message-1"
    assert "private body-like preview" not in first[0].observation.to_dict().values()

    # The unacknowledged stage is gated, so a failed ingest cannot replay on
    # every scheduler tick.
    assert tuple(monitor.observe(_context(target))) == ()
    assert fake.history_calls == 1

    clock.advance(61)
    result = monitor.beat(_context(target))

    assert result.admitted == 1
    assert len(host.observations) == 1
    assert (
        host.observations[0][0].idempotency_key
        == first[0].observation.idempotency_key
    )
    committed = monitor.state_store.load("alice")
    assert committed["cursor"] == "150"
    assert committed["staged"] is None


def test_message_budget_carries_same_history_record_across_beats(tmp_path: Path) -> None:
    fake = _GmailFake()
    for message_id in ("m-1", "m-2", "m-3"):
        fake.messages[message_id] = _message(message_id)
    clock = _Clock()
    target, host = _target()
    monitor = _monitor(tmp_path, fake, clock, max_messages_per_beat=2)

    assert tuple(monitor.observe(_context(target))) == ()
    clock.advance(61)
    fake.history_responses.append(_history("m-1", "m-2", "m-3"))

    first = monitor.beat(_context(target))
    assert first.admitted == 2
    assert fake.message_calls == ["m-1", "m-2"]
    assert monitor.state_store.load("alice")["cursor"] == "100"

    clock.advance(61)
    second = monitor.beat(_context(target))
    assert second.admitted == 1
    assert fake.message_calls == ["m-1", "m-2", "m-3"]
    assert monitor.state_store.load("alice")["cursor"] == "150"
    assert len(host.observations) == 3


def test_filtered_chunk_keeps_cursor_until_later_matching_message_is_ingested(
    tmp_path: Path,
) -> None:
    fake = _GmailFake()
    fake.messages["m-1"] = _message("m-1", subject="Newsletter")
    fake.messages["m-2"] = _message("m-2", subject="Receipt")
    fake.messages["m-3"] = _message("m-3", subject="Urgent incident")
    clock = _Clock()
    target, host = _target()
    monitor = _monitor(
        tmp_path,
        fake,
        clock,
        max_messages_per_beat=2,
        subject_keywords=("urgent",),
    )

    assert tuple(monitor.observe(_context(target))) == ()
    clock.advance(61)
    fake.history_responses.append(_history("m-1", "m-2", "m-3"))

    first = monitor.beat(_context(target))
    assert first.observed == 0
    assert monitor.state_store.load("alice")["cursor"] == "100"

    clock.advance(61)
    second = monitor.beat(_context(target))
    assert second.admitted == 1
    assert len(host.observations) == 1
    assert host.observations[0][0].payload["message_id"] == "m-3"
    assert monitor.state_store.load("alice")["cursor"] == "150"


class _NotFound(RuntimeError):
    resp = SimpleNamespace(status=404)


def test_expired_history_cursor_establishes_bounded_current_baseline(
    tmp_path: Path,
) -> None:
    fake = _GmailFake()
    clock = _Clock()
    target, _host = _target()
    monitor = _monitor(tmp_path, fake, clock)

    assert tuple(monitor.observe(_context(target))) == ()
    clock.advance(61)
    fake.profile["historyId"] = "900"
    fake.history_responses.append(_NotFound("stale startHistoryId"))

    assert tuple(monitor.observe(_context(target))) == ()
    checkpoint = monitor.state_store.load("alice")
    assert checkpoint["cursor"] == "900"
    assert checkpoint["resync_count"] == 1
    assert checkpoint["last_resync_reason"] == "history_cursor_expired"
    assert fake.history_calls == 1
    assert fake.profile_calls == 2


def test_owner_filter_prevents_client_or_oauth_resolution(tmp_path: Path) -> None:
    calls = []
    target, _host = _target("bob")
    config = GmailObservationMonitorConfig(
        owner_ids=("alice",),
        state_path=tmp_path / "gmail-state.json",
    )
    monitor = GmailObservationTriggerMonitor(
        config,
        gmail_client_provider=lambda target: calls.append(target) or _GmailFake(),  # type: ignore[arg-type]
    )

    assert tuple(monitor.observe(_context(target))) == ()
    assert calls == []
    assert not config.state_path.exists()


def test_rejects_send_scoped_client_before_gmail_call(tmp_path: Path) -> None:
    fake = _GmailFake()
    fake.config = GmailClientConfig(
        scopes=("https://www.googleapis.com/auth/gmail.send",)
    )
    target, _host = _target()
    monitor = _monitor(tmp_path, fake, _Clock())

    with pytest.raises(ValueError, match="read-only"):
        tuple(monitor.observe(_context(target)))
    assert fake.profile_calls == 0


def test_rejects_interactive_injected_gmail_client(tmp_path: Path) -> None:
    client = GmailApiClient(
        config=GmailClientConfig(
            scopes=("https://www.googleapis.com/auth/gmail.readonly",),
            allow_local_webserver_flow=True,
        ),
        service=object(),
    )
    target, _host = _target()
    config = GmailObservationMonitorConfig(
        owner_ids=("alice",),
        state_path=tmp_path / "gmail-state.json",
    )
    monitor = GmailObservationTriggerMonitor(
        config,
        gmail_client_provider=lambda _target: client,
    )

    with pytest.raises(ValueError, match="non-interactive"):
        tuple(monitor.observe(_context(target)))


def test_default_client_disables_interactive_oauth() -> None:
    root = Path(__file__).resolve().parents[1]
    agent = SimpleNamespace(
        email_agent_config_path=(
            root / "config" / "agents" / "email" / "gmail_email_agent.yaml"
        )
    )
    target = HeartbeatTarget(
        owner_id="alice",
        service_runtime=SimpleNamespace(agent=agent),
    )

    client = GmailObservationTriggerMonitor._build_default_client(target)

    assert client.config.scopes == (
        "https://www.googleapis.com/auth/gmail.readonly",
    )
    assert client.config.allow_local_webserver_flow is False
    assert client.config.allow_console_flow is False


def test_state_store_ignores_stale_acknowledgement(tmp_path: Path) -> None:
    store = GmailObservationStateStore(tmp_path / "gmail-state.json")
    store.save(
        "alice",
        {
            "cursor": "100",
            "staged": {
                "batch_id": "current",
                "commit_history_id": "150",
                "deliveries": [{"delivery_id": "delivery-1"}],
                "acknowledged_delivery_ids": [],
                "remaining_message_refs": [],
            },
        },
    )

    assert store.acknowledge("alice", "stale", "delivery-1") is False
    assert store.load("alice")["cursor"] == "100"


def test_client_failure_uses_retry_backoff(tmp_path: Path) -> None:
    clock = _Clock()
    target, _host = _target()
    calls = []
    config = GmailObservationMonitorConfig(
        owner_ids=("alice",),
        state_path=tmp_path / "gmail-state.json",
        retry_backoff_seconds=120,
    )

    def unavailable(_target: HeartbeatTarget):
        calls.append(_target)
        raise RuntimeError("gmail unavailable")

    monitor = GmailObservationTriggerMonitor(
        config,
        gmail_client_provider=unavailable,
        clock=clock,
    )

    with pytest.raises(HeartbeatMonitorError, match="gmail unavailable"):
        monitor.beat(_context(target))

    checkpoint = monitor.state_store.load("alice")
    assert checkpoint["next_poll_at"] == "2026-08-12T00:02:00Z"
    assert monitor.beat(_context(target)).observed == 0
    assert len(calls) == 1


class _FailingRuntimeHost(_RuntimeHost):
    def __init__(self, *, failures: int = 1) -> None:
        super().__init__()
        self.failures = failures
        self.attempted_idempotency_keys: list[str] = []

    def ingest(self, observation, *, schedule_drainer: bool = True) -> IngestResult:
        self.attempted_idempotency_keys.append(observation.idempotency_key)
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("ingest unavailable")
        return super().ingest(observation, schedule_drainer=schedule_drainer)


def test_failed_ingest_replays_staged_delivery_only_after_poll_gate(
    tmp_path: Path,
) -> None:
    fake = _GmailFake()
    fake.messages["m-1"] = _message("m-1")
    clock = _Clock()
    host = _FailingRuntimeHost()
    runtime = SimpleNamespace(runtime_host=host, default_thread_id="thread-1")
    target = HeartbeatTarget(owner_id="alice", service_runtime=runtime)
    monitor = _monitor(tmp_path, fake, clock)

    assert tuple(monitor.observe(_context(target))) == ()
    clock.advance(61)
    fake.history_responses.append(_history("m-1"))

    with pytest.raises(HeartbeatMonitorError, match="ingest unavailable"):
        monitor.beat(_context(target))

    staged = monitor.state_store.load("alice")
    assert staged["cursor"] == "100"
    assert staged["staged"] is not None
    assert monitor.beat(_context(target)).observed == 0
    assert len(host.attempted_idempotency_keys) == 1

    clock.advance(61)
    replay = monitor.beat(_context(target))

    assert replay.admitted == 1
    assert len(host.attempted_idempotency_keys) == 2
    assert len(set(host.attempted_idempotency_keys)) == 1
    assert monitor.state_store.load("alice")["cursor"] == "150"


def test_staged_metadata_failure_keeps_cursor_and_uses_error_backoff(
    tmp_path: Path,
) -> None:
    fake = _GmailFake()
    fake.messages["m-1"] = _message("m-1")
    fake.messages["m-2"] = _message("m-2")
    fake.message_failures["m-2"] = [RuntimeError("metadata unavailable")]
    clock = _Clock()
    target, host = _target()
    monitor = _monitor(
        tmp_path,
        fake,
        clock,
        max_messages_per_beat=1,
        retry_backoff_seconds=120,
    )

    assert tuple(monitor.observe(_context(target))) == ()
    clock.advance(61)
    fake.history_responses.append(_history("m-1", "m-2"))
    assert monitor.beat(_context(target)).admitted == 1

    clock.advance(61)
    with pytest.raises(HeartbeatMonitorError, match="metadata unavailable"):
        monitor.beat(_context(target))

    failed = monitor.state_store.load("alice")
    assert failed["cursor"] == "100"
    assert failed["staged"] is not None
    assert failed["next_poll_at"] == "2026-08-12T00:04:02Z"
    assert monitor.beat(_context(target)).observed == 0
    assert fake.message_calls == ["m-1", "m-2"]

    clock.advance(121)
    assert monitor.beat(_context(target)).admitted == 1
    assert len(host.observations) == 2
    assert monitor.state_store.load("alice")["cursor"] == "150"


def test_restart_cuts_current_gmail_baseline_and_skips_offline_window(
    tmp_path: Path,
) -> None:
    fake = _GmailFake()
    fake.messages["online-1"] = _message("online-1")
    fake.messages["online-2"] = _message("online-2")
    clock = _Clock()
    target, host = _target()
    first_monitor = _monitor(tmp_path, fake, clock)

    assert tuple(first_monitor.observe(_context(target))) == ()
    clock.advance(61)
    fake.history_responses.append(_history("online-1", history_id="150"))
    assert first_monitor.beat(_context(target)).admitted == 1
    assert first_monitor.state_store.load("alice")["cursor"] == "150"

    # historyId 300 represents mail accumulated while no monitor instance was
    # alive.  A new instance must start from 300, not persisted cursor 150.
    fake.profile["historyId"] = "300"
    restarted = _monitor(tmp_path, fake, clock)
    assert restarted.beat(_context(target)).observed == 0
    restarted_checkpoint = restarted.state_store.load("alice")
    assert restarted_checkpoint["cursor"] == "300"
    assert fake.history_calls == 1

    clock.advance(61)
    fake.history_responses.append(_history("online-2", history_id="350"))
    assert restarted.beat(_context(target)).admitted == 1

    assert fake.history_requests[-1]["start_history_id"] == "300"
    assert [item[0].payload["message_id"] for item in host.observations] == [
        "online-1",
        "online-2",
    ]


def test_restart_baseline_drops_prior_session_staged_batch_and_skips_gap(
    tmp_path: Path,
) -> None:
    fake = _GmailFake()
    fake.messages["pending-1"] = _message("pending-1")
    fake.messages["pending-2"] = _message("pending-2")
    clock = _Clock()
    target, host = _target()
    first_monitor = _monitor(
        tmp_path,
        fake,
        clock,
        max_messages_per_beat=1,
    )

    assert tuple(first_monitor.observe(_context(target))) == ()
    clock.advance(61)
    fake.history_responses.append(
        _history("pending-1", "pending-2", history_id="150")
    )
    first_delivery = tuple(first_monitor.observe(_context(target)))
    assert len(first_delivery) == 1
    assert first_monitor.state_store.load("alice")["staged"] is not None

    fake.profile["historyId"] = "300"
    restarted = _monitor(
        tmp_path,
        fake,
        clock,
        max_messages_per_beat=1,
    )
    assert restarted.beat(_context(target)).observed == 0
    after_baseline = restarted.state_store.load("alice")

    assert after_baseline["cursor"] == "300"
    assert after_baseline["staged"] is None
    assert fake.history_calls == 1

    clock.advance(61)
    fake.history_responses.append({"historyId": "300", "history": []})
    assert restarted.beat(_context(target)).observed == 0

    committed = restarted.state_store.load("alice")
    assert committed["cursor"] == "300"
    assert committed["staged"] is None
    assert host.observations == []


def test_failed_restart_baseline_keeps_but_never_replays_prior_staged_batch(
    tmp_path: Path,
) -> None:
    fake = _GmailFake()
    fake.messages["pending-1"] = _message("pending-1")
    clock = _Clock()
    target, host = _target()
    first_monitor = _monitor(tmp_path, fake, clock)

    assert tuple(first_monitor.observe(_context(target))) == ()
    clock.advance(61)
    fake.history_responses.append(_history("pending-1", history_id="150"))
    assert len(tuple(first_monitor.observe(_context(target)))) == 1
    prior = first_monitor.state_store.load("alice")
    assert prior["staged"] is not None

    fake.profile_responses.extend(
        [
            RuntimeError("profile unavailable"),
            {
                "emailAddress": "alice@example.com",
                "historyId": "300",
            },
        ]
    )
    restarted = _monitor(
        tmp_path,
        fake,
        clock,
        retry_backoff_seconds=120,
    )

    with pytest.raises(HeartbeatMonitorError, match="profile unavailable"):
        restarted.beat(_context(target))
    failed = restarted.state_store.load("alice")
    assert failed["staged"] == prior["staged"]
    assert host.observations == []

    assert restarted.beat(_context(target)).observed == 0
    assert host.observations == []
    clock.advance(121)
    assert restarted.beat(_context(target)).observed == 0

    established = restarted.state_store.load("alice")
    assert established["cursor"] == "300"
    assert established["staged"] is None
    assert host.observations == []
