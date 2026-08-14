"""Heartbeat monitor for deterministic arXiv RSS/Atom subscriptions.

The module owns only the path from a polled source through
``ObservationTriggerDelivery``.  Runtime attribution and consumption remain
outside this adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import unicodedata
from typing import (
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Union,
)

from m_agent.integrations.arxiv_feed import (
    ArxivFeedClient,
    ArxivFeedError,
    ArxivFeedResponse,
    ArxivPaper,
    UrllibArxivFeedClient,
    parse_arxiv_feed,
)
from m_agent.sdk.stimulus.contracts import IngestResult

from .heartbeat import (
    HeartbeatContext,
    HeartbeatTarget,
    ObservationTriggerDelivery,
    ObservationTriggerMonitor,
)


_CHECKPOINT_VERSION = 3
_PLAN_SCHEMA_VERSION = 2
_STORE_KEY = "arxiv"


logger = logging.getLogger(__name__)


class ArxivOutboxOverflowError(RuntimeError):
    """Raised after due deliveries when a bounded owner outbox overflowed."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_utc(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalized_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(normalized.casefold().split())


def _normalized_categories(values: Iterable[str]) -> Tuple[str, ...]:
    by_key: Dict[str, str] = {}
    for raw in values:
        value = str(raw or "").strip()
        if value:
            by_key.setdefault(value.casefold(), value)
    return tuple(by_key[key] for key in sorted(by_key))


@dataclass(frozen=True)
class ArxivSubscription:
    """One owner-scoped, deterministic arXiv feed filter."""

    subscription_id: str
    owner_id: str
    categories: Tuple[str, ...]
    keywords: Tuple[str, ...] = ()
    exclude_keywords: Tuple[str, ...] = ()
    announce_types: Tuple[str, ...] = ("new", "replace", "replace-cross")
    enabled: bool = True

    def __post_init__(self) -> None:
        subscription_id = str(self.subscription_id or "").strip()
        owner_id = str(self.owner_id or "").strip()
        categories = _normalized_categories(self.categories)
        keywords = tuple(
            dict.fromkeys(
                item
                for item in (
                    _normalized_match_text(value) for value in self.keywords
                )
                if item
            )
        )
        exclude_keywords = tuple(
            dict.fromkeys(
                item
                for item in (
                    _normalized_match_text(value)
                    for value in self.exclude_keywords
                )
                if item
            )
        )
        announce_types = tuple(
            dict.fromkeys(
                str(value or "").strip().casefold()
                for value in self.announce_types
                if str(value or "").strip()
            )
        )
        if not subscription_id:
            raise ValueError("subscription_id must be non-empty")
        if not owner_id:
            raise ValueError("owner_id must be non-empty")
        if not categories:
            raise ValueError("an arXiv subscription requires categories")
        object.__setattr__(self, "subscription_id", subscription_id)
        object.__setattr__(self, "owner_id", owner_id)
        object.__setattr__(self, "categories", categories)
        object.__setattr__(self, "keywords", keywords)
        object.__setattr__(self, "exclude_keywords", exclude_keywords)
        object.__setattr__(self, "announce_types", announce_types)

    def matches(self, paper: ArxivPaper) -> bool:
        """Apply exact categories and normalized substring keyword rules."""

        paper_categories = {item.casefold() for item in paper.categories}
        if not paper_categories.intersection(
            item.casefold() for item in self.categories
        ):
            return False
        announce_type = str(paper.announce_type or "new").strip().casefold()
        if self.announce_types and announce_type not in self.announce_types:
            return False
        haystack = _normalized_match_text(
            "\n".join((paper.title, paper.summary))
        )
        if any(keyword in haystack for keyword in self.exclude_keywords):
            return False
        if self.keywords and not any(
            keyword in haystack for keyword in self.keywords
        ):
            return False
        return True


@dataclass(frozen=True)
class ArxivMonitorConfig:
    """Bounds for one synchronous Heartbeat source adapter."""

    poll_interval_seconds: int = 21_600
    retry_backoff_seconds: int = 900
    request_timeout_seconds: float = 5.0
    max_response_bytes: int = 4_000_000
    max_feed_items: int = 2_000
    max_deliveries_per_beat: int = 20
    max_pending_per_owner: int = 2_000
    seen_revision_limit: int = 10_000
    schedule_drainer: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "poll_interval_seconds",
            max(60, int(self.poll_interval_seconds or 21_600)),
        )
        object.__setattr__(
            self,
            "retry_backoff_seconds",
            max(30, int(self.retry_backoff_seconds or 900)),
        )
        object.__setattr__(
            self,
            "request_timeout_seconds",
            max(0.1, min(30.0, float(self.request_timeout_seconds or 5.0))),
        )
        object.__setattr__(
            self,
            "max_response_bytes",
            max(1_024, int(self.max_response_bytes or 4_000_000)),
        )
        object.__setattr__(
            self,
            "max_feed_items",
            max(1, min(2_000, int(self.max_feed_items or 2_000))),
        )
        object.__setattr__(
            self,
            "max_deliveries_per_beat",
            max(1, min(200, int(self.max_deliveries_per_beat or 20))),
        )
        object.__setattr__(
            self,
            "max_pending_per_owner",
            max(1, min(100_000, int(self.max_pending_per_owner or 2_000))),
        )
        object.__setattr__(
            self,
            "seen_revision_limit",
            max(100, int(self.seen_revision_limit or 10_000)),
        )
        if self.seen_revision_limit < self.max_feed_items:
            raise ValueError(
                "seen_revision_limit must be greater than or equal to "
                "max_feed_items"
            )
        if self.max_pending_per_owner < self.max_deliveries_per_beat:
            raise ValueError(
                "max_pending_per_owner must be greater than or equal to "
                "max_deliveries_per_beat"
            )


