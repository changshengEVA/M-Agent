"""Heartbeat-driven, read-only Gmail observation monitor.

This module stops at the public Observation ingress boundary.  It detects
Gmail ``messageAdded`` history records, stages deterministic deliveries, and
only advances the Gmail cursor after ``ObservationTriggerDelivery`` invokes
its ``on_ingested`` acknowledgement.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from m_agent.api.heartbeat import (
    HeartbeatContext,
    HeartbeatTarget,
    ObservationTriggerDelivery,
    ObservationTriggerMonitor,
)
from m_agent.integrations.gmail_client import GmailApiClient, GmailClientConfig
from m_agent.paths import data_root_dir


logger = logging.getLogger(__name__)

_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
_METADATA_SCOPE = "https://www.googleapis.com/auth/gmail.metadata"
_READ_ONLY_SCOPES = frozenset({_READONLY_SCOPE, _METADATA_SCOPE})
_STATE_VERSION = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _parse_iso(value: object) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _clean_tuple(values: Sequence[object], *, lower: bool = False) -> Tuple[str, ...]:
    result = []
    seen = set()
    for raw in values:
        value = str(raw or "").strip()
        if lower:
            value = value.lower()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


@dataclass(frozen=True)
class GmailObservationMonitorConfig:
    """Bounded polling and deterministic filtering configuration."""

    enabled: bool = True
    owner_ids: Tuple[str, ...] = ()
    poll_interval_seconds: int = 60
    retry_backoff_seconds: int = 300
    state_path: Optional[Path] = None
    label_id: str = "INBOX"
    max_history_pages_per_beat: int = 2
    max_messages_per_beat: int = 20
    metadata_headers: Tuple[str, ...] = (
        "Subject",
        "From",
        "To",
        "Cc",
        "Date",
        "Message-ID",
        "Auto-Submitted",
        "Precedence",
        "List-Id",
    )
    include_snippet: bool = False
    subject_max_chars: int = 160
    snippet_max_chars: int = 240
    sender_allowlist: Tuple[str, ...] = ()
    sender_denylist: Tuple[str, ...] = ()
    subject_keywords: Tuple[str, ...] = ()
    excluded_label_ids: Tuple[str, ...] = ("SPAM", "TRASH", "SENT", "DRAFT")
    schedule_drainer: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "enabled", bool(self.enabled))
        object.__setattr__(self, "owner_ids", _clean_tuple(self.owner_ids))
        object.__setattr__(
            self,
            "poll_interval_seconds",
            max(1, int(self.poll_interval_seconds)),
        )
        object.__setattr__(
            self,
            "retry_backoff_seconds",
            max(10, int(self.retry_backoff_seconds)),
        )
        if self.state_path is not None:
            object.__setattr__(self, "state_path", Path(self.state_path).resolve())
        object.__setattr__(self, "label_id", str(self.label_id or "INBOX").strip() or "INBOX")
        object.__setattr__(
            self,
            "max_history_pages_per_beat",
            max(1, int(self.max_history_pages_per_beat)),
        )
        object.__setattr__(
            self,
            "max_messages_per_beat",
            max(1, int(self.max_messages_per_beat)),
        )
        object.__setattr__(self, "metadata_headers", _clean_tuple(self.metadata_headers))
        object.__setattr__(self, "subject_max_chars", max(0, int(self.subject_max_chars)))
        object.__setattr__(self, "snippet_max_chars", max(0, int(self.snippet_max_chars)))
        object.__setattr__(self, "sender_allowlist", _clean_tuple(self.sender_allowlist, lower=True))
        object.__setattr__(self, "sender_denylist", _clean_tuple(self.sender_denylist, lower=True))
        object.__setattr__(self, "subject_keywords", _clean_tuple(self.subject_keywords, lower=True))
        object.__setattr__(self, "excluded_label_ids", _clean_tuple(self.excluded_label_ids))


class GmailObservationStateStore:
    """Small atomic JSON checkpoint store, partitioned by Heartbeat owner."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self._lock = threading.RLock()

    def load(self, owner_id: str) -> Dict[str, Any]:
        key = str(owner_id or "").strip()
        if not key:
            raise ValueError("owner_id is required")
        with self._lock:
            root = self._read_root()
            raw = root.get("owners", {}).get(key, {})
            return json.loads(json.dumps(raw)) if isinstance(raw, dict) else {}

    def save(self, owner_id: str, checkpoint: Mapping[str, Any]) -> None:
        key = str(owner_id or "").strip()
        if not key:
            raise ValueError("owner_id is required")
        with self._lock:
            root = self._read_root()
            owners = root.setdefault("owners", {})
            if not isinstance(owners, dict):
                owners = {}
                root["owners"] = owners
            owners[key] = dict(checkpoint)
            self._write_root(root)

    def acknowledge(self, owner_id: str, batch_id: str, delivery_id: str) -> bool:
        """Ack one delivery and commit its cursor only when the batch is complete."""

        key = str(owner_id or "").strip()
        expected_batch = str(batch_id or "").strip()
        expected_delivery = str(delivery_id or "").strip()
        with self._lock:
            root = self._read_root()
            owners = root.get("owners")
            checkpoint = owners.get(key) if isinstance(owners, dict) else None
            staged = checkpoint.get("staged") if isinstance(checkpoint, dict) else None
            if not isinstance(staged, dict) or str(staged.get("batch_id", "")) != expected_batch:
                return False
            deliveries = staged.get("deliveries")
            deliveries = deliveries if isinstance(deliveries, list) else []
            known = {
                str(item.get("delivery_id", "") or "").strip()
                for item in deliveries
                if isinstance(item, dict)
            }
            if expected_delivery not in known:
                return False
            acknowledged = {
                str(item or "").strip()
                for item in staged.get("acknowledged_delivery_ids", [])
                if str(item or "").strip()
            }
            acknowledged.add(expected_delivery)
            staged["acknowledged_delivery_ids"] = sorted(acknowledged)
            remaining = staged.get("remaining_message_refs")
            remaining = remaining if isinstance(remaining, list) else []
            if known.issubset(acknowledged) and not remaining:
                checkpoint["cursor"] = str(staged.get("commit_history_id", "") or "").strip()
                checkpoint["staged"] = None
            self._write_root(root)
            return True

    def _read_root(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"version": _STATE_VERSION, "owners": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("failed to read Gmail observation state") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Gmail observation state root must be an object")
        if not isinstance(payload.get("owners", {}), dict):
            raise RuntimeError("Gmail observation owners state must be an object")
        return payload

    def _write_root(self, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        encoded = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True)
        try:
            temporary.write_text(encoded, encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                pass


GmailClientProvider = Callable[[HeartbeatTarget], GmailApiClient]


class GmailObservationTriggerMonitor(ObservationTriggerMonitor):
    """Incrementally turn new Gmail messages into Observation deliveries."""

    def __init__(
        self,
        config: Optional[GmailObservationMonitorConfig] = None,
        *,
        gmail_client_provider: Optional[GmailClientProvider] = None,
        state_store: Optional[GmailObservationStateStore] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        super().__init__("gmail_email")
        self.config = config or GmailObservationMonitorConfig()
        state_path = self.config.state_path or (
            data_root_dir() / "observations" / "gmail_email_state.json"
        )
        self.state_store = state_store or GmailObservationStateStore(state_path)
        self._client_provider = gmail_client_provider
        self._clock = clock or _utc_now
        self._default_clients: Dict[Tuple[str, int], GmailApiClient] = {}
        self._client_lock = threading.Lock()
        # This is intentionally process-local.  Durable cursors make retries
        # safe, but a newly constructed monitor always cuts a fresh source
        # baseline so mail received while the process was offline is skipped.
        self._online_baseline_attempted_owner_ids: set[str] = set()
        self._online_baseline_established_owner_ids: set[str] = set()

    def observe(self, context: HeartbeatContext) -> Iterable[ObservationTriggerDelivery]:
        if not self.config.enabled:
            return ()
        allowed = set(self.config.owner_ids)
        deliveries = []
        for target in context.targets:
            owner_id = str(target.owner_id or "").strip()
            # Owner filtering happens before resolving a Gmail client, so a
            # disabled owner cannot trigger OAuth, filesystem, or API work.
            # Empty means no owner, never broadcast one mailbox into every
            # runtime target.  Production composition requires one owner.
            if not owner_id or owner_id not in allowed:
                continue
            deliveries.extend(self._observe_target(target))
        return tuple(deliveries)

    def _observe_target(self, target: HeartbeatTarget) -> Sequence[ObservationTriggerDelivery]:
        owner_id = str(target.owner_id or "").strip()
        now = _as_utc(self._clock())
        checkpoint = self.state_store.load(owner_id)
        if owner_id not in self._online_baseline_established_owner_ids:
            next_poll_at = _parse_iso(checkpoint.get("next_poll_at"))
            if (
                owner_id in self._online_baseline_attempted_owner_ids
                and next_poll_at is not None
                and now < next_poll_at
            ):
                return ()
            self._online_baseline_attempted_owner_ids.add(owner_id)
            try:
                self._establish_online_baseline(
                    target=target,
                    checkpoint=checkpoint,
                    now=now,
                )
            except Exception:
                failed_checkpoint = self.state_store.load(owner_id)
                failed_checkpoint["next_poll_at"] = self._retry_at(now)
                failed_checkpoint["last_error_at"] = _iso(now)
                self.state_store.save(owner_id, failed_checkpoint)
                raise
            self._online_baseline_established_owner_ids.add(owner_id)
            # Baseline establishment never emits.  Prior-session staged work
            # is retired by the successful baseline commit above.
            return ()

        next_poll_at = _parse_iso(checkpoint.get("next_poll_at"))
        if next_poll_at is not None and now < next_poll_at:
            return ()

        staged = checkpoint.get("staged")
        if isinstance(staged, dict):
            try:
                # Arm the next gate before returning deliveries.  If ingest
                # fails after observe() returns, the still-unacknowledged
                # staged batch is not replayed on every Heartbeat tick.
                checkpoint["next_poll_at"] = self._next_poll(now)
                checkpoint.pop("last_error_at", None)
                self.state_store.save(owner_id, checkpoint)
                return self._resume_staged(target, checkpoint, staged, now)
            except Exception:
                # A later chunk may require Gmail metadata reads.  Keep the
                # staged batch/cursor intact and apply the configured error
                # backoff before retrying those external calls.
                failed_checkpoint = self.state_store.load(owner_id)
                failed_checkpoint["next_poll_at"] = self._retry_at(now)
                failed_checkpoint["last_error_at"] = _iso(now)
                self.state_store.save(owner_id, failed_checkpoint)
                raise

        try:
            client = self._client_for(target)
        except Exception:
            checkpoint["next_poll_at"] = self._retry_at(now)
            checkpoint["last_error_at"] = _iso(now)
            self.state_store.save(owner_id, checkpoint)
            raise

        cursor = str(checkpoint.get("cursor", "") or "").strip()
        account_key = str(checkpoint.get("account_key", "") or "").strip()
        if not cursor or not account_key:
            checkpoint["next_poll_at"] = self._retry_at(now)
            checkpoint["last_error_at"] = _iso(now)
            self.state_store.save(owner_id, checkpoint)
            raise RuntimeError("Gmail online baseline cursor is missing")

        checkpoint["next_poll_at"] = self._next_poll(now)
        checkpoint.pop("last_error_at", None)
        self.state_store.save(owner_id, checkpoint)
        try:
            return self._poll_history(
                target,
                client,
                checkpoint,
                account_key,
                now,
            )
        except Exception:
            failed_checkpoint = self.state_store.load(owner_id)
            failed_checkpoint["next_poll_at"] = self._retry_at(now)
            failed_checkpoint["last_error_at"] = _iso(now)
            self.state_store.save(owner_id, failed_checkpoint)
            raise

    def _establish_online_baseline(
        self,
        *,
        target: HeartbeatTarget,
        checkpoint: Dict[str, Any],
        now: datetime,
    ) -> None:
        """Cut a present-time cursor and retire prior-session staged work."""

        owner_id = str(target.owner_id or "").strip()
        client = self._client_for(target)
        profile = client.get_profile()
        email_address = str(profile.get("emailAddress", "") or "").strip().lower()
        profile_history_id = str(profile.get("historyId", "") or "").strip()
        if not email_address or not profile_history_id:
            raise RuntimeError("Gmail profile omitted emailAddress or historyId")
        account_key = self._account_key(email_address)
        retired_staged = isinstance(checkpoint.get("staged"), dict)
        # A staged batch belongs to the process session that observed it.
        # Replace it only after getProfile succeeds, so a transient baseline
        # failure neither deletes durable evidence nor replays it prematurely.
        checkpoint["cursor"] = profile_history_id
        checkpoint["account_key"] = account_key
        checkpoint["staged"] = None
        checkpoint["next_poll_at"] = self._next_poll(now)
        checkpoint.pop("last_error_at", None)
        self.state_store.save(owner_id, checkpoint)
        if retired_staged:
            logger.info(
                "Gmail online-session baseline retired prior staged batch "
                "owner_id=%s",
                owner_id,
            )

    def _poll_history(
        self,
        target: HeartbeatTarget,
        client: GmailApiClient,
        checkpoint: Dict[str, Any],
        account_key: str,
        now: datetime,
    ) -> Sequence[ObservationTriggerDelivery]:
        owner_id = str(target.owner_id or "").strip()
        cursor = str(checkpoint.get("cursor", "") or "").strip()
        messages_left = self.config.max_messages_per_beat

        for _page_index in range(self.config.max_history_pages_per_beat):
            try:
                response = client.list_history(
                    start_history_id=cursor,
                    max_results=min(500, self.config.max_messages_per_beat),
                    # A fresh request from the last committed history record
                    # avoids persisting or misusing page tokens tied to an
                    # older startHistoryId query.
                    page_token=None,
                    label_id=self.config.label_id,
                    history_types=("messageAdded",),
                )
            except Exception as exc:
                if not self._is_http_status(exc, 404):
                    raise
                return self._rebaseline_expired_cursor(
                    owner_id=owner_id,
                    client=client,
                    checkpoint=checkpoint,
                    now=now,
                )
            histories = response.get("history")
            histories = histories if isinstance(histories, list) else []
            for history in histories:
                if not isinstance(history, dict):
                    continue
                history_id = str(history.get("id", "") or "").strip()
                if not history_id:
                    continue
                refs = self._message_refs(history)
                if not refs:
                    checkpoint["cursor"] = history_id
                    self.state_store.save(owner_id, checkpoint)
                    cursor = history_id
                    continue

                current_refs = refs[:messages_left]
                remaining_refs = refs[messages_left:]
                events = self._materialize_events(
                    client,
                    current_refs,
                    account_key=account_key,
                    observed_at=now,
                )
                messages_left -= len(current_refs)
                if events or remaining_refs:
                    staged = self._new_staged_batch(
                        owner_id=owner_id,
                        starting_cursor=cursor,
                        commit_history_id=history_id,
                        deliveries=events,
                        remaining_refs=remaining_refs,
                    )
                    checkpoint["staged"] = staged
                    self.state_store.save(owner_id, checkpoint)
                    if events:
                        return self._deliveries_for(target, staged)
                    return ()

                # Every message in this history record was deterministically
                # filtered, so no runtime acknowledgement is necessary.
                checkpoint["cursor"] = history_id
                self.state_store.save(owner_id, checkpoint)
                cursor = history_id
                if messages_left <= 0:
                    return ()

            next_page = str(response.get("nextPageToken", "") or "").strip()
            if not next_page:
                high_water = str(response.get("historyId", "") or "").strip()
                if high_water:
                    checkpoint["cursor"] = high_water
                    self.state_store.save(owner_id, checkpoint)
                return ()
            if not histories:
                # Defensive stop: an opaque nextPageToken without any history
                # record gives us no durable cursor from which to resume.
                return ()
        # No page token is persisted intentionally.  Each fully processed
        # history record advances ``cursor``, so the next bounded request can
        # safely start from that Gmail-native cursor.
        return ()

    def _rebaseline_expired_cursor(
        self,
        *,
        owner_id: str,
        client: GmailApiClient,
        checkpoint: Mapping[str, Any],
        now: datetime,
    ) -> Sequence[ObservationTriggerDelivery]:
        """Bound a stale-history recovery by establishing a current baseline.

        Gmail documents an HTTP 404 for expired history cursors.  This monitor
        deliberately does not replay the entire mailbox in that case; it
        records the resync and resumes from the current mailbox high-water
        mark on a later poll.
        """

        profile = client.get_profile()
        email_address = str(profile.get("emailAddress", "") or "").strip().lower()
        history_id = str(profile.get("historyId", "") or "").strip()
        if not email_address or not history_id:
            raise RuntimeError("Gmail profile omitted emailAddress or historyId")
        try:
            resync_count = max(0, int(checkpoint.get("resync_count", 0) or 0)) + 1
        except (TypeError, ValueError):
            resync_count = 1
        self.state_store.save(
            owner_id,
            {
                "account_key": self._account_key(email_address),
                "cursor": history_id,
                "next_poll_at": self._next_poll(now),
                "staged": None,
                "resync_count": resync_count,
                "last_resync_at": _iso(now),
                "last_resync_reason": "history_cursor_expired",
            },
        )
        logger.warning(
            "Gmail history cursor expired; established a new baseline owner_id=%s",
            owner_id,
        )
        return ()

    def _resume_staged(
        self,
        target: HeartbeatTarget,
        checkpoint: Dict[str, Any],
        staged: Dict[str, Any],
        now: datetime,
    ) -> Sequence[ObservationTriggerDelivery]:
        deliveries = staged.get("deliveries")
        deliveries = deliveries if isinstance(deliveries, list) else []
        acknowledged = {
            str(item or "").strip()
            for item in staged.get("acknowledged_delivery_ids", [])
            if str(item or "").strip()
        }
        unacknowledged = [
            item
            for item in deliveries
            if isinstance(item, dict)
            and str(item.get("delivery_id", "") or "").strip() not in acknowledged
        ]
        if unacknowledged:
            replay = dict(staged)
            replay["deliveries"] = unacknowledged
            return self._deliveries_for(target, replay, callback_batch_id=str(staged.get("batch_id", "")))

        remaining = staged.get("remaining_message_refs")
        remaining = [item for item in remaining if isinstance(item, dict)] if isinstance(remaining, list) else []
        if not remaining:
            checkpoint["cursor"] = str(staged.get("commit_history_id", "") or "").strip()
            checkpoint["staged"] = None
            self.state_store.save(str(target.owner_id or "").strip(), checkpoint)
            return ()

        current_refs = remaining[: self.config.max_messages_per_beat]
        later_refs = remaining[self.config.max_messages_per_beat :]
        client = self._client_for(target)
        events = self._materialize_events(
            client,
            current_refs,
            account_key=str(checkpoint.get("account_key", "") or "").strip(),
            observed_at=now,
        )
        staged["deliveries"] = events
        staged["acknowledged_delivery_ids"] = []
        staged["remaining_message_refs"] = later_refs
        checkpoint["staged"] = staged
        if not events and not later_refs:
            checkpoint["cursor"] = str(staged.get("commit_history_id", "") or "").strip()
            checkpoint["staged"] = None
        self.state_store.save(str(target.owner_id or "").strip(), checkpoint)
        return self._deliveries_for(target, staged) if events else ()

    def _materialize_events(
        self,
        client: GmailApiClient,
        refs: Sequence[Mapping[str, Any]],
        *,
        account_key: str,
        observed_at: datetime,
    ) -> list[Dict[str, Any]]:
        events = []
        for ref in refs:
            message_id = str(ref.get("message_id", "") or "").strip()
            if not message_id:
                continue
            try:
                raw = client.get_message(
                    message_id=message_id,
                    fmt="metadata",
                    metadata_headers=self.config.metadata_headers,
                )
            except Exception as exc:
                # A message may be permanently deleted between history.list
                # and metadata retrieval.  Treat that specific disappearance
                # as a deterministic no-delivery outcome; other failures retry
                # from the uncommitted history cursor.
                if self._is_http_status(exc, 404):
                    continue
                raise
            event = self._event_from_message(raw, ref, account_key, observed_at)
            if event is not None:
                events.append(event)
        return events

    def _event_from_message(
        self,
        raw: Mapping[str, Any],
        ref: Mapping[str, Any],
        account_key: str,
        observed_at: datetime,
    ) -> Optional[Dict[str, Any]]:
        message_id = str(raw.get("id", "") or ref.get("message_id", "") or "").strip()
        thread_id = str(raw.get("threadId", "") or ref.get("thread_id", "") or "").strip()
        if not message_id:
            return None
        labels = {
            str(item or "").strip()
            for item in (raw.get("labelIds") if isinstance(raw.get("labelIds"), list) else [])
            if str(item or "").strip()
        }
        if self.config.label_id and self.config.label_id not in labels:
            return None
        if labels.intersection(self.config.excluded_label_ids):
            return None

        headers = self._header_map(raw)
        subject = str(headers.get("subject", "") or "").strip()
        _, sender_address = parseaddr(str(headers.get("from", "") or ""))
        sender_address = sender_address.strip().lower()
        sender_domain = sender_address.rsplit("@", 1)[-1] if "@" in sender_address else ""
        if self.config.sender_denylist and self._sender_matches(
            sender_address, sender_domain, self.config.sender_denylist
        ):
            return None
        if self.config.sender_allowlist and not self._sender_matches(
            sender_address, sender_domain, self.config.sender_allowlist
        ):
            return None
        lowered_subject = subject.lower()
        if self.config.subject_keywords and not any(
            keyword in lowered_subject for keyword in self.config.subject_keywords
        ):
            return None

        occurred_at = self._message_occurred_at(raw, headers, observed_at)
        safe_subject = self._truncate(subject, self.config.subject_max_chars)
        snippet = ""
        if self.config.include_snippet:
            snippet = self._truncate(str(raw.get("snippet", "") or "").strip(), self.config.snippet_max_chars)
        source_event_id = f"{account_key}:message_added:{message_id}"
        delivery_id = hashlib.sha256(source_event_id.encode("utf-8")).hexdigest()
        matched_filters = []
        if self.config.sender_allowlist:
            matched_filters.append("sender_allowlist")
        if self.config.subject_keywords:
            matched_filters.append("subject_keywords")
        return {
            "delivery_id": delivery_id,
            "source_event_id": source_event_id,
            "message_id": message_id,
            "thread_id": thread_id,
            "occurred_at": occurred_at,
            "observed_at": _iso(observed_at),
            "subject": safe_subject,
            "snippet": snippet,
            "sender_address": sender_address,
            "sender_domain": sender_domain,
            "account_key": account_key,
            "matched_filter_ids": matched_filters,
        }

    def _deliveries_for(
        self,
        target: HeartbeatTarget,
        staged: Mapping[str, Any],
        *,
        callback_batch_id: str = "",
    ) -> list[ObservationTriggerDelivery]:
        owner_id = str(target.owner_id or "").strip()
        batch_id = callback_batch_id or str(staged.get("batch_id", "") or "").strip()
        result = []
        raw_deliveries = staged.get("deliveries")
        for event in raw_deliveries if isinstance(raw_deliveries, list) else []:
            if not isinstance(event, dict):
                continue
            message_id = str(event.get("message_id", "") or "").strip()
            thread_id = str(event.get("thread_id", "") or "").strip()
            account_key = str(event.get("account_key", "") or "").strip()
            subject = str(event.get("subject", "") or "").strip()
            sender_domain = str(event.get("sender_domain", "") or "").strip()
            text = "New Gmail message"
            if subject:
                text += f": {subject}"
            payload = {
                "event_type": "email_message_arrived",
                "trigger_source": "gmail",
                "provider": "gmail",
                "account_key": account_key,
                "message_id": message_id,
                "thread_id": thread_id,
                "sender_address": str(event.get("sender_address", "") or "").strip(),
                "sender_domain": sender_domain,
                "matched_filter_ids": list(event.get("matched_filter_ids", []) or []),
            }
            snippet = str(event.get("snippet", "") or "").strip()
            if snippet:
                payload["snippet"] = snippet
            observation = target.build_observation_trigger(
                monitor_id=self.monitor_id,
                source="gmail",
                source_event_id=str(event.get("source_event_id", "") or "").strip(),
                occurred_at=str(event.get("occurred_at", "") or "").strip(),
                observed_at=str(event.get("observed_at", "") or "").strip(),
                subject=subject,
                text=text,
                payload=payload,
                privacy_class="private",
                payload_ref=f"gmail:{account_key}:message:{message_id}",
                stimulus_view=(
                    "kind: observation_trigger\n"
                    "event_type: email_message_arrived\n"
                    f"message_id: {message_id}\n"
                    f"thread_id: {thread_id}"
                ),
            )
            delivery_id = str(event.get("delivery_id", "") or "").strip()
            result.append(
                ObservationTriggerDelivery(
                    target=target,
                    observation=observation,
                    schedule_drainer=self.config.schedule_drainer,
                    on_ingested=(
                        lambda _ingest_result, oid=owner_id, bid=batch_id, did=delivery_id: self._acknowledge_or_raise(
                            oid, bid, did
                        )
                    ),
                )
            )
        return result

    def _client_for(self, target: HeartbeatTarget) -> GmailApiClient:
        if self._client_provider is not None:
            client = self._client_provider(target)
            self._assert_read_only(client)
            self._assert_noninteractive(client)
            return client
        key = (str(target.owner_id or "").strip(), id(target.service_runtime))
        with self._client_lock:
            cached = self._default_clients.get(key)
            if cached is not None:
                return cached
            client = self._build_default_client(target)
            self._default_clients[key] = client
            return client

    @staticmethod
    def _build_default_client(target: HeartbeatTarget) -> GmailApiClient:
        runtime_agent = getattr(target.service_runtime, "agent", None)
        config_path = getattr(runtime_agent, "email_agent_config_path", None)
        if config_path is None:
            raise RuntimeError("Heartbeat target has no EmailAgent config path")
        # Import lazily to keep the low-level integration module acyclic.
        from m_agent.agents.email_agent import EmailAgent

        configured = EmailAgent(config_path=config_path).gmail_client
        GmailObservationTriggerMonitor._assert_read_only(configured)
        base = configured.config
        noninteractive = replace(
            base,
            allow_local_webserver_flow=False,
            allow_console_flow=False,
        )
        return GmailApiClient(config=noninteractive)

    def _acknowledge_or_raise(
        self,
        owner_id: str,
        batch_id: str,
        delivery_id: str,
    ) -> None:
        if not self.state_store.acknowledge(owner_id, batch_id, delivery_id):
            raise RuntimeError(
                "Gmail delivery acknowledgement did not match staged state"
            )

    @staticmethod
    def _assert_read_only(client: Any) -> None:
        config = getattr(client, "config", None)
        scopes = getattr(config, "scopes", None)
        if scopes is None:
            # Test doubles and alternate read-only transports may not expose
            # OAuth configuration.  Production GmailApiClient always does.
            return
        normalized = {str(item or "").strip() for item in scopes if str(item or "").strip()}
        if not normalized or not normalized.issubset(_READ_ONLY_SCOPES):
            raise ValueError("Gmail observation monitor requires a read-only OAuth token")

    @staticmethod
    def _assert_noninteractive(client: Any) -> None:
        """Reject real injected Gmail clients that could open an OAuth UI."""

        if not isinstance(client, GmailApiClient):
            return
        config = client.config
        if bool(config.allow_local_webserver_flow) or bool(
            config.allow_console_flow
        ):
            raise ValueError(
                "Gmail observation monitor requires non-interactive OAuth"
            )

    @staticmethod
    def _is_http_status(exc: BaseException, expected_status: int) -> bool:
        candidates = (
            getattr(getattr(exc, "resp", None), "status", None),
            getattr(exc, "status_code", None),
            getattr(exc, "code", None),
        )
        for candidate in candidates:
            try:
                if int(candidate) == int(expected_status):
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def _next_poll(self, now: datetime) -> str:
        return _iso(now + timedelta(seconds=self.config.poll_interval_seconds))

    def _retry_at(self, now: datetime) -> str:
        return _iso(now + timedelta(seconds=self.config.retry_backoff_seconds))

    @staticmethod
    def _account_key(email_address: str) -> str:
        normalized = str(email_address or "").strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _message_refs(history: Mapping[str, Any]) -> list[Dict[str, str]]:
        result = []
        seen = set()
        additions = history.get("messagesAdded")
        for item in additions if isinstance(additions, list) else []:
            message = item.get("message") if isinstance(item, dict) else None
            if not isinstance(message, dict):
                continue
            message_id = str(message.get("id", "") or "").strip()
            if not message_id or message_id in seen:
                continue
            seen.add(message_id)
            result.append(
                {
                    "message_id": message_id,
                    "thread_id": str(message.get("threadId", "") or "").strip(),
                }
            )
        return result

    @staticmethod
    def _header_map(raw: Mapping[str, Any]) -> Dict[str, str]:
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
        headers = payload.get("headers") if isinstance(payload.get("headers"), list) else []
        result = {}
        for item in headers:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip().lower()
            if name:
                result[name] = str(item.get("value", "") or "").strip()
        return result

    @staticmethod
    def _sender_matches(address: str, domain: str, rules: Sequence[str]) -> bool:
        for rule in rules:
            normalized = str(rule or "").strip().lower()
            if not normalized:
                continue
            if normalized == address:
                return True
            if normalized.startswith("@") and normalized[1:] == domain:
                return True
            if "@" not in normalized and normalized == domain:
                return True
        return False

    @staticmethod
    def _message_occurred_at(
        raw: Mapping[str, Any],
        headers: Mapping[str, str],
        fallback: datetime,
    ) -> str:
        internal_date = str(raw.get("internalDate", "") or "").strip()
        if internal_date:
            try:
                return _iso(datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc))
            except (ValueError, OverflowError, OSError):
                pass
        header_date = str(headers.get("date", "") or "").strip()
        if header_date:
            try:
                return _iso(parsedate_to_datetime(header_date))
            except (TypeError, ValueError, OverflowError):
                pass
        return _iso(fallback)

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        text = str(value or "")
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        if limit <= 3:
            return text[:limit]
        return text[: limit - 3].rstrip() + "..."

    @staticmethod
    def _new_staged_batch(
        *,
        owner_id: str,
        starting_cursor: str,
        commit_history_id: str,
        deliveries: Sequence[Mapping[str, Any]],
        remaining_refs: Sequence[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        identity = f"{owner_id}:{starting_cursor}:{commit_history_id}"
        return {
            "batch_id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            "commit_history_id": commit_history_id,
            "deliveries": [dict(item) for item in deliveries],
            "acknowledged_delivery_ids": [],
            "remaining_message_refs": [dict(item) for item in remaining_refs],
        }


__all__ = [
    "GmailClientProvider",
    "GmailObservationMonitorConfig",
    "GmailObservationStateStore",
    "GmailObservationTriggerMonitor",
]
