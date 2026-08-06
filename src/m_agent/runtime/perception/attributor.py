"""Map stimuli to transaction lines."""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from m_agent.runtime.config import RuntimeConfig
from m_agent.runtime.domain.contracts import (
    SceneEntry,
    StimulusEnvelope,
    StimulusKind,
    TransactionKind,
    TransactionLifecycle,
    TransactionRecord,
    TransactionState,
)
from m_agent.runtime.perception.matcher_scene_view import (
    format_transaction_scene_view,
)
from m_agent.runtime.transaction.predicates import (
    is_match_candidate as is_domain_match_candidate,
    is_runnable_record,
    is_waiting_delegate,
)
from m_agent.runtime.transaction.registry import TransactionRegistry


# Resolver returns a turn-local ``candidate_N`` ref, a durable transaction id
# (compat), or ``None``/empty to create.  Inputs are turn-local views only.
SemanticResolver = Callable[..., Optional[str]]


def is_match_candidate(record: TransactionRecord) -> bool:
    """Return whether a transaction is eligible for sourceless matching.

    Target set: current-conversation ``pause`` and unflushed ``complete``.
    ``continue``/running, archive, and deleted records are excluded.
    """

    return is_domain_match_candidate(record)


def build_match_candidate_views(
    candidates: Sequence[TransactionRecord],
) -> List[Dict[str, Any]]:
    """Build selectable turn-local refs without semantic task summaries."""

    return [
        {"ref": f"candidate_{index}"}
        for index, _record in enumerate(candidates, start=1)
    ]