@dataclass(frozen=True)
class ArxivPendingDelivery:
    """Durable owner outbox entry, independent of the source frontier."""

    delivery_id: str
    owner_id: str
    matched_subscription_ids: Tuple[str, ...]
    paper: ArxivPaper
    batch_id: str = ""
    next_attempt_at: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "delivery_id": self.delivery_id,
            "owner_id": self.owner_id,
            "matched_subscription_ids": list(self.matched_subscription_ids),
            "paper": self.paper.to_dict(),
            "batch_id": self.batch_id,
            "next_attempt_at": self.next_attempt_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ArxivPendingDelivery":
        raw_paper = payload.get("paper")
        if not isinstance(raw_paper, Mapping):
            raw_paper = {}
        return cls(
            delivery_id=str(payload.get("delivery_id", "") or "").strip(),
            owner_id=str(payload.get("owner_id", "") or "").strip(),
            matched_subscription_ids=tuple(
                str(item or "").strip()
                for item in _sequence(payload.get("matched_subscription_ids"))
                if str(item or "").strip()
            ),
            paper=ArxivPaper.from_dict(dict(raw_paper)),
            batch_id=str(payload.get("batch_id", "") or "").strip(),
            next_attempt_at=str(
                payload.get("next_attempt_at", "") or ""
            ).strip(),
        )


@dataclass(frozen=True)
class ArxivOutboxOverflow:
    """Persistent evidence that an owner's bounded outbox dropped work."""

    owner_id: str
    dropped_count: int
    last_overflow_at: str
    latest_dropped_revision_ids: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "owner_id": self.owner_id,
            "dropped_count": self.dropped_count,
            "last_overflow_at": self.last_overflow_at,
            "latest_dropped_revision_ids": list(
                self.latest_dropped_revision_ids
            ),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ArxivOutboxOverflow":
        try:
            dropped_count = max(0, int(payload.get("dropped_count", 0) or 0))
        except (TypeError, ValueError):
            dropped_count = 0
        return cls(
            owner_id=str(payload.get("owner_id", "") or "").strip(),
            dropped_count=dropped_count,
            last_overflow_at=str(
                payload.get("last_overflow_at", "") or ""
            ).strip(),
            latest_dropped_revision_ids=tuple(
                str(item or "").strip()
                for item in _sequence(
                    payload.get("latest_dropped_revision_ids")
                )
                if str(item or "").strip()
            ),
        )


