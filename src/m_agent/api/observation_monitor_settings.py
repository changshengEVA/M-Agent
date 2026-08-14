"""Transactional settings management for external observation monitors."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import math
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import yaml

from m_agent.config_paths import resolve_config_path

from .observation_monitor_factory import (
    build_observation_monitors_from_mapping,
    load_observation_monitor_config,
)
from .schedule_heartbeat import ScheduleHeartbeatCoordinator


MANAGED_MONITOR_IDS = frozenset({"gmail_email", "arxiv"})

_GMAIL_FIELDS = frozenset(
    {
        "enabled",
        "owner_ids",
        "poll_interval_seconds",
        "retry_backoff_seconds",
        "state_path",
        "label_id",
        "max_history_pages_per_beat",
        "max_messages_per_beat",
        "include_snippet",
        "subject_max_chars",
        "snippet_max_chars",
        "subject_keywords",
        "sender_allowlist",
        "sender_denylist",
    }
)
_ARXIV_FIELDS = frozenset(
    {
        "enabled",
        "poll_interval_seconds",
        "retry_backoff_seconds",
        "request_timeout_seconds",
        "max_response_bytes",
        "max_feed_items",
        "max_deliveries_per_beat",
        "max_pending_per_owner",
        "seen_revision_limit",
        "schedule_drainer",
        "feed_base_url",
        "user_agent",
        "state_path",
        "subscriptions",
    }
)
_ARXIV_SUBSCRIPTION_FIELDS = frozenset(
    {
        "subscription_id",
        "owner_id",
        "categories",
        "keywords",
        "exclude_keywords",
        "announce_types",
        "enabled",
    }
)
_GMAIL_PUBLIC_PATCH_FIELDS = frozenset(
    {
        "enabled",
        "poll_interval_seconds",
        "retry_backoff_seconds",
        "label_id",
        "max_history_pages_per_beat",
        "max_messages_per_beat",
        "include_snippet",
        "subject_keywords",
        "sender_allowlist",
        "sender_denylist",
    }
)
_ARXIV_PUBLIC_PATCH_FIELDS = frozenset(
    {
        "enabled",
        "poll_interval_seconds",
        "retry_backoff_seconds",
        "request_timeout_seconds",
        "max_deliveries_per_beat",
        "max_pending_per_owner",
        "subscriptions",
    }
)
_ARXIV_SUBSCRIPTION_PUBLIC_PATCH_FIELDS = frozenset(
    {
        "subscription_id",
        "categories",
        "keywords",
        "exclude_keywords",
        "announce_types",
        "enabled",
    }
)
_BOOLEAN_FIELDS = frozenset({"enabled", "include_snippet", "schedule_drainer"})
_INTEGER_FIELDS = frozenset(
    {
        "poll_interval_seconds",
        "retry_backoff_seconds",
        "max_history_pages_per_beat",
        "max_messages_per_beat",
        "subject_max_chars",
        "snippet_max_chars",
        "max_response_bytes",
        "max_feed_items",
        "max_deliveries_per_beat",
        "max_pending_per_owner",
        "seen_revision_limit",
    }
)
_STRING_FIELDS = frozenset(
    {"state_path", "label_id", "feed_base_url", "user_agent"}
)
_STRING_LIST_FIELDS = frozenset(
    {
        "owner_ids",
        "subject_keywords",
        "sender_allowlist",
        "sender_denylist",
        "categories",
        "keywords",
        "exclude_keywords",
        "announce_types",
    }
)


class ObservationMonitorSettingsError(RuntimeError):
    """A stable, HTTP-mappable settings management failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.extra = dict(extra or {})


