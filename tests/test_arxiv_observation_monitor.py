from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from m_agent.api.arxiv_heartbeat import (
    ArxivCheckpoint,
    ArxivMonitorConfig,
    ArxivPendingDelivery,
    ArxivRssMonitor,
    ArxivSubscription,
    JsonArxivCheckpointStore,
)
from m_agent.api.heartbeat import (
    HeartbeatContext,
    HeartbeatMonitorError,
    HeartbeatMonitorRegistry,
    HeartbeatTarget,
)
from m_agent.integrations.arxiv_feed import (
    ArxivFeedTooLargeError,
    ArxivFeedResponse,
    ArxivPaper,
    parse_arxiv_feed,
)
from m_agent.sdk.stimulus.contracts import IngestResult, Observation


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class _FeedClientFake:
    def __init__(
        self,
        *responses: object,
        source_identity: str = "https://rss.example.test/atom",
    ) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []
        self.source_identity = source_identity

    def fetch(self, **kwargs: object) -> ArxivFeedResponse:
        self.calls.append(dict(kwargs))
        if not self.responses:
            raise AssertionError("unexpected arXiv feed request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, ArxivFeedResponse)
        return response


class _RuntimeHostFake:
    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[Observation] = []

    def ingest(
        self,
        observation: Observation,
        *,
        schedule_drainer: bool = True,
    ) -> IngestResult:
        del schedule_drainer
        self.calls.append(observation)
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("injected ingest failure")
        return IngestResult(
            stimulus_id=f"stimulus-{len(self.calls)}",
            pool_state="ready",
            created=True,
        )


def _target(host: _RuntimeHostFake, owner_id: str = "alice") -> HeartbeatTarget:
    return HeartbeatTarget(
        owner_id=owner_id,
        service_runtime=SimpleNamespace(
            runtime_host=host,
            default_thread_id=f"{owner_id}-thread",
        ),
    )


def _context(clock: _Clock, *targets: HeartbeatTarget) -> HeartbeatContext:
    return HeartbeatContext(
        beat_started_at=clock.value.isoformat().replace("+00:00", "Z"),
        targets=tuple(targets),
    )


def _subscription(
    *,
    keywords: tuple[str, ...] = ("memory agent",),
) -> ArxivSubscription:
    return ArxivSubscription(
        subscription_id="sub-memory",
        owner_id="alice",
        categories=("cs.AI",),
        keywords=keywords,
        exclude_keywords=("survey",),
        announce_types=("new", "replace"),
    )


def _atom_feed(*entries: str) -> bytes:
    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<feed xmlns='http://www.w3.org/2005/Atom' "
        "xmlns:arxiv='http://arxiv.org/schemas/atom'>"
        + "".join(entries)
        + "</feed>"
    ).encode("utf-8")


def _atom_entry(
    paper_id: str,
    *,
    title: str = "A Memory Agent Architecture",
    summary: str = "A persistent memory agent with deterministic recall.",
    category: str = "cs.AI",
    announce_type: str = "new",
) -> str:
    return (
        "<entry>"
        f"<id>oai:arXiv.org:{paper_id}</id>"
        f"<title>{title}</title>"
        f"<summary>arXiv:{paper_id} Announce Type: {announce_type} "
        f"Abstract: {summary}</summary>"
        "<published>2026-08-12T00:00:00-04:00</published>"
        f"<category term='{category}'/>"
        "<author><name>Alice Researcher</name></author>"
        f"<arxiv:announce_type>{announce_type}</arxiv:announce_type>"
        f"<link rel='alternate' href='https://arxiv.org/abs/{paper_id}'/>"
        "</entry>"
    )