@dataclass(frozen=True)
class ArxivCheckpoint:
    """One stable source frontier plus owner-scoped durable outboxes."""

    plan_fingerprint: str = ""
    etag: str = ""
    last_modified: str = ""
    seen_revision_ids: Tuple[str, ...] = ()
    next_poll_at: str = ""
    pending_deliveries: Tuple[ArxivPendingDelivery, ...] = ()
    rr_last_owner_id: str = ""
    outbox_overflows: Tuple[ArxivOutboxOverflow, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": _CHECKPOINT_VERSION,
            "plan_fingerprint": self.plan_fingerprint,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "seen_revision_ids": list(self.seen_revision_ids),
            "next_poll_at": self.next_poll_at,
            "pending_deliveries": [
                item.to_dict() for item in self.pending_deliveries
            ],
            "rr_last_owner_id": self.rr_last_owner_id,
            "outbox_overflows": [
                item.to_dict() for item in self.outbox_overflows
            ],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ArxivCheckpoint":
        return cls(
            plan_fingerprint=str(
                payload.get("plan_fingerprint", "") or ""
            ).strip(),
            etag=str(payload.get("etag", "") or "").strip(),
            last_modified=str(
                payload.get("last_modified", "") or ""
            ).strip(),
            seen_revision_ids=tuple(
                str(item or "").strip()
                for item in _sequence(payload.get("seen_revision_ids"))
                if str(item or "").strip()
            ),
            next_poll_at=str(payload.get("next_poll_at", "") or "").strip(),
            pending_deliveries=tuple(
                ArxivPendingDelivery.from_dict(item)
                for item in _sequence(payload.get("pending_deliveries"))
                if isinstance(item, Mapping)
            ),
            rr_last_owner_id=str(
                payload.get("rr_last_owner_id", "") or ""
            ).strip(),
            outbox_overflows=tuple(
                ArxivOutboxOverflow.from_dict(item)
                for item in _sequence(payload.get("outbox_overflows"))
                if isinstance(item, Mapping)
            ),
        )


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return value
    return ()


class ArxivCheckpointStore(Protocol):
    """Durable source-frontier/outbox boundary."""

    def load(self, feed_key: str) -> ArxivCheckpoint:
        """Load the source frontier and pending owner outboxes."""

    def save(self, feed_key: str, checkpoint: ArxivCheckpoint) -> None:
        """Persist scheduling, frontier, and pending outbox state."""

    def acknowledge(
        self,
        feed_key: str,
        *,
        delivery_id: str,
    ) -> bool:
        """Atomically remove one successfully ingested owner outbox item."""


class JsonArxivCheckpointStore:
    """Thread-safe JSON checkpoint store with replace-on-write durability."""

    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self, feed_key: str) -> ArxivCheckpoint:
        key = str(feed_key or "").strip()
        if not key:
            raise ValueError("feed_key must be non-empty")
        with self._lock:
            root = self._read_root()
            payload = root.get("feeds", {}).get(key, {})
            return (
                ArxivCheckpoint.from_dict(payload)
                if isinstance(payload, Mapping)
                else ArxivCheckpoint()
            )

    def save(self, feed_key: str, checkpoint: ArxivCheckpoint) -> None:
        key = str(feed_key or "").strip()
        if not key:
            raise ValueError("feed_key must be non-empty")
        if not isinstance(checkpoint, ArxivCheckpoint):
            raise TypeError("checkpoint must be an ArxivCheckpoint")
        with self._lock:
            root = self._read_root()
            feeds = root.setdefault("feeds", {})
            if not isinstance(feeds, dict):
                feeds = {}
                root["feeds"] = feeds
            feeds[key] = checkpoint.to_dict()
            self._write_root(root)

    def acknowledge(
        self,
        feed_key: str,
        *,
        delivery_id: str,
    ) -> bool:
        key = str(feed_key or "").strip()
        expected_delivery_id = str(delivery_id or "").strip()
        if not key or not expected_delivery_id:
            raise ValueError("feed_key and delivery_id must be non-empty")
        with self._lock:
            root = self._read_root()
            feeds = root.setdefault("feeds", {})
            if not isinstance(feeds, dict):
                raise RuntimeError("arXiv checkpoint feeds payload is invalid")
            raw_checkpoint = feeds.get(key, {})
            checkpoint = (
                ArxivCheckpoint.from_dict(raw_checkpoint)
                if isinstance(raw_checkpoint, Mapping)
                else ArxivCheckpoint()
            )
            known_ids = {
                item.delivery_id for item in checkpoint.pending_deliveries
            }
            if expected_delivery_id not in known_ids:
                return False
            committed = ArxivCheckpoint(
                plan_fingerprint=checkpoint.plan_fingerprint,
                etag=checkpoint.etag,
                last_modified=checkpoint.last_modified,
                seen_revision_ids=checkpoint.seen_revision_ids,
                next_poll_at=checkpoint.next_poll_at,
                pending_deliveries=tuple(
                    item
                    for item in checkpoint.pending_deliveries
                    if item.delivery_id != expected_delivery_id
                ),
                rr_last_owner_id=checkpoint.rr_last_owner_id,
                outbox_overflows=checkpoint.outbox_overflows,
            )
            feeds[key] = committed.to_dict()
            self._write_root(root)
            return True

    def _read_root(self) -> Dict[str, object]:
        if not self.path.exists():
            return {"version": _CHECKPOINT_VERSION, "feeds": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("failed to read arXiv checkpoint store") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("arXiv checkpoint root must be an object")
        return payload

    def _write_root(self, payload: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        try:
            temp_path.write_text(encoded, encoding="utf-8")
            os.replace(temp_path, self.path)
        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass


SubscriptionSource = Union[
    Iterable[ArxivSubscription],
    Callable[[], Iterable[ArxivSubscription]],
]


class ArxivRssMonitor(ObservationTriggerMonitor):
    """Poll arXiv once per interval and deliver owner-scoped paper matches."""

    def __init__(
        self,
        *,
        subscriptions: SubscriptionSource,
        checkpoint_store: ArxivCheckpointStore,
        client: Optional[ArxivFeedClient] = None,
        config: Optional[ArxivMonitorConfig] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        super().__init__("arxiv")
        if not callable(getattr(checkpoint_store, "load", None)):
            raise TypeError("checkpoint_store.load must be callable")
        if not callable(getattr(checkpoint_store, "save", None)):
            raise TypeError("checkpoint_store.save must be callable")
        if not callable(getattr(checkpoint_store, "acknowledge", None)):
            raise TypeError("checkpoint_store.acknowledge must be callable")
        self.checkpoint_store = checkpoint_store
        self.client = client or UrllibArxivFeedClient()
        self.config = config or ArxivMonitorConfig()
        self.clock = clock or _utc_now
        # Deliberately process-local: a newly constructed monitor cuts a new
        # feed baseline instead of replaying changes from the offline window.
        self._online_baseline_attempted = False
        self._online_baseline_established = False
        # A static iterable may be a one-shot generator.  Materialize and
        # validate it exactly once so every beat observes the same plan.
        self._subscriptions: SubscriptionSource = (
            subscriptions
            if callable(subscriptions)
            else self._normalize_subscriptions(tuple(subscriptions or ()))
        )

    def observe(
        self,
        context: HeartbeatContext,
    ) -> Iterable[ObservationTriggerDelivery]:
        now = self._now()
        target_by_owner = {
            str(target.owner_id or "").strip(): target
            for target in context.targets
            if str(target.owner_id or "").strip()
        }
        subscriptions = tuple(
            item for item in self._load_subscriptions() if item.enabled
        )
        checkpoint = self.checkpoint_store.load(_STORE_KEY)
        active_owner_ids = {item.owner_id for item in subscriptions}
        retained_pending = tuple(
            item
            for item in checkpoint.pending_deliveries
            if item.owner_id in active_owner_ids
        )
        if retained_pending != checkpoint.pending_deliveries:
            checkpoint = self._replace_checkpoint(
                checkpoint,
                pending_deliveries=retained_pending,
            )
            self.checkpoint_store.save(_STORE_KEY, checkpoint)
        if not subscriptions:
            return ()

        # Source scope is independent of runtime availability.  Changing the
        # endpoint, category plan, or normalization schema invalidates HTTP
        # validators and the seen frontier, but never discards owner outboxes.
        categories = _normalized_categories(
            category
            for subscription in subscriptions
            for category in subscription.categories
        )
        plan_fingerprint = self.feed_key(
            categories,
            endpoint=self._source_identity(),
        )
        if checkpoint.plan_fingerprint != plan_fingerprint:
            checkpoint = ArxivCheckpoint(
                plan_fingerprint=plan_fingerprint,
                pending_deliveries=checkpoint.pending_deliveries,
                rr_last_owner_id=checkpoint.rr_last_owner_id,
                outbox_overflows=checkpoint.outbox_overflows,
            )
            self.checkpoint_store.save(_STORE_KEY, checkpoint)

        if not self._online_baseline_established:
            if (
                self._online_baseline_attempted
                and not self._poll_is_due(checkpoint, now)
            ):
                return ()
            self._online_baseline_attempted = True
            attempted = self._replace_checkpoint(
                checkpoint,
                next_poll_at=_iso_utc(
                    now + timedelta(seconds=self.config.retry_backoff_seconds)
                ),
            )
            self.checkpoint_store.save(_STORE_KEY, attempted)
            try:
                response, papers = self._fetch_source(
                    categories=categories,
                    # A process session baseline is an unconditional current
                    # snapshot.  Do not let prior-process HTTP validators turn
                    # it into a cursor-dependent incremental poll.
                    checkpoint=ArxivCheckpoint(),
                )
                if response.status_code != 200:
                    raise ArxivFeedError(
                        "arXiv online-session baseline requires a complete "
                        "HTTP 200 feed response"
                    )
                retired_pending_count = len(checkpoint.pending_deliveries)
                checkpoint = self._accept_online_baseline_response(
                    checkpoint=checkpoint,
                    response=response,
                    papers=papers,
                    plan_fingerprint=plan_fingerprint,
                    now=now,
                )
                self.checkpoint_store.save(_STORE_KEY, checkpoint)
                if retired_pending_count:
                    logger.info(
                        "arXiv online-session baseline retired %s prior "
                        "pending delivery item(s)",
                        retired_pending_count,
                    )
            except Exception:
                # The retry gate and every pre-existing pending outbox entry
                # remain durable until a baseline succeeds. Pending work is
                # never emitted by this newly constructed monitor.
                raise
            self._online_baseline_established = True
            return ()

        poll_error: Optional[BaseException] = None
        if self._poll_is_due(checkpoint, now):
            retry_at = _iso_utc(
                now + timedelta(seconds=self.config.retry_backoff_seconds)
            )
            attempted = self._replace_checkpoint(
                checkpoint,
                next_poll_at=retry_at,
            )
            self.checkpoint_store.save(_STORE_KEY, attempted)
            try:
                response, papers = self._fetch_source(
                    categories=categories,
                    checkpoint=checkpoint,
                )
            except Exception as exc:
                # The attempted checkpoint already schedules a short retry;
                # its source frontier and pending owner outboxes are intact.
                poll_error = exc
                checkpoint = attempted
            else:
                previous_dropped = {
                    item.owner_id: item.dropped_count
                    for item in checkpoint.outbox_overflows
                }
                checkpoint = self._accept_source_response(
                    checkpoint=checkpoint,
                    response=response,
                    papers=papers,
                    subscriptions=subscriptions,
                    plan_fingerprint=plan_fingerprint,
                    now=now,
                )
                # A 200 response durably commits validators, every unseen
                # source revision, and all resulting owner outbox entries.
                self.checkpoint_store.save(_STORE_KEY, checkpoint)
                overflow_deltas = {
                    item.owner_id: max(
                        0,
                        item.dropped_count
                        - previous_dropped.get(item.owner_id, 0),
                    )
                    for item in checkpoint.outbox_overflows
                }
                overflow_delta = sum(overflow_deltas.values())
                if overflow_delta:
                    affected_owners = ",".join(
                        sorted(
                            owner_id
                            for owner_id, count in overflow_deltas.items()
                            if count
                        )
                    )
                    poll_error = ArxivOutboxOverflowError(
                        "arXiv owner outbox capacity exceeded; "
                        f"owners={affected_owners}; dropped "
                        f"{overflow_delta} delivery item(s)"
                    )

        deliveries = self._deliver_pending(
            checkpoint=checkpoint,
            target_by_owner=target_by_owner,
            now=now,
        )
        if poll_error is not None:
            # Iterable semantics let Heartbeat ingest due durable outbox work
            # first, then observe the provider failure in monitor health.
            return self._deliveries_then_raise(deliveries, poll_error)
        return deliveries

    @staticmethod
    def feed_key(
        categories: Sequence[str],
        *,
        endpoint: str = "",
    ) -> str:
        normalized_categories = _normalized_categories(categories)
        encoded = json.dumps(
            {
                "provider": "arxiv_atom",
                "endpoint": str(endpoint or "").strip().rstrip("/"),
                "categories": list(normalized_categories),
                "schema": _PLAN_SCHEMA_VERSION,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"arxiv_plan:{hashlib.sha256(encoded).hexdigest()}"

    @property
    def checkpoint_key(self) -> str:
        """Stable store key; plan changes are represented inside its value."""

        return _STORE_KEY

    def _source_identity(self) -> str:
        raw = getattr(self.client, "source_identity", "")
        if callable(raw):
            raw = raw()
        value = str(raw or "").strip()
        if not value:
            value = str(
                getattr(self.client, "feed_base_url", "") or ""
            ).strip()
        if value:
            return value.rstrip("/")
        client_type = type(self.client)
        return f"{client_type.__module__}.{client_type.__qualname__}"

    def _load_subscriptions(self) -> Tuple[ArxivSubscription, ...]:
        raw = (
            self._subscriptions()
            if callable(self._subscriptions)
            else self._subscriptions
        )
        return self._normalize_subscriptions(tuple(raw or ()))

    @staticmethod
    def _normalize_subscriptions(
        subscriptions: Sequence[ArxivSubscription],
    ) -> Tuple[ArxivSubscription, ...]:
        if not all(
            isinstance(item, ArxivSubscription) for item in subscriptions
        ):
            raise TypeError(
                "subscriptions must contain ArxivSubscription values"
            )
        by_id: Dict[str, ArxivSubscription] = {}
        for item in subscriptions:
            if item.subscription_id in by_id:
                raise ValueError(
                    "duplicate arXiv subscription_id: "
                    f"{item.subscription_id}"
                )
            by_id[item.subscription_id] = item
        return tuple(by_id[key] for key in sorted(by_id))

    def _now(self) -> datetime:
        now = self.clock()
        if not isinstance(now, datetime):
            raise TypeError("clock must return datetime")
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)

    @staticmethod
    def _poll_is_due(
        checkpoint: ArxivCheckpoint,
        now: datetime,
    ) -> bool:
        due_at = _parse_iso_utc(checkpoint.next_poll_at)
        return due_at is None or now >= due_at

    def _fetch_source(
        self,
        *,
        categories: Sequence[str],
        checkpoint: ArxivCheckpoint,
    ) -> Tuple[ArxivFeedResponse, Tuple[ArxivPaper, ...]]:
        response = self.client.fetch(
            categories=categories,
            etag=checkpoint.etag,
            last_modified=checkpoint.last_modified,
            timeout_seconds=self.config.request_timeout_seconds,
            max_response_bytes=self.config.max_response_bytes,
        )
        if response.status_code not in {200, 304}:
            raise ArxivFeedError(
                "arXiv feed returned status " f"{response.status_code}"
            )
        papers = (
            parse_arxiv_feed(
                response.content,
                max_items=self.config.max_feed_items,
            )
            if response.status_code == 200
            else ()
        )
        return response, tuple(papers)

    def _accept_online_baseline_response(
        self,
        *,
        checkpoint: ArxivCheckpoint,
        response: ArxivFeedResponse,
        papers: Sequence[ArxivPaper],
        plan_fingerprint: str,
        now: datetime,
    ) -> ArxivCheckpoint:
        """Commit the current feed frontier without creating new outbox work."""

        current_revisions = tuple(paper.revision_id for paper in papers)
        return ArxivCheckpoint(
            plan_fingerprint=plan_fingerprint,
            etag=response.etag,
            last_modified=response.last_modified,
            seen_revision_ids=self._bounded_seen_revisions(
                (),
                current_revisions,
            ),
            next_poll_at=_iso_utc(
                now + timedelta(seconds=self.config.poll_interval_seconds)
            ),
            # Pending entries belong to the prior process session.  Clearing
            # them is part of the successful new baseline commit, so restart
            # never replays stale work or the offline source window.
            pending_deliveries=(),
            rr_last_owner_id="",
            outbox_overflows=checkpoint.outbox_overflows,
        )

    def _accept_source_response(
        self,
        *,
        checkpoint: ArxivCheckpoint,
        response: ArxivFeedResponse,
        papers: Sequence[ArxivPaper],
        subscriptions: Sequence[ArxivSubscription],
        plan_fingerprint: str,
        now: datetime,
    ) -> ArxivCheckpoint:
        next_poll_at = _iso_utc(
            now + timedelta(seconds=self.config.poll_interval_seconds)
        )
        if response.status_code == 304:
            return ArxivCheckpoint(
                plan_fingerprint=plan_fingerprint,
                etag=response.etag or checkpoint.etag,
                last_modified=(
                    response.last_modified or checkpoint.last_modified
                ),
                seen_revision_ids=checkpoint.seen_revision_ids,
                next_poll_at=next_poll_at,
                pending_deliveries=checkpoint.pending_deliveries,
                rr_last_owner_id=checkpoint.rr_last_owner_id,
                outbox_overflows=checkpoint.outbox_overflows,
            )

        committed_seen = set(checkpoint.seen_revision_ids)
        unseen = tuple(
            paper
            for paper in papers
            if paper.revision_id not in committed_seen
        )
        batch_id = self._batch_id(
            feed_key=plan_fingerprint,
            papers=unseen,
            etag=response.etag,
            last_modified=response.last_modified,
        )
        new_pending = self._match_deliveries(
            unseen,
            subscriptions,
            batch_id=batch_id,
        )
        merged_pending, overflowed = self._merge_pending(
            checkpoint.pending_deliveries,
            new_pending,
            max_pending_per_owner=self.config.max_pending_per_owner,
        )
        overflows = self._updated_overflows(
            checkpoint.outbox_overflows,
            overflowed,
            occurred_at=_iso_utc(now),
        )
        return ArxivCheckpoint(
            plan_fingerprint=plan_fingerprint,
            etag=response.etag or checkpoint.etag,
            last_modified=(
                response.last_modified or checkpoint.last_modified
            ),
            seen_revision_ids=self._bounded_seen_revisions(
                checkpoint.seen_revision_ids,
                tuple(paper.revision_id for paper in unseen),
            ),
            next_poll_at=next_poll_at,
            pending_deliveries=merged_pending,
            rr_last_owner_id=checkpoint.rr_last_owner_id,
            outbox_overflows=overflows,
        )

    @staticmethod
    def _deliveries_then_raise(
        deliveries: Sequence[ObservationTriggerDelivery],
        error: BaseException,
    ) -> Iterable[ObservationTriggerDelivery]:
        for delivery in deliveries:
            yield delivery
        raise error

    @staticmethod
    def _match_deliveries(
        papers: Sequence[ArxivPaper],
        subscriptions: Sequence[ArxivSubscription],
        *,
        batch_id: str,
    ) -> Tuple[ArxivPendingDelivery, ...]:
        grouped: Dict[Tuple[str, str], List[str]] = {}
        paper_by_revision = {paper.revision_id: paper for paper in papers}
        for paper in papers:
            for subscription in subscriptions:
                if subscription.matches(paper):
                    grouped.setdefault(
                        (subscription.owner_id, paper.revision_id),
                        [],
                    ).append(subscription.subscription_id)
        deliveries = []
        for owner_id, revision_id in sorted(grouped):
            subscription_ids = tuple(sorted(set(grouped[(owner_id, revision_id)])))
            deliveries.append(
                ArxivPendingDelivery(
                    delivery_id=f"{owner_id}:{revision_id}",
                    owner_id=owner_id,
                    matched_subscription_ids=subscription_ids,
                    paper=paper_by_revision[revision_id],
                    batch_id=batch_id,
                )
            )
        return tuple(deliveries)

    @staticmethod
    def _merge_pending(
        existing: Sequence[ArxivPendingDelivery],
        additions: Sequence[ArxivPendingDelivery],
        *,
        max_pending_per_owner: int,
    ) -> Tuple[
        Tuple[ArxivPendingDelivery, ...],
        Mapping[str, Tuple[str, ...]],
    ]:
        limit = max(1, int(max_pending_per_owner))
        by_id: Dict[str, ArxivPendingDelivery] = {}
        ordered_ids: List[str] = []
        owner_counts: Dict[str, int] = {}
        overflowed: Dict[str, List[str]] = {}
        # Existing durable work is never retroactively discarded when an
        # operator lowers the cap.  It may temporarily remain above the new
        # limit, but no new item for that owner is admitted until it drains.
        for item in existing:
            prior = by_id.get(item.delivery_id)
            if prior is not None:
                continue
            by_id[item.delivery_id] = item
            ordered_ids.append(item.delivery_id)
            owner_counts[item.owner_id] = owner_counts.get(item.owner_id, 0) + 1

        for item in additions:
            prior = by_id.get(item.delivery_id)
            if prior is not None:
                merged_ids = tuple(
                    sorted(
                        set(prior.matched_subscription_ids).union(
                            item.matched_subscription_ids
                        )
                    )
                )
                by_id[item.delivery_id] = ArxivPendingDelivery(
                    delivery_id=prior.delivery_id,
                    owner_id=prior.owner_id,
                    matched_subscription_ids=merged_ids,
                    paper=prior.paper,
                    batch_id=prior.batch_id or item.batch_id,
                    next_attempt_at=prior.next_attempt_at,
                )
                continue
            count = owner_counts.get(item.owner_id, 0)
            if count >= limit:
                overflowed.setdefault(item.owner_id, []).append(
                    item.paper.revision_id
                )
                continue
            by_id[item.delivery_id] = item
            ordered_ids.append(item.delivery_id)
            owner_counts[item.owner_id] = count + 1
        return (
            tuple(by_id[item_id] for item_id in ordered_ids),
            {
                owner_id: tuple(revision_ids)
                for owner_id, revision_ids in overflowed.items()
            },
        )

    @staticmethod
    def _updated_overflows(
        existing: Sequence[ArxivOutboxOverflow],
        overflowed: Mapping[str, Sequence[str]],
        *,
        occurred_at: str,
    ) -> Tuple[ArxivOutboxOverflow, ...]:
        by_owner = {item.owner_id: item for item in existing}
        owner_order = [item.owner_id for item in existing]
        for owner_id in sorted(overflowed):
            revision_ids = tuple(overflowed[owner_id])
            if not revision_ids:
                continue
            prior = by_owner.get(owner_id)
            if prior is None:
                owner_order.append(owner_id)
            total = (prior.dropped_count if prior is not None else 0) + len(
                revision_ids
            )
            by_owner[owner_id] = ArxivOutboxOverflow(
                owner_id=owner_id,
                dropped_count=total,
                last_overflow_at=occurred_at,
                latest_dropped_revision_ids=revision_ids[-20:],
            )
            logger.error(
                "arXiv owner outbox overflow owner_id=%s dropped=%s "
                "total_dropped=%s",
                owner_id,
                len(revision_ids),
                total,
            )
        return tuple(by_owner[owner_id] for owner_id in owner_order)

    def _deliver_pending(
        self,
        *,
        checkpoint: ArxivCheckpoint,
        target_by_owner: Mapping[str, HeartbeatTarget],
        now: datetime,
    ) -> Tuple[ObservationTriggerDelivery, ...]:
        observed_at = _iso_utc(now)
        eligible = self._round_robin_pending(
            checkpoint.pending_deliveries,
            target_by_owner=target_by_owner,
            now=now,
            last_owner_id=checkpoint.rr_last_owner_id,
        )
        selected = eligible[: self.config.max_deliveries_per_beat]
        if not selected:
            return ()

        # Arm the persistent retry gate before RuntimeHost.ingest is called.
        # Success removes the item via on_ingested; failure leaves it gated.
        selected_ids = {item.delivery_id for item in selected}
        next_attempt_at = _iso_utc(
            now + timedelta(seconds=self.config.retry_backoff_seconds)
        )
        armed_pending = tuple(
            ArxivPendingDelivery(
                delivery_id=item.delivery_id,
                owner_id=item.owner_id,
                matched_subscription_ids=item.matched_subscription_ids,
                paper=item.paper,
                batch_id=item.batch_id,
                next_attempt_at=(
                    next_attempt_at
                    if item.delivery_id in selected_ids
                    else item.next_attempt_at
                ),
            )
            for item in checkpoint.pending_deliveries
        )
        armed_checkpoint = self._replace_checkpoint(
            checkpoint,
            pending_deliveries=armed_pending,
            rr_last_owner_id=selected[-1].owner_id,
        )
        self.checkpoint_store.save(_STORE_KEY, armed_checkpoint)

        deliveries = []
        for item in selected:
            target = target_by_owner[item.owner_id]
            paper = item.paper
            occurred_at = paper.announced_at or observed_at
            event_type = (
                "arxiv_paper_revised"
                if paper.version > 1
                or "replace" in str(paper.announce_type or "").casefold()
                else "arxiv_paper_matched"
            )
            observation = target.build_observation_trigger(
                monitor_id=self.monitor_id,
                source="arxiv",
                source_event_id=paper.revision_id,
                occurred_at=occurred_at,
                observed_at=observed_at,
                subject=paper.title,
                text=f"arXiv paper matched: {paper.title}",
                confidence=1.0,
                privacy_class="public",
                payload={
                    "event_type": event_type,
                    "trigger_source": "arxiv_rss",
                    "batch_id": item.batch_id,
                    "matched_subscription_ids": list(
                        item.matched_subscription_ids
                    ),
                    "paper": paper.to_dict(),
                },
            )
            deliveries.append(
                ObservationTriggerDelivery(
                    target=target,
                    observation=observation,
                    schedule_drainer=self.config.schedule_drainer,
                    on_ingested=self._ack_callback(
                        delivery_id=item.delivery_id,
                    ),
                )
            )
        return tuple(deliveries)

    @staticmethod
    def _round_robin_pending(
        pending: Sequence[ArxivPendingDelivery],
        *,
        target_by_owner: Mapping[str, HeartbeatTarget],
        now: datetime,
        last_owner_id: str,
    ) -> Tuple[ArxivPendingDelivery, ...]:
        """Interleave due owner queues using a persistent cross-beat cursor."""

        owner_order: List[str] = []
        by_owner: Dict[str, List[ArxivPendingDelivery]] = {}
        for item in pending:
            if item.owner_id not in target_by_owner:
                continue
            due_at = _parse_iso_utc(item.next_attempt_at)
            if due_at is not None and now < due_at:
                continue
            if item.owner_id not in by_owner:
                owner_order.append(item.owner_id)
                by_owner[item.owner_id] = []
            by_owner[item.owner_id].append(item)
        if last_owner_id in owner_order:
            pivot = owner_order.index(last_owner_id) + 1
            owner_order = owner_order[pivot:] + owner_order[:pivot]
        ordered: List[ArxivPendingDelivery] = []
        max_depth = max(
            (len(items) for items in by_owner.values()),
            default=0,
        )
        for index in range(max_depth):
            for owner_id in owner_order:
                queue = by_owner[owner_id]
                if index < len(queue):
                    ordered.append(queue[index])
        return tuple(ordered)

    def _ack_callback(
        self,
        *,
        delivery_id: str,
    ) -> Callable[[IngestResult], None]:
        def acknowledge(_result: IngestResult) -> None:
            accepted = self.checkpoint_store.acknowledge(
                _STORE_KEY,
                delivery_id=delivery_id,
            )
            if not accepted:
                raise RuntimeError(
                    "arXiv pending delivery acknowledgement was stale"
                )

        return acknowledge

    def _bounded_seen_revisions(
        self,
        committed: Sequence[str],
        completed: Sequence[str],
    ) -> Tuple[str, ...]:
        combined = list(committed)
        combined.extend(completed)
        deduped = list(dict.fromkeys(combined))
        return tuple(deduped[-self.config.seen_revision_limit :])

    @staticmethod
    def _replace_checkpoint(
        checkpoint: ArxivCheckpoint,
        *,
        next_poll_at: Optional[str] = None,
        pending_deliveries: Optional[
            Sequence[ArxivPendingDelivery]
        ] = None,
        rr_last_owner_id: Optional[str] = None,
        outbox_overflows: Optional[
            Sequence[ArxivOutboxOverflow]
        ] = None,
    ) -> ArxivCheckpoint:
        return ArxivCheckpoint(
            plan_fingerprint=checkpoint.plan_fingerprint,
            etag=checkpoint.etag,
            last_modified=checkpoint.last_modified,
            seen_revision_ids=checkpoint.seen_revision_ids,
            next_poll_at=(
                checkpoint.next_poll_at
                if next_poll_at is None
                else next_poll_at
            ),
            pending_deliveries=(
                checkpoint.pending_deliveries
                if pending_deliveries is None
                else tuple(pending_deliveries)
            ),
            rr_last_owner_id=(
                checkpoint.rr_last_owner_id
                if rr_last_owner_id is None
                else str(rr_last_owner_id or "").strip()
            ),
            outbox_overflows=(
                checkpoint.outbox_overflows
                if outbox_overflows is None
                else tuple(outbox_overflows)
            ),
        )

    @staticmethod
    def _batch_id(
        *,
        feed_key: str,
        papers: Sequence[ArxivPaper],
        etag: str,
        last_modified: str,
    ) -> str:
        identity = {
            "feed_key": feed_key,
            "revision_ids": sorted(paper.revision_id for paper in papers),
            "etag": str(etag or "").strip(),
            "last_modified": str(last_modified or "").strip(),
        }
        encoded = json.dumps(
            identity,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"arxiv_batch:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "ArxivCheckpoint",
    "ArxivCheckpointStore",
    "ArxivMonitorConfig",
    "ArxivOutboxOverflowError",
    "ArxivOutboxOverflow",
    "ArxivPendingDelivery",
    "ArxivRssMonitor",
    "ArxivSubscription",
    "JsonArxivCheckpointStore",
]
