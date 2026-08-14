"""Configuration-only composition for Heartbeat observation monitors.

The FastAPI application factory stays side-effect free: only the production
CLI calls :func:`build_observation_monitors`, and only sections explicitly
enabled in YAML construct a source client.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence, Tuple

import yaml

from m_agent.config_paths import (
    OBSERVATION_MONITORS_CONFIG_PATH,
    resolve_config_path,
)
from m_agent.paths import data_root_dir

from .heartbeat import HeartbeatMonitor


def _mapping(value: object, *, field_name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _strings(value: object, *, field_name: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise ValueError(f"{field_name} must be a list of strings")
    if not all(isinstance(raw, str) for raw in value):
        raise ValueError(f"{field_name} must contain only strings")
    return tuple(item for item in (raw.strip() for raw in value) if item)


def _bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    text = str(value or "").strip().casefold()
    if text in {"true", "yes", "on", "1"}:
        return True
    if text in {"false", "no", "off", "0"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _state_path(raw: object, *, default_name: str) -> Path:
    text = str(raw or "").strip()
    if not text:
        return data_root_dir() / "observation_monitors" / default_name
    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    root = data_root_dir().resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("relative monitor state_path escapes data root") from exc
    return resolved


def load_observation_monitor_config(
    config_path: str | Path = OBSERVATION_MONITORS_CONFIG_PATH,
    *,
    missing_ok: bool = False,
) -> Mapping[str, Any]:
    """Load the opt-in source configuration without constructing clients."""

    path = resolve_config_path(config_path)
    if not path.exists():
        if missing_ok:
            return {}
        raise FileNotFoundError(
            f"observation monitor config not found: {path}"
        )
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, Mapping):
        raise ValueError("observation monitor config root must be a mapping")
    return payload


def build_observation_monitors(
    config_path: str | Path = OBSERVATION_MONITORS_CONFIG_PATH,
    *,
    missing_ok: bool = False,
) -> Tuple[HeartbeatMonitor, ...]:
    """Construct only the explicitly enabled Heartbeat source monitors."""

    payload = load_observation_monitor_config(
        config_path,
        missing_ok=missing_ok,
    )
    return build_observation_monitors_from_mapping(payload)


def build_observation_monitors_from_mapping(
    payload: Mapping[str, Any],
) -> Tuple[HeartbeatMonitor, ...]:
    """Construct monitors from an already loaded configuration mapping.

    This is shared by startup composition and the settings reconciler so both
    paths validate and normalize monitor configuration identically.
    """

    if not isinstance(payload, Mapping):
        raise ValueError("observation monitor config root must be a mapping")
    monitors: list[HeartbeatMonitor] = []

    gmail_raw = _mapping(
        payload.get("gmail_email"),
        field_name="gmail_email",
    )
    if _bool(gmail_raw.get("enabled"), default=False):
        # Lazy import keeps Gmail's optional dependencies off the normal
        # server startup path when this monitor is disabled.
        from m_agent.integrations.gmail_observation_monitor import (
            GmailObservationMonitorConfig,
            GmailObservationTriggerMonitor,
        )

        owner_ids = _strings(
            gmail_raw.get("owner_ids"),
            field_name="gmail_email.owner_ids",
        )
        if len(owner_ids) != 1:
            raise ValueError(
                "gmail_email requires exactly one explicit owner_id"
            )
        gmail_config = GmailObservationMonitorConfig(
            enabled=True,
            owner_ids=owner_ids,
            poll_interval_seconds=int(
                gmail_raw.get("poll_interval_seconds", 60)
            ),
            retry_backoff_seconds=int(
                gmail_raw.get("retry_backoff_seconds", 300)
            ),
            state_path=_state_path(
                gmail_raw.get("state_path"),
                default_name="gmail_email.json",
            ),
            label_id=str(gmail_raw.get("label_id", "INBOX") or "").strip(),
            max_history_pages_per_beat=int(
                gmail_raw.get("max_history_pages_per_beat", 2)
            ),
            max_messages_per_beat=int(
                gmail_raw.get("max_messages_per_beat", 20)
            ),
            include_snippet=_bool(
                gmail_raw.get("include_snippet"),
                default=False,
            ),
            subject_max_chars=int(
                gmail_raw.get("subject_max_chars", 160)
            ),
            snippet_max_chars=int(
                gmail_raw.get("snippet_max_chars", 240)
            ),
            subject_keywords=_strings(
                gmail_raw.get("subject_keywords"),
                field_name="gmail_email.subject_keywords",
            ),
            sender_allowlist=_strings(
                gmail_raw.get("sender_allowlist"),
                field_name="gmail_email.sender_allowlist",
            ),
            sender_denylist=_strings(
                gmail_raw.get("sender_denylist"),
                field_name="gmail_email.sender_denylist",
            ),
        )
        monitors.append(GmailObservationTriggerMonitor(gmail_config))

    arxiv_raw = _mapping(payload.get("arxiv"), field_name="arxiv")
    if _bool(arxiv_raw.get("enabled"), default=False):
        from m_agent.integrations.arxiv_feed import UrllibArxivFeedClient

        from .arxiv_heartbeat import (
            ArxivMonitorConfig,
            ArxivRssMonitor,
            ArxivSubscription,
            JsonArxivCheckpointStore,
        )

        subscriptions_raw = arxiv_raw.get("subscriptions")
        if not isinstance(subscriptions_raw, Sequence) or isinstance(
            subscriptions_raw,
            (str, bytes, bytearray),
        ):
            raise ValueError("arxiv.subscriptions must be a list")
        subscriptions = []
        for index, raw_subscription in enumerate(subscriptions_raw):
            subscription = _mapping(
                raw_subscription,
                field_name=f"arxiv.subscriptions[{index}]",
            )
            subscriptions.append(
                ArxivSubscription(
                    subscription_id=str(
                        subscription.get("subscription_id", "") or ""
                    ),
                    owner_id=str(subscription.get("owner_id", "") or ""),
                    categories=_strings(
                        subscription.get("categories"),
                        field_name=(
                            f"arxiv.subscriptions[{index}].categories"
                        ),
                    ),
                    keywords=_strings(
                        subscription.get("keywords"),
                        field_name=f"arxiv.subscriptions[{index}].keywords",
                    ),
                    exclude_keywords=_strings(
                        subscription.get("exclude_keywords"),
                        field_name=(
                            "arxiv.subscriptions"
                            f"[{index}].exclude_keywords"
                        ),
                    ),
                    announce_types=_strings(
                        subscription.get(
                            "announce_types",
                            ("new", "replace", "replace-cross"),
                        ),
                        field_name=(
                            f"arxiv.subscriptions[{index}].announce_types"
                        ),
                    ),
                    enabled=_bool(subscription.get("enabled"), default=True),
                )
            )
        if not subscriptions:
            raise ValueError(
                "arxiv monitor is enabled but has no subscriptions"
            )

        monitor_config = ArxivMonitorConfig(
            poll_interval_seconds=int(
                arxiv_raw.get("poll_interval_seconds", 21_600)
            ),
            retry_backoff_seconds=int(
                arxiv_raw.get("retry_backoff_seconds", 900)
            ),
            request_timeout_seconds=float(
                arxiv_raw.get("request_timeout_seconds", 5.0)
            ),
            max_response_bytes=int(
                arxiv_raw.get("max_response_bytes", 4_000_000)
            ),
            max_feed_items=int(arxiv_raw.get("max_feed_items", 2_000)),
            max_deliveries_per_beat=int(
                arxiv_raw.get("max_deliveries_per_beat", 20)
            ),
            max_pending_per_owner=int(
                arxiv_raw.get("max_pending_per_owner", 2_000)
            ),
            seen_revision_limit=int(
                arxiv_raw.get("seen_revision_limit", 10_000)
            ),
            schedule_drainer=_bool(
                arxiv_raw.get("schedule_drainer"),
                default=True,
            ),
        )
        client = UrllibArxivFeedClient(
            feed_base_url=str(
                arxiv_raw.get(
                    "feed_base_url",
                    "https://rss.arxiv.org/atom",
                )
                or ""
            ).strip(),
            user_agent=str(
                arxiv_raw.get("user_agent", "M-Agent arXiv observer") or ""
            ).strip(),
        )
        monitors.append(
            ArxivRssMonitor(
                subscriptions=tuple(subscriptions),
                checkpoint_store=JsonArxivCheckpointStore(
                    _state_path(
                        arxiv_raw.get("state_path"),
                        default_name="arxiv.json",
                    )
                ),
                client=client,
                config=monitor_config,
            )
        )

    return tuple(monitors)


__all__ = [
    "build_observation_monitors",
    "build_observation_monitors_from_mapping",
    "load_observation_monitor_config",
]