def _monitor(
    tmp_path: Path,
    *,
    client: _FeedClientFake,
    clock: _Clock,
    subscriptions: object = None,
    max_deliveries: int = 20,
    max_pending_per_owner: int = 2_000,
    retry_backoff: int = 120,
    online_baseline_established: bool = True,
) -> tuple[ArxivRssMonitor, JsonArxivCheckpointStore]:
    store = JsonArxivCheckpointStore(tmp_path / "arxiv-checkpoint.json")
    monitor = ArxivRssMonitor(
        subscriptions=(subscriptions if subscriptions is not None else [_subscription()]),
        checkpoint_store=store,
        client=client,
        config=ArxivMonitorConfig(
            poll_interval_seconds=3_600,
            retry_backoff_seconds=retry_backoff,
            max_deliveries_per_beat=max_deliveries,
            max_pending_per_owner=max_pending_per_owner,
        ),
        clock=clock,
    )
    # Most tests below isolate post-startup polling/outbox behavior.  Dedicated
    # startup/restart tests leave this process-local gate at its production
    # default and exercise the online-only baseline explicitly.
    if online_baseline_established:
        monitor._online_baseline_attempted = True
        monitor._online_baseline_established = True
    return monitor, store


def _feed_key() -> str:
    return "arxiv"


def test_subscription_filter_is_category_keyword_exclusion_and_announce_deterministic() -> None:
    subscription = _subscription()
    matching = ArxivPaper(
        base_id="2608.01234",
        version=1,
        title="Ｍｅｍｏｒｙ   Agent for Long Context",
        summary="Uses persistent state.",
        url="https://arxiv.org/abs/2608.01234",
        authors=("Alice",),
        categories=("cs.AI", "cs.LG"),
        announce_type="new",
        announced_at="2026-08-12T04:00:00Z",
    )

    assert subscription.matches(matching) is True
    assert subscription.matches(
        ArxivPaper(**{**matching.__dict__, "categories": ("math.LO",)})
    ) is False
    assert subscription.matches(
        ArxivPaper(**{**matching.__dict__, "summary": "A survey"})
    ) is False
    assert subscription.matches(
        ArxivPaper(**{**matching.__dict__, "announce_type": "cross"})
    ) is False


def test_atom_normalizer_deduplicates_same_base_id_and_version() -> None:
    papers = parse_arxiv_feed(
        _atom_feed(
            _atom_entry("2608.01234v2", category="cs.AI", announce_type="replace"),
            _atom_entry("2608.01234v2", category="cs.LG", announce_type="replace"),
        )
    )

    assert len(papers) == 1
    assert papers[0].base_id == "2608.01234"
    assert papers[0].version == 2
    assert papers[0].revision_id == "2608.01234v2"
    assert papers[0].categories == ("cs.AI", "cs.LG")


def test_ingest_failure_replays_pending_revision_without_fetch_and_acks_outbox(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    client = _FeedClientFake(
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(_atom_entry("2608.01234v1")),
            etag='"feed-v1"',
            last_modified="Wed, 12 Aug 2026 04:00:00 GMT",
        )
    )
    monitor, store = _monitor(tmp_path, client=client, clock=clock)
    host = _RuntimeHostFake(failures=1)
    target = _target(host)

    with pytest.raises(HeartbeatMonitorError, match="injected ingest failure"):
        monitor.beat(_context(clock, target))

    pending = store.load(_feed_key())
    assert pending.etag == '"feed-v1"'
    assert pending.seen_revision_ids == ("2608.01234v1",)
    assert len(pending.pending_deliveries) == 1
    assert (
        pending.pending_deliveries[0].next_attempt_at
        == "2026-08-12T05:02:00Z"
    )

    gated = monitor.beat(_context(clock, target))
    assert gated.observed == 0
    assert len(host.calls) == 1

    clock.value += timedelta(seconds=120)
    result = monitor.beat(_context(clock, target))
    committed = store.load(_feed_key())

    assert result.admitted == 1
    assert len(client.calls) == 1
    assert len(host.calls) == 2
    assert host.calls[0].idempotency_key == host.calls[1].idempotency_key
    assert host.calls[1].payload["event_type"] == "arxiv_paper_matched"
    assert committed.etag == '"feed-v1"'
    assert committed.seen_revision_ids == ("2608.01234v1",)
    assert committed.pending_deliveries == ()