class TransactionAttributor:
    def __init__(
        self,
        *,
        registry: TransactionRegistry,
        config: RuntimeConfig,
        semantic_resolver: Optional[SemanticResolver] = None,
        scene_writer: Optional[Any] = None,
    ) -> None:
        self.registry = registry
        self.config = config
        self.semantic_resolver = semantic_resolver
        self.scene_writer = scene_writer

    def list_match_candidates(
        self,
        conversation_id: str,
    ) -> List[TransactionRecord]:
        cid = str(conversation_id or "").strip()
        return [
            record
            for record in self.registry.list_for_conversation(cid)
            if is_match_candidate(record)
        ]

    def resolve(
        self,
        stimulus: StimulusEnvelope,
        *,
        dialogue_history: Optional[List[dict]] = None,
        scene_context: str = "",
        scene_entries: Optional[Sequence[SceneEntry]] = None,
    ) -> Tuple[TransactionRecord, bool]:
        """Return (transaction, created_new)."""
        if stimulus.kind == StimulusKind.EXECUTION_FEEDBACK:
            return self._resolve_feedback(stimulus)

        if stimulus.kind == StimulusKind.SCHEDULED_PLAN:
            return self._resolve_scheduled_plan(stimulus)

        explicit_transaction_id = str(stimulus.transaction_id or "").strip()
        if explicit_transaction_id:
            return self._resolve_explicit_source(stimulus)

        return self._resolve_sourceless(
            stimulus,
            dialogue_history=dialogue_history,
            scene_context=scene_context,
            scene_entries=scene_entries,
        )

    def _resolve_scheduled_plan(
        self,
        stimulus: StimulusEnvelope,
    ) -> Tuple[TransactionRecord, bool]:
        """Resolve both transaction-bound and external schedule deliveries.

        A persisted transaction schedule run is the authority for attribution,
        even when the delivery envelope omitted ``transaction_id``. External
        heartbeat runs have no row in the transaction schedule store and keep
        creating their own ``SCHEDULE`` transaction on first admission.
        """

        explicit_transaction_id = str(stimulus.transaction_id or "").strip()
        run_id = str(
            stimulus.schedule_run_id
            or stimulus.payload.get("schedule_run_id", "")
            or stimulus.payload.get("run_id", "")
            or ""
        ).strip()
        store = getattr(self.registry, "store", None)
        persisted = (
            store.load_schedule_run(run_id)
            if run_id
            and store is not None
            and hasattr(store, "load_schedule_run")
            else None
        )
        if persisted is not None:
            bound_transaction_id = str(
                persisted.get("transaction_id", "") or ""
            ).strip()
            if not bound_transaction_id:
                raise ValueError(
                    "scheduled_plan run has no bound transaction: "
                    f"{run_id}"
                )
            if (
                explicit_transaction_id
                and explicit_transaction_id != bound_transaction_id
            ):
                raise ValueError(
                    "scheduled_plan run is bound to another transaction"
                )
            record = self.registry.get(bound_transaction_id)
            if record is None:
                raise ValueError(
                    "scheduled_plan run references an unknown transaction: "
                    f"{bound_transaction_id}"
                )
            return self._resolve_scheduled_record(stimulus, record)

        if explicit_transaction_id:
            return self._resolve_explicit_source(stimulus)
        return self._resolve_heartbeat(stimulus)

    def _resolve_explicit_source(
        self,
        stimulus: StimulusEnvelope,
    ) -> Tuple[TransactionRecord, bool]:
        explicit_transaction_id = str(stimulus.transaction_id or "").strip()
        record = self.registry.get(explicit_transaction_id)
        if record is None:
            raise ValueError(
                "explicit stimulus transaction_id is unknown: "
                f"{explicit_transaction_id}"
            )
        if record.conversation_id != stimulus.conversation_id:
            raise ValueError(
                "explicit stimulus transaction belongs to another "
                "conversation"
            )
        if record.lifecycle_status != TransactionLifecycle.ACTIVE:
            raise ValueError(
                "explicit stimulus transaction is not active: "
                f"{explicit_transaction_id}"
            )

        if is_waiting_delegate(record):
            raise ValueError(
                "transaction is waiting for its matching execution_feedback"
            )

        if stimulus.kind == StimulusKind.SCHEDULED_PLAN:
            return self._resolve_scheduled_record(stimulus, record)

        if record.state in {
            TransactionState.PAUSE,
            TransactionState.COMPLETE,
            TransactionState.ARCHIVE,
        }:
            # Sourced reentry / explicit control continues the locked line.
            # Archive restore is only legal for explicit restore/schedule
            # paths; plain user reentry with an attributed id still locks.
            if record.state == TransactionState.ARCHIVE:
                raise ValueError(
                    "archived transaction requires explicit restore control"
                )
            return self._reactivate_if_needed(
                record,
                source="attributed_reentry",
            ), False
        return record, False

    def _resolve_scheduled_record(
        self,
        stimulus: StimulusEnvelope,
        record: TransactionRecord,
    ) -> Tuple[TransactionRecord, bool]:
        if record.conversation_id != stimulus.conversation_id:
            raise ValueError(
                "scheduled_plan transaction belongs to another conversation"
            )
        if record.lifecycle_status != TransactionLifecycle.ACTIVE:
            raise ValueError(
                "scheduled_plan transaction is not active: "
                f"{record.transaction_id}"
            )
        if is_waiting_delegate(record):
            raise ValueError(
                "transaction is waiting for its matching execution_feedback"
            )

        self._validate_schedule_source(stimulus, record)
        record = self._register_pending_schedule_for_stimulus(
            stimulus,
            record,
        )
        self._ensure_schedule_run_for_stimulus(stimulus, record)
        if self._is_duplicate_schedule_delivery(stimulus, record):
            return (
                self.registry.get(record.transaction_id) or record
            ), False
        claimed = self._claim_schedule_if_available(stimulus, record)
        if claimed is not None:
            self._mark_schedule_delivery_observed(stimulus, record)
            return claimed, False
        reactivated = self._reactivate_if_needed(
            record,
            source="schedule_delivery",
        )
        self._mark_schedule_delivery_observed(stimulus, record)
        return reactivated, False

    def _validate_schedule_source(
        self,
        stimulus: StimulusEnvelope,
        record: TransactionRecord,
    ) -> None:
        run_id = str(
            stimulus.schedule_run_id
            or stimulus.payload.get("run_id", "")
            or ""
        ).strip()
        delivery_id = str(
            stimulus.schedule_delivery_id
            or stimulus.payload.get("delivery_id", "")
            or ""
        ).strip()
        if not run_id or not delivery_id:
            raise ValueError(
                "scheduled_plan source requires schedule_run_id and "
                "schedule_delivery_id"
            )
        store = getattr(self.registry, "store", None)
        if store is None or not hasattr(store, "load_schedule_run"):
            return
        persisted = store.load_schedule_run(run_id)
        if persisted is None:
            # Acceptance and early schedule wiring may present a pre-validated
            # explicit transaction without a persisted run row yet.
            return
        bound_tx = str(persisted.get("transaction_id", "") or "").strip()
        if bound_tx and bound_tx != record.transaction_id:
            raise ValueError(
                "scheduled_plan run is bound to another transaction"
            )

    @staticmethod
    def _schedule_due_at(stimulus: StimulusEnvelope) -> Optional[str]:
        payload = (
            stimulus.payload
            if isinstance(stimulus.payload, dict)
            else {}
        )
        activation = (
            payload.get("activation")
            if isinstance(payload.get("activation"), dict)
            else {}
        )
        event = (
            activation.get("event")
            if isinstance(activation.get("event"), dict)
            else {}
        )
        facts = (
            event.get("facts")
            if isinstance(event.get("facts"), dict)
            else {}
        )
        return str(
            payload.get("due_at_utc", "")
            or payload.get("due_at", "")
            or payload.get("scheduled_at", "")
            or facts.get("due_at_utc", "")
            or ""
        ).strip() or None

    def _register_pending_schedule_for_stimulus(
        self,
        stimulus: StimulusEnvelope,
        record: TransactionRecord,
    ) -> TransactionRecord:
        """Linearize an early delivery with its pending schedule intent.

        A short-delay heartbeat may arrive after schedule tool Feedback but
        before the next silent planning turn has registered the intent.  Close
        the old activation and establish ``scheduled_wait`` before claiming
        that delivery, so the scheduled work owns a fresh activation.
        """

        schedule_id = str(
            stimulus.schedule_id
            or stimulus.payload.get("schedule_id", "")
            or ""
        ).strip()
        pending_intents = list(record.pending_schedule_intents)
        intent = next(
            (
                item
                for item in pending_intents
                if str(item.get("schedule_id", "") or "").strip()
                == schedule_id
            ),
            None,
        )
        if intent is None:
            return record

        run_id = str(
            stimulus.schedule_run_id
            or stimulus.payload.get("schedule_run_id", "")
            or stimulus.payload.get("run_id", "")
            or ""
        ).strip()
        if not run_id:
            raise ValueError(
                "pending scheduled_plan requires schedule_run_id"
            )
        intent_due_at = str(intent.get("due_at", "") or "").strip()
        if not intent_due_at:
            raise ValueError("pending schedule intent has no due_at")
        from m_agent.runtime.transaction import (
            TransactionScheduleCoordinator,
            stable_schedule_run_id,
        )

        expected_run_id = stable_schedule_run_id(
            schedule_id,
            record.transaction_id,
            intent_due_at,
        )
        if run_id != expected_run_id:
            raise ValueError("scheduled_plan run_id binding mismatch")
        delivered_due_at = self._schedule_due_at(stimulus)
        if delivered_due_at and delivered_due_at != intent_due_at:
            raise ValueError("scheduled_plan due_at binding mismatch")

        payload = (
            stimulus.payload
            if isinstance(stimulus.payload, dict)
            else {}
        )
        coordinator = TransactionScheduleCoordinator(self.registry.store)
        current = record
        # Register the whole stable intent snapshot before claiming the run.
        # Otherwise a second pending intent would be discovered while the
        # claimed activation is completing and incorrectly turn that active
        # schedule execution back into ``scheduled_wait``.
        ordered_intents = [
            item for item in pending_intents if item is not intent
        ] + [intent]
        for pending in ordered_intents:
            pending_schedule_id = str(
                pending.get("schedule_id", "") or ""
            ).strip()
            due_at = str(pending.get("due_at", "") or "").strip()
            if not due_at:
                raise ValueError("pending schedule intent has no due_at")
            owner_id = str(
                (
                    payload.get("owner_id", "")
                    if pending is intent
                    else ""
                )
                or pending.get("owner_id", "")
                or ""
            ).strip() or None
            result = coordinator.register(
                schedule_id=pending_schedule_id,
                schedule_run_id=run_id if pending is intent else None,
                transaction_id=record.transaction_id,
                due_at=due_at,
                expected_transaction_revision=int(current.revision),
                schedule_owner_id=owner_id,
            )
            current = (
                self.registry.refresh_from_store(record.transaction_id)
                or result.transaction
            )
        return current

    def _ensure_schedule_run_for_stimulus(
        self,
        stimulus: StimulusEnvelope,
        record: TransactionRecord,
    ) -> None:
        run_id = str(
            stimulus.schedule_run_id
            or stimulus.payload.get("run_id", "")
            or ""
        ).strip()
        schedule_id = str(
            stimulus.schedule_id
            or stimulus.payload.get("schedule_id", "")
            or ""
        ).strip()
        if not run_id or not schedule_id:
            return
        store = getattr(self.registry, "store", None)
        if store is None or not hasattr(store, "load_schedule_run"):
            return
        from m_agent.runtime.transaction import (
            TransactionScheduleCoordinator,
        )

        coordinator = TransactionScheduleCoordinator(store)
        coordinator.ensure_run(
            schedule_id=schedule_id,
            schedule_run_id=run_id,
            transaction_id=record.transaction_id,
            conversation_id=record.conversation_id,
            due_at=self._schedule_due_at(stimulus),
        )

    def _delivery_id_for_stimulus(
        self,
        stimulus: StimulusEnvelope,
    ) -> str:
        return str(
            stimulus.schedule_delivery_id
            or stimulus.payload.get("schedule_delivery_id", "")
            or stimulus.payload.get("delivery_id", "")
            or ""
        ).strip()

    def _is_duplicate_schedule_delivery(
        self,
        stimulus: StimulusEnvelope,
        record: TransactionRecord,
    ) -> bool:
        run_id = str(
            stimulus.schedule_run_id
            or stimulus.payload.get("run_id", "")
            or ""
        ).strip()
        delivery_id = self._delivery_id_for_stimulus(stimulus)
        if not run_id or not delivery_id:
            return False
        store = getattr(self.registry, "store", None)
        if store is None or not hasattr(store, "load_schedule_run"):
            return False
        persisted = store.load_schedule_run(run_id)
        if persisted is None:
            return False
        seen = str(
            persisted.get("schedule_delivery_id", "") or ""
        ).strip()
        status = str(persisted.get("status", "") or "").strip()
        return seen == delivery_id and status in {
            "claimed",
            "consumed",
            "scheduled",
            "due",
            "blocked_on_activation",
        }

    def _mark_schedule_delivery_observed(
        self,
        stimulus: StimulusEnvelope,
        record: TransactionRecord,
    ) -> None:
        run_id = str(
            stimulus.schedule_run_id
            or stimulus.payload.get("run_id", "")
            or ""
        ).strip()
        delivery_id = self._delivery_id_for_stimulus(stimulus)
        if not run_id or not delivery_id:
            return
        store = getattr(self.registry, "store", None)
        if store is None or not hasattr(store, "load_schedule_run"):
            return
        persisted = store.load_schedule_run(run_id)
        if persisted is None:
            return
        if str(persisted.get("schedule_delivery_id", "") or "").strip():
            return
        from m_agent.runtime.transaction import (
            TransactionScheduleCoordinator,
        )

        coordinator = TransactionScheduleCoordinator(store)
        try:
            coordinator.mark_delivery_observed(
                run_id,
                schedule_delivery_id=delivery_id,
                expected_run_revision=int(
                    persisted.get("revision", 1) or 1
                ),
            )
        except Exception:
            return

    def _claim_schedule_if_available(
        self,
        stimulus: StimulusEnvelope,
        record: TransactionRecord,
    ) -> Optional[TransactionRecord]:
        run_id = str(
            stimulus.schedule_run_id
            or stimulus.payload.get("run_id", "")
            or ""
        ).strip()
        store = getattr(self.registry, "store", None)
        if (
            not run_id
            or store is None
            or not hasattr(store, "load_schedule_run")
        ):
            return None
        persisted = store.load_schedule_run(run_id)
        if persisted is None:
            return None
        status = str(persisted.get("status", "") or "").strip()
        delivery_id = self._delivery_id_for_stimulus(stimulus)
        seen_delivery = str(
            persisted.get("schedule_delivery_id", "") or ""
        ).strip()
        if (
            delivery_id
            and seen_delivery == delivery_id
            and status in {"claimed", "consumed", "blocked_on_activation"}
        ):
            return self.registry.get(record.transaction_id) or record
        if status == "claimed":
            return self.registry.get(record.transaction_id) or record
        if status not in {"scheduled", "due"}:
            return None
        try:
            from m_agent.runtime.transaction import (
                TransactionScheduleCoordinator,
            )

            coordinator = TransactionScheduleCoordinator(store)
            result = coordinator.claim(
                run_id,
                expected_run_revision=int(
                    persisted.get("revision", 1) or 1
                ),
                expected_transaction_revision=int(record.revision),
                schedule_owner_id=str(
                    stimulus.payload.get("owner_id", "") or ""
                ).strip()
                or None,
            )
        except Exception:
            return None
        if result.outcome != "claimed":
            return None
        return (
            self.registry.get(record.transaction_id)
            or result.transaction
        )

    def _resolve_sourceless(
        self,
        stimulus: StimulusEnvelope,
        *,
        dialogue_history: Optional[List[dict]] = None,
        scene_context: str = "",
        scene_entries: Optional[Sequence[SceneEntry]] = None,
    ) -> Tuple[TransactionRecord, bool]:
        conversation_records = self.registry.list_for_conversation(
            stimulus.conversation_id
        )
        candidates = [
            record
            for record in conversation_records
            if is_match_candidate(record)
        ]
        deprecated_transaction_ids = {
            record.transaction_id
            for record in conversation_records
            if record.lifecycle_status == TransactionLifecycle.DELETED
        }
        if (
            candidates or deprecated_transaction_ids
        ) and self.semantic_resolver is not None:
            candidate_ids = {
                record.transaction_id for record in candidates
            }
            selected_id = self._invoke_semantic_resolver(
                stimulus,
                candidates,
                deprecated_transaction_ids=deprecated_transaction_ids,
                dialogue_history=dialogue_history,
                scene_context=scene_context,
                scene_entries=scene_entries,
            )
            if selected_id and selected_id in candidate_ids:
                # The semantic resolver may block on an LLM while another
                # request deletes or otherwise invalidates a candidate.  Use
                # durable identity, then revalidate under the same lock used
                # by delete and restore.  Object membership is insufficient:
                # registry cache self-healing mutates the original candidate
                # object into its tombstoned snapshot.
                with self.registry._lock:
                    selected = self.registry.get(selected_id)
                    if (
                        selected is not None
                        and selected.conversation_id
                        == stimulus.conversation_id
                        and is_match_candidate(selected)
                    ):
                        return self._reactivate_if_needed(
                            selected,
                            source="semantic_match",
                        ), False
            return self._resolve_user(stimulus, reuse_active=False)
        return self._resolve_user(stimulus, reuse_active=False)

    def _invoke_semantic_resolver(
        self,
        stimulus: StimulusEnvelope,
        candidates: Sequence[TransactionRecord],
        *,
        deprecated_transaction_ids: Iterable[str] = (),
        dialogue_history: Optional[List[dict]] = None,
        scene_context: str = "",
        scene_entries: Optional[Sequence[SceneEntry]] = None,
    ) -> Optional[str]:
        views = build_match_candidate_views(candidates)
        ref_to_id = {
            str(view["ref"]): record.transaction_id
            for view, record in zip(views, candidates)
        }
        id_set = {record.transaction_id for record in candidates}
        deprecated_id_set = {
            str(transaction_id or "").strip()
            for transaction_id in deprecated_transaction_ids
            if str(transaction_id or "").strip()
        }
        resolver_scene_context = str(scene_context or "").strip()
        if scene_entries is not None:
            resolver_scene_context = format_transaction_scene_view(
                scene_entries,
                candidate_refs_by_transaction_id={
                    record.transaction_id: str(view["ref"])
                    for view, record in zip(views, candidates)
                },
                deprecated_transaction_ids=deprecated_id_set,
                current_stimulus=stimulus,
            )
        try:
            try:
                selected = self.semantic_resolver(
                    stimulus,
                    views,
                    dialogue_history=dialogue_history,
                    scene_context=resolver_scene_context,
                )
            except TypeError:
                # Production ThinkingAgent still accepts TransactionRecord list
                # and already maps turn-local candidate_N internally.
                try:
                    selected = self.semantic_resolver(
                        stimulus,
                        list(candidates),
                        dialogue_history=dialogue_history,
                        scene_context=resolver_scene_context,
                    )
                except TypeError:
                    selected = self.semantic_resolver(stimulus, list(candidates))
        except Exception:
            return None

        selected_id = str(selected or "").strip()
        if not selected_id:
            return None
        # ``deprecated_N`` is prompt-only historical context. Durable ids of
        # deleted transactions are equally non-selectable and non-restorable.
        if (
            selected_id.startswith("deprecated_")
            or selected_id in deprecated_id_set
        ):
            return None
        if selected_id in ref_to_id:
            return ref_to_id[selected_id]
        if selected_id in id_set:
            return selected_id
        return None

    def _reactivate_if_needed(
        self,
        record: TransactionRecord,
        *,
        source: str,
    ) -> TransactionRecord:
        if record.state in {
            TransactionState.PAUSE,
            TransactionState.COMPLETE,
            TransactionState.ARCHIVE,
        }:
            return self.registry.restore(record.transaction_id, source=source)
        return record

    def _resolve_feedback(self, stimulus: StimulusEnvelope) -> Tuple[TransactionRecord, bool]:
        delegate_id = str(stimulus.delegate_id or stimulus.payload.get("delegate_id", "") or "").strip()
        if not delegate_id:
            self._mark_preconsume_discard(
                stimulus,
                reason="missing_delegate_id",
            )
            raise ValueError("execution_feedback requires delegate_id")
        transaction_id = str(stimulus.transaction_id or "").strip()
        if not transaction_id:
            self._mark_preconsume_discard(
                stimulus,
                reason="missing_transaction_id",
            )
            raise ValueError("execution_feedback requires transaction_id")
        activation_id = str(
            stimulus.activation_id
            or stimulus.payload.get("activation_id", "")
            or ""
        ).strip()
        if not activation_id:
            self._mark_preconsume_discard(
                stimulus,
                reason="missing_activation_id",
            )
            raise ValueError("execution_feedback requires activation_id")

        record = self.registry.get(transaction_id)
        if record is None:
            self._mark_preconsume_discard(
                stimulus,
                reason="unknown_transaction",
            )
            raise ValueError(f"unknown feedback transaction_id={transaction_id}")
        if record.conversation_id != stimulus.conversation_id:
            self._mark_preconsume_discard(
                stimulus,
                reason="cross_conversation_feedback",
            )
            raise ValueError(
                "execution_feedback source belongs to another conversation"
            )
        if self.registry.validate_feedback_source(
            transaction_id,
            activation_id,
            delegate_id,
        ):
            record = self.registry.consume_feedback(
                transaction_id,
                activation_id,
                delegate_id,
            )
            self._mark_feedback_consumed(stimulus)
            return record, False

        if self._is_already_consumed_feedback(
            transaction_id,
            activation_id,
            delegate_id,
        ):
            # Duplicate Feedback after a successful consume is canonical
            # evidence for the same ingress identity, not a new discard.
            refreshed = self.registry.get(transaction_id) or record
            self._mark_feedback_consumed(stimulus)
            return refreshed, False

        self._mark_preconsume_discard(
            stimulus,
            reason="stale_activation_or_delegate",
        )
        raise ValueError(
            "execution_feedback source is no longer valid"
        )

    def _is_already_consumed_feedback(
        self,
        transaction_id: str,
        activation_id: str,
        delegate_id: str,
    ) -> bool:
        store = getattr(self.registry, "store", None)
        if store is None or not hasattr(store, "load_delegate"):
            return False
        try:
            delegate = store.load_delegate(delegate_id)
        except Exception:
            return False
        if delegate is None:
            return False
        status = getattr(delegate, "status", None)
        status_value = (
            status.value if hasattr(status, "value") else str(status or "")
        )
        return (
            status_value == "consumed"
            and str(getattr(delegate, "transaction_id", "") or "")
            == transaction_id
            and str(getattr(delegate, "activation_id", "") or "")
            == activation_id
        )

    def _mark_feedback_consumed(self, stimulus: StimulusEnvelope) -> None:
        store = getattr(self.registry, "store", None)
        if store is None or not hasattr(store, "set_stimulus_disposition"):
            return
        sid = str(stimulus.stimulus_id or "").strip()
        if not sid:
            return
        try:
            if store.load_stimulus(sid) is None:
                return
            store.set_stimulus_disposition(
                sid,
                disposition="completed",
                stage="final",
                reason="feedback_consumed",
                reason_code="turn_completed",
            )
        except Exception:
            pass

    def _mark_preconsume_discard(
        self,
        stimulus: StimulusEnvelope,
        *,
        reason: str,
    ) -> None:
        store = getattr(self.registry, "store", None)
        if store is None:
            return
        try:
            store.set_stimulus_disposition(
                stimulus.stimulus_id,
                disposition="discarded",
                stage="preconsume",
                reason=reason,
                reason_code="preconsume",
            )
        except Exception:
            pass

    def _resolve_heartbeat(self, stimulus: StimulusEnvelope) -> Tuple[TransactionRecord, bool]:
        schedule_id = str(stimulus.schedule_id or stimulus.payload.get("schedule_id", "") or "").strip()
        priority = stimulus.priority_override or self.config.scheduler.default_heartbeat_priority
        from m_agent.runtime.domain.contracts import TransactionCorrelation

        payload = stimulus.payload if isinstance(stimulus.payload, dict) else {}
        correlation = TransactionCorrelation(
            schedule_id=schedule_id or None,
            schedule_owner_id=str(payload.get("owner_id", "") or "").strip() or None,
            schedule_run_id=str(payload.get("run_id", "") or "").strip() or None,
        )
        record = self.registry.create(
            thread_id=stimulus.thread_id,
            conversation_id=stimulus.conversation_id,
            kind=TransactionKind.SCHEDULE,
            priority=int(priority),
            correlation=correlation,
        )
        return record, True

    def _resolve_user(
        self,
        stimulus: StimulusEnvelope,
        *,
        reuse_active: bool = True,
    ) -> Tuple[TransactionRecord, bool]:
        priority = stimulus.priority_override
        if priority is None:
            priority = self.config.scheduler.default_user_priority
        active = (
            self.registry.get_active_user_transaction(stimulus.conversation_id)
            if reuse_active
            else None
        )
        if active is not None and is_runnable_record(active):
            return active, False
        record = self.registry.create(
            thread_id=stimulus.thread_id,
            conversation_id=stimulus.conversation_id,
            kind=TransactionKind.USER_TASK,
            priority=int(priority),
        )
        self.registry.set_active_user_transaction(stimulus.conversation_id, record.transaction_id)
        return record, True

    def priority_for(self, stimulus: StimulusEnvelope) -> int:
        if stimulus.priority_override is not None:
            return int(stimulus.priority_override)
        if stimulus.kind == StimulusKind.USER_MESSAGE:
            return self.config.scheduler.default_user_priority
        if stimulus.kind == StimulusKind.EXECUTION_FEEDBACK:
            return self.config.scheduler.default_feedback_priority
        if stimulus.kind == StimulusKind.SCHEDULED_PLAN:
            return getattr(
                self.config.scheduler,
                "default_schedule_priority",
                30,
            )
        return self.config.scheduler.default_heartbeat_priority
