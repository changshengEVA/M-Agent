"""Transaction-aware user interaction view for sourceless matching."""
from __future__ import annotations

import json
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set

from m_agent.runtime.think_life.contracts import (
    SceneActor,
    SceneEntry,
    SceneEntryType,
    StimulusEnvelope,
)


def format_transaction_scene_view(
    entries: Sequence[SceneEntry],
    *,
    candidate_refs_by_transaction_id: Mapping[str, str],
    deprecated_transaction_ids: Iterable[str] = (),
    current_stimulus: Optional[StimulusEnvelope] = None,
    max_chars: int = 8000,
    max_entry_chars: int = 1200,
) -> str:
    """Render user-visible interaction history with local transaction refs.

    Durable transaction ids are used only for the local mapping and never
    rendered. Candidate transactions retain their ``candidate_N`` refs;
    deleted transactions receive first-seen ``deprecated_N`` refs;
    other transaction-bound non-candidates receive first-seen ``context_N``
    refs; entries without a transaction binding are explicitly marked
    ``unbound``.

    Only user utterances and assistant replies belong to this view. Internal
    thoughts, actions, outcomes, execution feedback, and other runtime process
    events are deliberately excluded even when they carry a transaction id.

    The current sourceless stimulus is not in Scene yet, so callers may append
    it as ``CURRENT`` with the unresolved ``tx=?`` marker.
    """

    local_candidate_refs = {
        str(transaction_id or "").strip(): str(ref or "").strip()
        for transaction_id, ref in candidate_refs_by_transaction_id.items()
        if str(transaction_id or "").strip() and str(ref or "").strip()
    }
    local_deprecated_ids = {
        str(transaction_id or "").strip()
        for transaction_id in deprecated_transaction_ids
        if str(transaction_id or "").strip()
    }
    # Scene seq is the canonical append order. The original position is a
    # stable tie-breaker for legacy or synthetic entries with duplicate seq.
    ordered_entries = [
        entry
        for _, entry in sorted(
            enumerate(entries),
            key=lambda pair: (int(pair[1].seq), pair[0]),
        )
        if is_user_visible_scene_interaction(entry)
    ]

    deprecated_refs: Dict[str, str] = {}
    context_refs: Dict[str, str] = {}
    history_rows: List[str] = []
    for entry in ordered_entries:
        transaction_ref = _transaction_ref(
            entry.transaction_id,
            candidate_refs=local_candidate_refs,
            deprecated_ids=local_deprecated_ids,
            deprecated_refs=deprecated_refs,
            context_refs=context_refs,
        )
        metadata = [
            f"#{max(0, int(entry.seq)):05d}",
            f"at={str(entry.occurred_at or '-').strip() or '-'}",
            f"tx={transaction_ref}",
            f"{entry.actor.value}/{entry.entry_type.value}",
        ]
        history_rows.append(
            " | ".join(metadata)
            + f" | text={_quoted(entry.text, max_entry_chars)}"
        )

    current_row = ""
    if current_stimulus is not None:
        current_row = " | ".join(
            [
                "CURRENT",
                f"at={str(current_stimulus.occurred_at or '-').strip() or '-'}",
                "tx=?",
                f"stimulus/{current_stimulus.kind.value}",
                f"text={_quoted(current_stimulus.text, max_entry_chars)}",
            ]
        )

    if not history_rows and not current_row:
        return ""

    header = [
        "[Transaction-aware user interaction | oldest -> newest]",
        (
            "[Legend] candidate_N=selectable; deprecated_N=deleted historical "
            "context (not selectable/restorable); context_N=history; "
            "unbound=no tx; ?=current"
        ),
    ]
    return _fit_latest_rows(
        header=header,
        history_rows=history_rows,
        current_row=current_row,
        max_chars=max_chars,
    )


def is_user_visible_scene_interaction(entry: SceneEntry) -> bool:
    """Return whether a Scene entry is part of the user-visible dialogue."""

    return (
        entry.actor == SceneActor.USER
        and entry.entry_type == SceneEntryType.UTTERANCE
    ) or (
        entry.actor == SceneActor.ASSISTANT
        and entry.entry_type == SceneEntryType.REPLY
    )


def _transaction_ref(
    transaction_id: Optional[str],
    *,
    candidate_refs: Mapping[str, str],
    deprecated_ids: Set[str],
    deprecated_refs: Dict[str, str],
    context_refs: Dict[str, str],
) -> str:
    durable_id = str(transaction_id or "").strip()
    if not durable_id:
        return "unbound"
    # A tombstone is historical context even if a stale caller accidentally
    # also supplied a candidate ref for the same durable id.
    if durable_id in deprecated_ids:
        deprecated_ref = deprecated_refs.get(durable_id)
        if deprecated_ref:
            return deprecated_ref
        deprecated_ref = f"deprecated_{len(deprecated_refs) + 1}"
        deprecated_refs[durable_id] = deprecated_ref
        return deprecated_ref
    candidate_ref = candidate_refs.get(durable_id)
    if candidate_ref:
        return candidate_ref
    context_ref = context_refs.get(durable_id)
    if context_ref:
        return context_ref
    context_ref = f"context_{len(context_refs) + 1}"
    context_refs[durable_id] = context_ref
    return context_ref


def _quoted(value: object, max_chars: int) -> str:
    text = str(value or "")
    limit = max(1, int(max_chars or 1))
    if len(text) > limit:
        text = text[: max(0, limit - 1)] + "…"
    return json.dumps(text, ensure_ascii=False)


def _fit_latest_rows(
    *,
    header: Sequence[str],
    history_rows: Sequence[str],
    current_row: str,
    max_chars: int,
) -> str:
    """Fit complete newest rows, then restore their chronological order."""

    cap = max(1, int(max_chars or 1))

    def render(start: int) -> str:
        body = list(header)
        if start:
            body.append(f"[... {start} older Scene entries omitted ...]")
        body.extend(history_rows[start:])
        if current_row:
            body.append(current_row)
        return "\n".join(body)

    # Start with only the newest historical row and grow backwards. This
    # ensures truncation never keeps old context at the expense of the latest
    # question/reply that is most useful for attribution.
    best = render(len(history_rows))
    for candidate_start in range(len(history_rows) - 1, -1, -1):
        candidate = render(candidate_start)
        if len(candidate) > cap:
            break
        best = candidate

    if len(best) <= cap:
        return best
    # Extremely small caller budgets may not fit even the header/current row.
    # Keep the beginning, which preserves the view contract and local labels.
    return best[:cap]