def test_filtered_revisions_commit_validators_and_do_not_replay_after_filter_change(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    response = ArxivFeedResponse(
        status_code=200,
        content=_atom_feed(
            _atom_entry(
                "2608.02222v1",
                title="Unrelated Compiler Optimization",
                summary="A compiler paper.",
            )
        ),
        etag='"filtered"',
        last_modified="Wed, 12 Aug 2026 04:00:00 GMT",
    )
    client = _FeedClientFake(response, response)
    active_subscriptions = [_subscription()]
    monitor, store = _monitor(
        tmp_path,
        client=client,
        clock=clock,
        subscriptions=lambda: tuple(active_subscriptions),
    )
    host = _RuntimeHostFake()
    target = _target(host)

    first = monitor.beat(_context(clock, target))
    filtered = store.load(_feed_key())

    assert first.observed == 0
    assert filtered.etag == '"filtered"'
    assert filtered.seen_revision_ids == ("2608.02222v1",)
    assert filtered.pending_deliveries == ()

    active_subscriptions[:] = [_subscription(keywords=())]
    clock.value += timedelta(hours=1)
    second = monitor.beat(_context(clock, target))

    assert second.observed == 0
    assert len(client.calls) == 2
    assert client.calls[1]["etag"] == '"filtered"'
    assert host.calls == []


def test_fetch_failure_uses_short_retry_checkpoint(tmp_path: Path) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    client = _FeedClientFake(
        RuntimeError("feed unavailable"),
        ArxivFeedResponse(status_code=304, etag='"same"'),
    )
    monitor, store = _monitor(
        tmp_path,
        client=client,
        clock=clock,
        retry_backoff=120,
    )
    target = _target(_RuntimeHostFake())

    with pytest.raises(HeartbeatMonitorError, match="feed unavailable"):
        monitor.beat(_context(clock, target))

    checkpoint = store.load(_feed_key())
    assert checkpoint.next_poll_at == "2026-08-12T05:02:00Z"

    clock.value += timedelta(seconds=119)
    assert monitor.beat(_context(clock, target)).observed == 0
    assert len(client.calls) == 1

    clock.value += timedelta(seconds=1)
    assert monitor.beat(_context(clock, target)).observed == 0
    assert len(client.calls) == 2


def test_delivery_limit_drains_pending_outbox_before_next_source_fetch(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    client = _FeedClientFake(
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(
                _atom_entry("2608.00001v1"),
                _atom_entry("2608.00002v1"),
                _atom_entry("2608.00003v1"),
            ),
            etag='"three"',
        )
    )
    monitor, store = _monitor(
        tmp_path,
        client=client,
        clock=clock,
        max_deliveries=2,
    )
    host = _RuntimeHostFake()
    target = _target(host)

    first = monitor.beat(_context(clock, target))
    after_first = store.load(_feed_key())
    second = monitor.beat(_context(clock, target))
    after_second = store.load(_feed_key())

    assert first.admitted == 2
    assert second.admitted == 1
    assert len(client.calls) == 1
    assert after_first.etag == '"three"'
    assert len(after_first.pending_deliveries) == 1
    assert after_second.etag == '"three"'
    assert after_second.pending_deliveries == ()
    assert after_second.seen_revision_ids == (
        "2608.00001v1",
        "2608.00002v1",
        "2608.00003v1",
    )


def test_feed_plan_is_stable_when_subscribed_owner_target_is_unavailable(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    client = _FeedClientFake(ArxivFeedResponse(status_code=304))
    monitor, _store = _monitor(tmp_path, client=client, clock=clock)

    result = monitor.beat(_context(clock))

    assert result.observed == 0
    assert client.calls[0]["categories"] == ("cs.AI",)


def test_removed_subscription_owner_drops_pending_outbox(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    client = _FeedClientFake(
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(_atom_entry("2608.09999v1")),
            etag='"removed-owner"',
        )
    )
    active_subscriptions = [_subscription()]
    monitor, store = _monitor(
        tmp_path,
        client=client,
        clock=clock,
        subscriptions=lambda: tuple(active_subscriptions),
    )
    target = _target(_RuntimeHostFake(failures=1))

    with pytest.raises(HeartbeatMonitorError):
        monitor.beat(_context(clock, target))
    assert len(store.load(_feed_key()).pending_deliveries) == 1

    active_subscriptions.clear()
    result = monitor.beat(_context(clock))

    assert result.observed == 0
    assert store.load(_feed_key()).pending_deliveries == ()


def test_static_subscription_generator_is_materialized_once(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    client = _FeedClientFake(
        ArxivFeedResponse(status_code=304),
        ArxivFeedResponse(status_code=304),
    )
    subscriptions = (item for item in (_subscription(),))
    monitor, _store = _monitor(
        tmp_path,
        client=client,
        clock=clock,
        subscriptions=subscriptions,
    )

    monitor.beat(_context(clock))
    clock.value += timedelta(hours=1)
    monitor.beat(_context(clock))

    assert [call["categories"] for call in client.calls] == [
        ("cs.AI",),
        ("cs.AI",),
    ]


def test_feed_fingerprint_scopes_endpoint_categories_and_schema() -> None:
    first = ArxivRssMonitor.feed_key(
        ("cs.LG", "cs.AI"),
        endpoint="https://rss.example.test/atom/",
    )

    assert first == ArxivRssMonitor.feed_key(
        ("cs.AI", "cs.LG"),
        endpoint="https://rss.example.test/atom",
    )
    assert first != ArxivRssMonitor.feed_key(
        ("cs.AI", "cs.LG"),
        endpoint="https://mirror.example.test/atom",
    )
    assert first != ArxivRssMonitor.feed_key(
        ("cs.AI",),
        endpoint="https://rss.example.test/atom",
    )


def test_provider_cap_is_fail_closed_and_independent_of_local_item_limit() -> None:
    exactly_provider_cap = _atom_feed(
        *(
            _atom_entry(f"2608.{index:05d}v1")
            for index in range(2_000)
        )
    )

    with pytest.raises(ArxivFeedTooLargeError, match="provider cap"):
        parse_arxiv_feed(exactly_provider_cap)

    two_items = _atom_feed(
        _atom_entry("2608.10001v1"),
        _atom_entry("2608.10002v1"),
    )
    assert len(parse_arxiv_feed(two_items, max_items=2)) == 2
    with pytest.raises(ArxivFeedTooLargeError, match="limit is 1"):
        parse_arxiv_feed(two_items, max_items=1)


def test_missing_target_does_not_freeze_source_frontier_or_future_fetches(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    client = _FeedClientFake(
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(_atom_entry("2608.20001v1")),
            etag='"one"',
        ),
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(
                _atom_entry("2608.20001v1"),
                _atom_entry("2608.20002v1"),
            ),
            etag='"two"',
        ),
    )
    monitor, store = _monitor(tmp_path, client=client, clock=clock)

    assert monitor.beat(_context(clock)).observed == 0
    first = store.load(_feed_key())
    assert first.etag == '"one"'
    assert len(first.pending_deliveries) == 1

    clock.value += timedelta(hours=1)
    assert monitor.beat(_context(clock)).observed == 0
    second = store.load(_feed_key())
    assert second.etag == '"two"'
    assert second.seen_revision_ids == (
        "2608.20001v1",
        "2608.20002v1",
    )
    assert len(second.pending_deliveries) == 2
    assert len(client.calls) == 2

    host = _RuntimeHostFake()
    result = monitor.beat(_context(clock, _target(host)))

    assert result.admitted == 2
    assert len(client.calls) == 2
    assert store.load(_feed_key()).pending_deliveries == ()


def test_plan_change_after_restart_preserves_and_drains_old_owner_outbox(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    first_client = _FeedClientFake(
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(_atom_entry("2608.30001v1")),
            etag='"old-plan"',
        ),
        source_identity="https://first.example.test/atom",
    )
    first_monitor, store = _monitor(
        tmp_path,
        client=first_client,
        clock=clock,
    )
    first_monitor.beat(_context(clock))
    old_checkpoint = store.load(_feed_key())
    assert len(old_checkpoint.pending_deliveries) == 1

    changed_subscription = ArxivSubscription(
        subscription_id="sub-new-plan",
        owner_id="alice",
        categories=("cs.LG",),
        keywords=("memory agent",),
    )
    second_client = _FeedClientFake(
        ArxivFeedResponse(status_code=304),
        source_identity="https://second.example.test/atom",
    )
    second_monitor, _same_store = _monitor(
        tmp_path,
        client=second_client,
        clock=clock,
        subscriptions=(changed_subscription,),
    )
    host = _RuntimeHostFake()

    result = second_monitor.beat(_context(clock, _target(host)))
    changed_checkpoint = store.load(_feed_key())

    assert result.admitted == 1
    assert second_client.calls[0]["categories"] == ("cs.LG",)
    assert second_client.calls[0]["etag"] == ""
    assert changed_checkpoint.pending_deliveries == ()
    assert changed_checkpoint.plan_fingerprint != old_checkpoint.plan_fingerprint


def test_pending_delivery_limit_round_robins_owners_after_first_owner_fails(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    client = _FeedClientFake(
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(
                _atom_entry(
                    "2608.40001v1",
                    title="Alpha Memory Agent One",
                ),
                _atom_entry(
                    "2608.40002v1",
                    title="Alpha Memory Agent Two",
                ),
                _atom_entry(
                    "2608.40003v1",
                    title="Beta Memory Agent",
                ),
            ),
        )
    )
    subscriptions = (
        ArxivSubscription(
            subscription_id="sub-alpha",
            owner_id="alice",
            categories=("cs.AI",),
            keywords=("alpha",),
        ),
        ArxivSubscription(
            subscription_id="sub-beta",
            owner_id="bob",
            categories=("cs.AI",),
            keywords=("beta",),
        ),
    )
    monitor, store = _monitor(
        tmp_path,
        client=client,
        clock=clock,
        subscriptions=subscriptions,
        max_deliveries=2,
    )
    alice_host = _RuntimeHostFake(failures=1)
    bob_host = _RuntimeHostFake()

    with pytest.raises(HeartbeatMonitorError) as error:
        monitor.beat(
            _context(
                clock,
                _target(alice_host, "alice"),
                _target(bob_host, "bob"),
            )
        )

    assert error.value.result.observed == 2
    assert error.value.result.failed == 1
    assert error.value.result.admitted == 1
    assert len(alice_host.calls) == 1
    assert len(bob_host.calls) == 1
    remaining = store.load(_feed_key()).pending_deliveries
    assert [item.owner_id for item in remaining] == ["alice", "alice"]


def test_pending_retry_gate_does_not_block_due_source_poll(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    client = _FeedClientFake(
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(_atom_entry("2608.50001v1")),
            etag='"first"',
        ),
        ArxivFeedResponse(status_code=304, etag='"first"'),
    )
    monitor, store = _monitor(
        tmp_path,
        client=client,
        clock=clock,
        retry_backoff=7_200,
    )
    host = _RuntimeHostFake(failures=1)
    target = _target(host)

    with pytest.raises(HeartbeatMonitorError, match="injected ingest failure"):
        monitor.beat(_context(clock, target))
    first = store.load(_feed_key())
    assert first.next_poll_at == "2026-08-12T06:00:00Z"
    assert (
        first.pending_deliveries[0].next_attempt_at
        == "2026-08-12T07:00:00Z"
    )

    clock.value += timedelta(hours=1)
    result = monitor.beat(_context(clock, target))

    assert result.observed == 0
    assert len(client.calls) == 2
    assert len(host.calls) == 1
    assert len(store.load(_feed_key()).pending_deliveries) == 1


def test_round_robin_cursor_survives_restart_and_prevents_owner_starvation(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    client = _FeedClientFake(
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(
                _atom_entry("2608.51001v1", title="Alpha Memory Agent"),
                _atom_entry("2608.51002v1", title="Beta Memory Agent"),
            ),
        )
    )
    subscriptions = (
        ArxivSubscription(
            subscription_id="alpha",
            owner_id="alice",
            categories=("cs.AI",),
            keywords=("alpha",),
        ),
        ArxivSubscription(
            subscription_id="beta",
            owner_id="bob",
            categories=("cs.AI",),
            keywords=("beta",),
        ),
    )
    monitor, store = _monitor(
        tmp_path,
        client=client,
        clock=clock,
        subscriptions=subscriptions,
        max_deliveries=1,
        retry_backoff=120,
    )
    alice_host = _RuntimeHostFake(failures=99)
    bob_host = _RuntimeHostFake()
    targets = (
        _target(alice_host, "alice"),
        _target(bob_host, "bob"),
    )

    with pytest.raises(HeartbeatMonitorError):
        monitor.beat(_context(clock, *targets))
    after_alice = store.load(_feed_key())
    assert after_alice.rr_last_owner_id == "alice"
    assert len(after_alice.pending_deliveries) == 2

    clock.value += timedelta(seconds=120)
    restarted, restarted_store = _monitor(
        tmp_path,
        client=client,
        clock=clock,
        subscriptions=subscriptions,
        max_deliveries=1,
        retry_backoff=120,
    )
    second = restarted.beat(_context(clock, *targets))
    after_bob = restarted_store.load(_feed_key())

    assert second.admitted == 1
    assert len(alice_host.calls) == 1
    assert len(bob_host.calls) == 1
    assert after_bob.rr_last_owner_id == "bob"
    assert [item.owner_id for item in after_bob.pending_deliveries] == [
        "alice"
    ]
    assert len(client.calls) == 1


def test_owner_outbox_overflow_is_bounded_persistent_and_health_visible(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    client = _FeedClientFake(
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(
                _atom_entry("2608.52001v1", title="Alpha Memory Agent One"),
                _atom_entry("2608.52002v1", title="Alpha Memory Agent Two"),
                _atom_entry("2608.52003v1", title="Beta Memory Agent"),
            ),
            etag='"overflow"',
        ),
        ArxivFeedResponse(status_code=304, etag='"overflow"'),
    )
    subscriptions = (
        ArxivSubscription(
            subscription_id="alpha",
            owner_id="alice",
            categories=("cs.AI",),
            keywords=("alpha",),
        ),
        ArxivSubscription(
            subscription_id="beta",
            owner_id="bob",
            categories=("cs.AI",),
            keywords=("beta",),
        ),
    )
    monitor, store = _monitor(
        tmp_path,
        client=client,
        clock=clock,
        subscriptions=subscriptions,
        max_deliveries=1,
        max_pending_per_owner=1,
    )
    bob_host = _RuntimeHostFake()
    registry = HeartbeatMonitorRegistry([monitor])

    with caplog.at_level("ERROR"):
        report = registry.beat(
            _context(clock, _target(bob_host, "bob"))
        )
    checkpoint = store.load(_feed_key())
    health = registry.health_payload()["monitors"]["arxiv"]

    assert report["failed"] == 1
    assert report["results"]["arxiv"]["admitted"] == 1
    assert report["results"]["arxiv"]["failed"] == 1
    assert "outbox capacity exceeded" in report["errors"]["arxiv"]
    assert health["status"] == "degraded"
    assert "outbox capacity exceeded" in health["last_error"]
    assert checkpoint.etag == '"overflow"'
    assert checkpoint.seen_revision_ids == (
        "2608.52001v1",
        "2608.52002v1",
        "2608.52003v1",
    )
    assert [item.owner_id for item in checkpoint.pending_deliveries] == [
        "alice"
    ]
    assert len(checkpoint.outbox_overflows) == 1
    overflow = checkpoint.outbox_overflows[0]
    assert overflow.owner_id == "alice"
    assert overflow.dropped_count == 1
    assert overflow.latest_dropped_revision_ids == ("2608.52002v1",)
    assert "owner_id=alice dropped=1" in caplog.text

    clock.value += timedelta(hours=1)
    healthy = registry.beat(_context(clock))
    assert healthy["failed"] == 0
    assert len(client.calls) == 2
    assert store.load(_feed_key()).outbox_overflows == (overflow,)


def test_lowering_outbox_cap_preserves_existing_durable_work(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    client = _FeedClientFake(
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(
                _atom_entry("2608.53003v1", title="Memory Agent Three")
            ),
        )
    )
    subscription = _subscription()
    monitor, store = _monitor(
        tmp_path,
        client=client,
        clock=clock,
        subscriptions=(subscription,),
        max_deliveries=1,
        max_pending_per_owner=1,
    )
    existing = tuple(
        ArxivPendingDelivery(
            delivery_id=f"alice:2608.5300{index}v1",
            owner_id="alice",
            matched_subscription_ids=(subscription.subscription_id,),
            paper=ArxivPaper(
                base_id=f"2608.5300{index}",
                version=1,
                title=f"Memory Agent {index}",
                summary="memory agent research",
                url=f"https://arxiv.org/abs/2608.5300{index}",
                authors=("Alice Researcher",),
                categories=("cs.AI",),
                announce_type="new",
                announced_at="2026-08-12T04:00:00Z",
            ),
        )
        for index in (1, 2)
    )
    store.save(
        _feed_key(),
        ArxivCheckpoint(
            plan_fingerprint=monitor.feed_key(
                ("cs.AI",), endpoint=client.source_identity
            ),
            pending_deliveries=existing,
        ),
    )

    with pytest.raises(HeartbeatMonitorError, match="outbox capacity exceeded"):
        monitor.beat(_context(clock))

    checkpoint = store.load(_feed_key())
    assert checkpoint.pending_deliveries == existing
    assert checkpoint.outbox_overflows[0].latest_dropped_revision_ids == (
        "2608.53003v1",
    )


def test_source_failure_reports_health_after_draining_due_pending(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    client = _FeedClientFake(
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(_atom_entry("2608.53001v1")),
            etag='"seed"',
        ),
        RuntimeError("source unavailable"),
    )
    monitor, store = _monitor(tmp_path, client=client, clock=clock)

    assert monitor.beat(_context(clock)).observed == 0
    assert len(store.load(_feed_key()).pending_deliveries) == 1

    clock.value += timedelta(hours=1)
    host = _RuntimeHostFake()
    with pytest.raises(HeartbeatMonitorError, match="source unavailable") as error:
        monitor.beat(_context(clock, _target(host)))

    assert error.value.result.observed == 1
    assert error.value.result.admitted == 1
    assert error.value.result.failed == 1
    assert len(host.calls) == 1
    checkpoint = store.load(_feed_key())
    assert checkpoint.pending_deliveries == ()
    assert checkpoint.next_poll_at == "2026-08-12T06:02:00Z"


def test_seen_revision_limit_must_cover_one_complete_feed() -> None:
    with pytest.raises(ValueError, match="seen_revision_limit"):
        ArxivMonitorConfig(
            max_feed_items=500,
            seen_revision_limit=499,
        )

    assert ArxivMonitorConfig(
        max_feed_items=500,
        seen_revision_limit=500,
    ).seen_revision_limit == 500
    assert ArxivMonitorConfig(
        max_feed_items=5_000,
        seen_revision_limit=2_000,
    ).max_feed_items == 2_000


def test_new_monitor_baselines_current_feed_then_delivers_online_increment(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    baseline_entry = _atom_entry("2608.60001v1")
    online_entry = _atom_entry("2608.60002v1")
    client = _FeedClientFake(
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(baseline_entry),
            etag='"baseline"',
        ),
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(baseline_entry, online_entry),
            etag='"online"',
        ),
    )
    monitor, store = _monitor(
        tmp_path,
        client=client,
        clock=clock,
        online_baseline_established=False,
    )
    host = _RuntimeHostFake()
    target = _target(host)

    first = monitor.beat(_context(clock, target))
    baseline = store.load(_feed_key())

    assert first.observed == 0
    assert host.calls == []
    assert baseline.etag == '"baseline"'
    assert baseline.seen_revision_ids == ("2608.60001v1",)
    assert baseline.pending_deliveries == ()

    clock.value += timedelta(hours=1)
    second = monitor.beat(_context(clock, target))

    assert second.admitted == 1
    assert len(host.calls) == 1
    assert host.calls[0].payload["paper"]["revision_id"] == "2608.60002v1"


def test_restart_drops_prior_pending_and_offline_revisions_at_new_baseline(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    subscription = _subscription()
    prior_paper = ArxivPaper(
        base_id="2608.61001",
        version=1,
        title="Prior Memory Agent",
        summary="memory agent research",
        url="https://arxiv.org/abs/2608.61001v1",
        authors=("Alice Researcher",),
        categories=("cs.AI",),
        announce_type="new",
        announced_at="2026-08-12T03:00:00Z",
    )
    prior_pending = ArxivPendingDelivery(
        delivery_id="alice:2608.61001v1",
        owner_id="alice",
        matched_subscription_ids=(subscription.subscription_id,),
        paper=prior_paper,
        batch_id="prior-session",
    )
    baseline_entries = (
        _atom_entry("2608.61001v1"),
        _atom_entry("2608.61002v1"),
    )
    online_entry = _atom_entry("2608.61003v1")
    client = _FeedClientFake(
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(*baseline_entries),
            etag='"restarted"',
        ),
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(*baseline_entries, online_entry),
            etag='"online"',
        ),
    )
    monitor, store = _monitor(
        tmp_path,
        client=client,
        clock=clock,
        subscriptions=(subscription,),
        online_baseline_established=False,
    )
    store.save(
        _feed_key(),
        ArxivCheckpoint(
            plan_fingerprint=monitor.feed_key(
                ("cs.AI",), endpoint=client.source_identity
            ),
            etag='"prior"',
            seen_revision_ids=("2608.60000v1", "2608.61001v1"),
            pending_deliveries=(prior_pending,),
        ),
    )
    host = _RuntimeHostFake()
    target = _target(host)

    baseline_result = monitor.beat(_context(clock, target))
    restarted = store.load(_feed_key())

    assert baseline_result.observed == 0
    assert host.calls == []
    assert client.calls[0]["etag"] == ""
    assert client.calls[0]["last_modified"] == ""
    assert restarted.pending_deliveries == ()
    assert restarted.seen_revision_ids == (
        "2608.61001v1",
        "2608.61002v1",
    )

    clock.value += timedelta(hours=1)
    online_result = monitor.beat(_context(clock, target))

    assert online_result.admitted == 1
    assert len(host.calls) == 1
    assert host.calls[0].payload["paper"]["revision_id"] == "2608.61003v1"