def _revision(payload: Mapping[str, Any]) -> str:
    canonical = yaml.safe_dump(
        dict(payload),
        allow_unicode=True,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _mapping(value: object, *, field_name: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ObservationMonitorSettingsError(
            f"{field_name} must be an object"
        )
    return {str(key): deepcopy(item) for key, item in value.items()}


def _reject_unknown(
    payload: Mapping[str, Any],
    allowed: frozenset[str],
    *,
    field_name: str,
) -> None:
    unknown = sorted(str(key) for key in payload if str(key) not in allowed)
    if unknown:
        raise ObservationMonitorSettingsError(
            f"unknown {field_name} field(s): {', '.join(unknown)}"
        )


def _validate_string_list(value: object, *, field_name: str) -> None:
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ObservationMonitorSettingsError(
            f"{field_name} must be an array of strings"
        )
    if any(not item.strip() for item in value):
        raise ObservationMonitorSettingsError(
            f"{field_name} must not contain blank strings"
        )


def _validate_scalar_fields(
    section: Mapping[str, Any],
    *,
    field_name: str,
) -> None:
    for key, value in section.items():
        qualified = f"{field_name}.{key}"
        if key in _BOOLEAN_FIELDS and not isinstance(value, bool):
            raise ObservationMonitorSettingsError(
                f"{qualified} must be a boolean"
            )
        if key in _INTEGER_FIELDS and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            raise ObservationMonitorSettingsError(
                f"{qualified} must be an integer"
            )
        if key == "request_timeout_seconds" and (
            not isinstance(value, (int, float)) or isinstance(value, bool)
        ):
            raise ObservationMonitorSettingsError(
                f"{qualified} must be a number"
            )
        if key in _STRING_FIELDS and not isinstance(value, str):
            raise ObservationMonitorSettingsError(
                f"{qualified} must be a string"
            )
        if key in _STRING_LIST_FIELDS:
            _validate_string_list(value, field_name=qualified)


def _validate_full_config(payload: Mapping[str, Any]) -> None:
    _reject_unknown(
        payload,
        frozenset({"gmail_email", "arxiv"}),
        field_name="observation monitor config",
    )
    gmail = _mapping(payload.get("gmail_email", {}), field_name="gmail_email")
    _reject_unknown(gmail, _GMAIL_FIELDS, field_name="gmail_email")
    _validate_scalar_fields(gmail, field_name="gmail_email")

    arxiv = _mapping(payload.get("arxiv", {}), field_name="arxiv")
    _reject_unknown(arxiv, _ARXIV_FIELDS, field_name="arxiv")
    _validate_scalar_fields(arxiv, field_name="arxiv")
    subscriptions = arxiv.get("subscriptions")
    if subscriptions is not None:
        if not isinstance(subscriptions, list):
            raise ObservationMonitorSettingsError(
                "arxiv.subscriptions must be an array"
            )
        subscription_ids: set[str] = set()
        for index, raw in enumerate(subscriptions):
            subscription = _mapping(
                raw,
                field_name=f"arxiv.subscriptions[{index}]",
            )
            _reject_unknown(
                subscription,
                _ARXIV_SUBSCRIPTION_FIELDS,
                field_name=f"arxiv.subscriptions[{index}]",
            )
            for required in ("subscription_id", "owner_id"):
                if not isinstance(subscription.get(required), str) or not str(
                    subscription.get(required) or ""
                ).strip():
                    raise ObservationMonitorSettingsError(
                        f"arxiv.subscriptions[{index}].{required} must be "
                        "a non-empty string"
                    )
            subscription_id = str(subscription["subscription_id"]).strip()
            if subscription_id in subscription_ids:
                raise ObservationMonitorSettingsError(
                    "duplicate arxiv subscription_id: " + subscription_id
                )
            subscription_ids.add(subscription_id)
            if "enabled" in subscription and not isinstance(
                subscription["enabled"], bool
            ):
                raise ObservationMonitorSettingsError(
                    f"arxiv.subscriptions[{index}].enabled must be a boolean"
                )
            for key in (
                "categories",
                "keywords",
                "exclude_keywords",
                "announce_types",
            ):
                if key in subscription:
                    _validate_string_list(
                        subscription[key],
                        field_name=f"arxiv.subscriptions[{index}].{key}",
                    )
            categories = subscription.get("categories")
            if not isinstance(categories, list) or not any(
                str(item or "").strip() for item in categories
            ):
                raise ObservationMonitorSettingsError(
                    f"arxiv.subscriptions[{index}].categories must contain "
                    "at least one non-empty category"
                )
    if arxiv.get("enabled") is True and not subscriptions:
        raise ObservationMonitorSettingsError(
            "arxiv monitor is enabled but has no subscriptions"
        )
    if "label_id" in gmail and not str(gmail["label_id"] or "").strip():
        raise ObservationMonitorSettingsError(
            "gmail_email.label_id must be a non-empty string"
        )
    for field_name in ("feed_base_url",):
        if field_name in arxiv and not str(arxiv[field_name] or "").strip():
            raise ObservationMonitorSettingsError(
                f"arxiv.{field_name} must be a non-empty string"
            )
    _validate_ranges(gmail=gmail, arxiv=arxiv)


def _bounded_integer(
    section: Mapping[str, Any],
    key: str,
    *,
    minimum: int,
    maximum: Optional[int] = None,
    field_name: str,
) -> None:
    if key not in section:
        return
    value = int(section[key])
    if value < minimum or (maximum is not None and value > maximum):
        suffix = (
            f" between {minimum} and {maximum}"
            if maximum is not None
            else f" greater than or equal to {minimum}"
        )
        raise ObservationMonitorSettingsError(f"{field_name}.{key} must be{suffix}")


def _validate_ranges(
    *,
    gmail: Mapping[str, Any],
    arxiv: Mapping[str, Any],
) -> None:
    _bounded_integer(
        gmail,
        "poll_interval_seconds",
        minimum=1,
        field_name="gmail_email",
    )
    _bounded_integer(
        gmail,
        "retry_backoff_seconds",
        minimum=10,
        field_name="gmail_email",
    )
    for key in ("max_history_pages_per_beat", "max_messages_per_beat"):
        _bounded_integer(gmail, key, minimum=1, field_name="gmail_email")
    for key in ("subject_max_chars", "snippet_max_chars"):
        _bounded_integer(gmail, key, minimum=0, field_name="gmail_email")

    _bounded_integer(
        arxiv,
        "poll_interval_seconds",
        minimum=60,
        field_name="arxiv",
    )
    _bounded_integer(
        arxiv,
        "retry_backoff_seconds",
        minimum=30,
        field_name="arxiv",
    )
    if "request_timeout_seconds" in arxiv:
        timeout = float(arxiv["request_timeout_seconds"])
        if not math.isfinite(timeout) or timeout < 0.1 or timeout > 30.0:
            raise ObservationMonitorSettingsError(
                "arxiv.request_timeout_seconds must be between 0.1 and 30"
            )
    _bounded_integer(
        arxiv,
        "max_response_bytes",
        minimum=1_024,
        field_name="arxiv",
    )
    _bounded_integer(
        arxiv,
        "max_feed_items",
        minimum=1,
        maximum=2_000,
        field_name="arxiv",
    )
    _bounded_integer(
        arxiv,
        "max_deliveries_per_beat",
        minimum=1,
        maximum=200,
        field_name="arxiv",
    )
    _bounded_integer(
        arxiv,
        "max_pending_per_owner",
        minimum=1,
        maximum=100_000,
        field_name="arxiv",
    )
    _bounded_integer(
        arxiv,
        "seen_revision_limit",
        minimum=100,
        field_name="arxiv",
    )
    max_feed = int(arxiv.get("max_feed_items", 2_000))
    seen = int(arxiv.get("seen_revision_limit", 10_000))
    max_deliveries = int(arxiv.get("max_deliveries_per_beat", 20))
    max_pending = int(arxiv.get("max_pending_per_owner", 2_000))
    if seen < max_feed:
        raise ObservationMonitorSettingsError(
            "arxiv.seen_revision_limit must be greater than or equal to "
            "arxiv.max_feed_items"
        )
    if max_pending < max_deliveries:
        raise ObservationMonitorSettingsError(
            "arxiv.max_pending_per_owner must be greater than or equal to "
            "arxiv.max_deliveries_per_beat"
        )


def _validate_patch(payload: Mapping[str, Any]) -> None:
    _reject_unknown(
        payload,
        frozenset({"gmail_email", "arxiv"}),
        field_name="observation monitor config",
    )
    if "gmail_email" in payload:
        gmail = _mapping(payload["gmail_email"], field_name="gmail_email")
        _reject_unknown(
            gmail,
            _GMAIL_PUBLIC_PATCH_FIELDS,
            field_name="gmail_email",
        )
        _validate_scalar_fields(gmail, field_name="gmail_email")
    if "arxiv" in payload:
        arxiv = _mapping(payload["arxiv"], field_name="arxiv")
        _reject_unknown(
            arxiv,
            _ARXIV_PUBLIC_PATCH_FIELDS,
            field_name="arxiv",
        )
        _validate_scalar_fields(arxiv, field_name="arxiv")
        if "subscriptions" in arxiv:
            subscriptions = arxiv["subscriptions"]
            if not isinstance(subscriptions, list):
                raise ObservationMonitorSettingsError(
                    "arxiv.subscriptions must be an array"
                )
            for index, raw in enumerate(subscriptions):
                subscription = _mapping(
                    raw,
                    field_name=f"arxiv.subscriptions[{index}]",
                )
                _reject_unknown(
                    subscription,
                    _ARXIV_SUBSCRIPTION_PUBLIC_PATCH_FIELDS,
                    field_name=f"arxiv.subscriptions[{index}]",
                )


def _merge_and_scope(
    current: Mapping[str, Any],
    patch: Mapping[str, Any],
    *,
    owner_id: str,
) -> Dict[str, Any]:
    merged = deepcopy(dict(current))
    for section_name in ("gmail_email", "arxiv"):
        if section_name not in patch:
            continue
        prior = _mapping(
            merged.get(section_name, {}),
            field_name=section_name,
        )
        prior.update(deepcopy(dict(patch[section_name])))
        merged[section_name] = prior

    gmail = merged.get("gmail_email")
    if "gmail_email" in patch and isinstance(gmail, Mapping):
        gmail = dict(gmail)
        gmail["owner_ids"] = [owner_id]
        merged["gmail_email"] = gmail
    arxiv = merged.get("arxiv")
    if "arxiv" in patch and isinstance(arxiv, Mapping):
        arxiv = dict(arxiv)
        subscriptions = arxiv.get("subscriptions")
        if isinstance(subscriptions, list):
            scoped = []
            for raw in subscriptions:
                subscription = dict(raw)
                subscription["owner_id"] = owner_id
                scoped.append(subscription)
            arxiv["subscriptions"] = scoped
        merged["arxiv"] = arxiv
    return merged


def public_observation_monitor_config(
    payload: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return settings safe for UI display and round-tripping."""

    public: Dict[str, Any] = {}
    raw_gmail = payload.get("gmail_email")
    if isinstance(raw_gmail, Mapping):
        public["gmail_email"] = {
            key: deepcopy(value)
            for key, value in raw_gmail.items()
            if str(key) in _GMAIL_PUBLIC_PATCH_FIELDS
        }
    raw_arxiv = payload.get("arxiv")
    if isinstance(raw_arxiv, Mapping):
        arxiv = {
            key: deepcopy(value)
            for key, value in raw_arxiv.items()
            if str(key) in _ARXIV_PUBLIC_PATCH_FIELDS
        }
        subscriptions = arxiv.get("subscriptions")
        if isinstance(subscriptions, list):
            arxiv["subscriptions"] = [
                {
                    key: deepcopy(value)
                    for key, value in subscription.items()
                    if str(key) in _ARXIV_SUBSCRIPTION_PUBLIC_PATCH_FIELDS
                }
                for subscription in subscriptions
                if isinstance(subscription, Mapping)
            ]
        public["arxiv"] = arxiv
    return public


class ObservationMonitorSettingsManager:
    """CAS-backed YAML settings plus live Heartbeat group reconciliation."""

    def __init__(
        self,
        *,
        config_path: str | Path,
        coordinator: ScheduleHeartbeatCoordinator,
        live_apply_enabled: bool,
        initial_config_applied: bool = False,
    ) -> None:
        self.config_path = resolve_config_path(config_path)
        self.coordinator = coordinator
        self.live_apply_enabled = bool(live_apply_enabled)
        self._lock = threading.RLock()
        current = self._load()
        self._effective_revision: Optional[str] = (
            _revision(current)
            if self.live_apply_enabled and initial_config_applied
            else None
        )

    def _load(self) -> Dict[str, Any]:
        try:
            payload = load_observation_monitor_config(
                self.config_path,
                missing_ok=True,
            )
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise ObservationMonitorSettingsError(
                f"failed to load observation monitor settings: {exc}",
                status_code=500,
            ) from exc
        return deepcopy(dict(payload))

    def _health(self) -> Dict[str, Any]:
        return self.coordinator.monitor_registry.health_payload()

    def _effective(self, *, desired_revision: str) -> Dict[str, Any]:
        monitor_ids = [
            monitor_id
            for monitor_id in self.coordinator.monitor_registry.names()
            if monitor_id in MANAGED_MONITOR_IDS
        ]
        return {
            "revision": self._effective_revision,
            "monitor_ids": monitor_ids,
            "live_apply_enabled": self.live_apply_enabled,
            "restart_required": (
                not self.live_apply_enabled
                or self._effective_revision != desired_revision
            ),
        }

    def get(self) -> Dict[str, Any]:
        with self._lock:
            current = self._load()
            current_revision = _revision(current)
            return {
                "config": public_observation_monitor_config(current),
                "revision": current_revision,
                "effective": self._effective(
                    desired_revision=current_revision,
                ),
                "health": self._health(),
            }

    def put(
        self,
        *,
        patch: Mapping[str, Any],
        expected_revision: str,
        owner_id: str,
    ) -> Dict[str, Any]:
        normalized_owner = str(owner_id or "").strip()
        if not normalized_owner:
            raise ObservationMonitorSettingsError("authenticated owner is required")
        if not isinstance(patch, Mapping):
            raise ObservationMonitorSettingsError("config must be an object")
        expected = str(expected_revision or "").strip()
        if not expected:
            raise ObservationMonitorSettingsError(
                "expected_revision or If-Match is required",
                status_code=428,
            )

        with self._lock:
            current = self._load()
            current_revision = _revision(current)
            if expected != current_revision:
                raise ObservationMonitorSettingsError(
                    "observation monitor settings revision conflict",
                    status_code=409,
                    extra={"current_revision": current_revision},
                )
            _validate_patch(patch)
            candidate = _merge_and_scope(
                current,
                patch,
                owner_id=normalized_owner,
            )
            _validate_full_config(candidate)
            try:
                monitors = build_observation_monitors_from_mapping(candidate)
            except (TypeError, ValueError, ImportError) as exc:
                raise ObservationMonitorSettingsError(
                    f"invalid observation monitor settings: {exc}"
                ) from exc
            candidate_revision = _revision(candidate)
            changed = candidate_revision != current_revision
            reconcile_required = (
                self.live_apply_enabled
                and self._effective_revision != candidate_revision
            )
            if not changed and not reconcile_required:
                return self._result(
                    candidate,
                    revision=candidate_revision,
                    changed=False,
                    application_status=(
                        "applied"
                        if self.live_apply_enabled
                        else "restart_required"
                    ),
                )

            def commit() -> None:
                latest_revision = _revision(self._load())
                if latest_revision != current_revision:
                    raise ObservationMonitorSettingsError(
                        "observation monitor settings changed during update",
                        status_code=409,
                        extra={"current_revision": latest_revision},
                    )
                if changed:
                    self._atomic_write(candidate)

            try:
                if self.live_apply_enabled:
                    monitor_ids = self.coordinator.reconcile_monitor_group(
                        managed_ids=MANAGED_MONITOR_IDS,
                        monitors=monitors,
                        commit=commit,
                    )
                    self._effective_revision = candidate_revision
                    status = "applied"
                else:
                    commit()
                    monitor_ids = tuple(
                        monitor_id
                        for monitor_id in self.coordinator.monitor_registry.names()
                        if monitor_id in MANAGED_MONITOR_IDS
                    )
                    status = "restart_required"
            except ObservationMonitorSettingsError:
                raise
            except Exception as exc:
                raise ObservationMonitorSettingsError(
                    f"failed to apply observation monitor settings: {exc}",
                    status_code=500,
                ) from exc
            return self._result(
                candidate,
                revision=candidate_revision,
                changed=changed,
                application_status=status,
                monitor_ids=monitor_ids,
            )

    def _result(
        self,
        payload: Mapping[str, Any],
        *,
        revision: str,
        changed: bool,
        application_status: str,
        monitor_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        effective = self._effective(desired_revision=revision)
        if monitor_ids is not None and self.live_apply_enabled:
            effective["monitor_ids"] = list(monitor_ids)
        return {
            "config": public_observation_monitor_config(payload),
            "revision": revision,
            "changed": bool(changed),
            "persisted": True,
            "application": {
                "status": application_status,
                "monitor_ids": list(effective["monitor_ids"]),
                "restart_required": application_status == "restart_required",
            },
            "effective": effective,
            "health": self._health(),
        }

    def _atomic_write(self, payload: Mapping[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = yaml.safe_dump(
            dict(payload),
            allow_unicode=True,
            sort_keys=False,
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.config_path.name}.",
            suffix=".tmp",
            dir=str(self.config_path.parent),
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.config_path)
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


__all__ = [
    "MANAGED_MONITOR_IDS",
    "ObservationMonitorSettingsError",
    "ObservationMonitorSettingsManager",
    "public_observation_monitor_config",
]