def test_failed_restart_baseline_neither_drops_nor_delivers_prior_pending(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 12, 5, tzinfo=timezone.utc))
    subscription = _subscription()
    pending = ArxivPendingDelivery(
        delivery_id="alice:2608.62001v1",
        owner_id="alice",
        matched_subscription_ids=(subscription.subscription_id,),
        paper=ArxivPaper(
            base_id="2608.62001",
            version=1,
            title="Prior Memory Agent",
            summary="memory agent research",
            url="https://arxiv.org/abs/2608.62001v1",
            authors=("Alice Researcher",),
            categories=("cs.AI",),
            announce_type="new",
            announced_at="2026-08-12T03:00:00Z",
        ),
    )
    client = _FeedClientFake(
        RuntimeError("baseline unavailable"),
        ArxivFeedResponse(
            status_code=200,
            content=_atom_feed(_atom_entry("2608.62002v1")),
            etag='"current"',
        ),
    )
    monitor, store = _monitor(
        tmp_path,
        client=client,
        clock=clock,
        subscriptions=(subscription,),
        online_baseline_established=False,
    )
    store.save(
        _feed_key(),
        ArxivCheckpoint(
            plan_fingerprint=monitor.feed_key(
                ("cs.AI",), endpoint=client.source_identity
            ),
            pending_deliveries=(pending,),
        ),
    )
    host = _RuntimeHostFake()
    target = _target(host)

    with pytest.raises(HeartbeatMonitorError, match="baseline unavailable"):
        monitor.beat(_context(clock, target))
    failed = store.load(_feed_key())
    assert failed.pending_deliveries == (pending,)
    assert host.calls == []

    clock.value += timedelta(seconds=120)
    assert monitor.beat(_context(clock, target)).observed == 0
    assert store.load(_feed_key()).pending_deliveries == ()
    assert host.calls == []
