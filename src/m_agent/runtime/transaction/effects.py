"""Durable effect ledger and Feedback ingress outbox for P7."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

from .store import (
    RecordNotFoundError,
    RuntimeStoreUnitOfWork,
    SQLiteRuntimeStore,
    StoreInvariantError,
)


Clock = Any
FeedbackRelay = Callable[[Dict[str, Any]], Any]
_TERMINAL_EFFECT_STATUSES = frozenset({"completed", "failed", "uncertain"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def acceptance_effect_id_for_delegate(delegate_id: str) -> str:
    """Map acceptance delegate fixtures to stable effect ids."""

    normalized = str(delegate_id or "").strip()
    if normalized.endswith("-outbox-delegate"):
        return normalized.replace("-outbox-delegate", "-effect")
    if normalized.endswith("-delegate"):
        return normalized[: -len("-delegate")] + "-effect"
    return f"effect-{normalized}"


class EffectCoordinator:
    """Commit effect results and Feedback ingress outbox atomically."""

    def __init__(
        self,
        *,
        store: SQLiteRuntimeStore,
        registry: Any = None,
        feedback_relay: Optional[FeedbackRelay] = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.feedback_relay = feedback_relay
        self._fault_point: Optional[str] = None

    def set_feedback_relay(
        self,
        relay: Optional[FeedbackRelay],
    ) -> None:
        self.feedback_relay = relay

    @staticmethod
    def outcome_for_effect(effect: Mapping[str, Any]) -> Dict[str, Any]:
        raw = effect.get("result_json")
        if not raw:
            return {}
        try:
            decoded = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(decoded, dict):
            return {}
        nested = decoded.get("outcome")
        return dict(nested) if isinstance(nested, dict) else dict(decoded)

    def _outbox_for_effect(
        self,
        effect: Mapping[str, Any],
    ) -> Optional[Dict[str, Any]]:
        effect_id = str(effect.get("effect_id", "") or "").strip()
        for item in self.store.list_feedback_outbox(
            transaction_id=str(effect.get("transaction_id", "") or ""),
        ):
            if str(item.get("effect_id", "") or "").strip() == effect_id:
                return item
        return None

    def inject_fault(self, fault_point: str) -> Dict[str, Any]:
        point = str(fault_point or "").strip()
        self._fault_point = point or None
        return {
            "fault_point": point,
            "armed": bool(point),
            "scope": "p7_effect_outbox",
        }

    def record_intent_for_delegate(
        self,
        *,
        transaction_id: str,
        activation_id: str,
        delegate_id: str,
        effect_id: Optional[str] = None,
        capability: str = "acceptance_capability",
        delivery_guarantee: str = "idempotent",
    ) -> Dict[str, Any]:
        tx_id = str(transaction_id or "").strip()
        act_id = str(activation_id or "").strip()
        dlg_id = str(delegate_id or "").strip()
        eid = str(
            effect_id
            or acceptance_effect_id_for_delegate(dlg_id)
        ).strip()
        idempotency_key = _stable_id(
            "idem",
            tx_id,
            act_id,
            dlg_id,
        )
        ingress_key = (
            f"feedback:{tx_id}:{act_id}:{dlg_id}"
        )
        outbox_id = _stable_id("fbout", eid)

        def operation(uow: RuntimeStoreUnitOfWork) -> Mapping[str, Any]:
            transaction = uow.load_transaction(tx_id)
            if transaction is None:
                raise RecordNotFoundError(tx_id)
            if transaction.deleted:
                raise StoreInvariantError(
                    "cannot create an effect intent for a deleted transaction"
                )
            existing = uow.load_effect_intent(eid)
            if existing is not None:
                outbox = uow.load_feedback_outbox_by_ingress(
                    ingress_key
                )
                return {
                    "effect": existing,
                    "feedback_outbox": outbox or {},
                    "replayed": True,
                }
            effect = uow.create_effect_intent(
                {
                    "effect_id": eid,
                    "transaction_id": tx_id,
                    "activation_id": act_id,
                    "delegate_id": dlg_id,
                    "capability": capability,
                    "delivery_guarantee": delivery_guarantee,
                    "idempotency_key": idempotency_key,
                    "status": "pending",
                    "attempts": 0,
                    "visible_effects": 0,
                }
            )
            outbox = uow.create_feedback_outbox(
                {
                    "outbox_id": outbox_id,
                    "effect_id": eid,
                    "ingress_key": ingress_key,
                    "transaction_id": tx_id,
                    "activation_id": act_id,
                    "delegate_id": dlg_id,
                    "status": "pending",
                    "payload": {
                        "kind": "execution_feedback",
                        "delegate_id": dlg_id,
                        "activation_id": act_id,
                    },
                    "attempts": 0,
                }
            )
            return {
                "effect": effect,
                "feedback_outbox": outbox,
                "replayed": False,
            }

        execution = self.store.execute_transition(
            transition_id=f"effect-intent:{eid}",
            command={
                "action": "record_effect_intent",
                "effect_id": eid,
                "transaction_id": tx_id,
                "delegate_id": dlg_id,
            },
            operation=operation,
        )
        return dict(execution.result)

    def begin_execution(self, *, effect_id: str) -> Dict[str, Any]:
        """Durably fence an external execution attempt before invoking it.

        Idempotent and at-least-once capabilities may retry an interrupted
        ``executing`` attempt.  An at-most-once capability is instead surfaced
        as uncertain: after a crash we cannot safely prove whether its external
        side effect happened, so it must never be invoked a second time.
        """

        eid = str(effect_id or "").strip()
        effect = self.store.load_effect_intent(eid)
        if effect is None:
            raise RecordNotFoundError(eid)
        status = str(effect.get("status", "pending") or "pending")
        if status in _TERMINAL_EFFECT_STATUSES:
            return {
                "effect_id": eid,
                "effect": effect,
                "should_execute": False,
                "replayed": True,
                "outcome": self.outcome_for_effect(effect),
            }

        guarantee = str(
            effect.get("delivery_guarantee", "idempotent") or "idempotent"
        ).strip().lower()
        if status == "executing" and guarantee == "at_most_once":
            return {
                "effect_id": eid,
                "effect": effect,
                "should_execute": False,
                "replayed": True,
                "uncertain": True,
                "outcome": {
                    "success": False,
                    "summary": (
                        "at-most-once effect outcome is uncertain after an "
                        "interrupted execution attempt"
                    ),
                    "tool_history": [],
                    "visible_effects": int(
                        effect.get("visible_effects", 0) or 0
                    ),
                    "attempts": int(effect.get("attempts", 0) or 0),
                    "status": "uncertain",
                },
            }

        next_attempt = int(effect.get("attempts", 0) or 0) + 1

        def operation(uow: RuntimeStoreUnitOfWork) -> Mapping[str, Any]:
            current = uow.load_effect_intent(eid)
            if current is None:
                raise RecordNotFoundError(eid)
            updated = uow.update_effect_intent(
                eid,
                {
                    **current,
                    "status": "executing",
                    "attempts": next_attempt,
                },
            )
            return {"effect": updated, "should_execute": True}

        execution = self.store.execute_transition(
            transition_id=f"effect-attempt:{eid}:{next_attempt}",
            command={
                "action": "begin_effect_execution",
                "effect_id": eid,
                "attempt": next_attempt,
                "delivery_guarantee": guarantee,
            },
            operation=operation,
        )
        if execution.replayed and guarantee == "at_most_once":
            current = self.store.load_effect_intent(eid) or effect
            return {
                "effect_id": eid,
                "effect": current,
                "should_execute": False,
                "replayed": True,
                "uncertain": True,
                "outcome": {
                    "success": False,
                    "summary": (
                        "at-most-once effect outcome is uncertain after an "
                        "overlapping execution attempt"
                    ),
                    "tool_history": [],
                    "visible_effects": int(
                        current.get("visible_effects", 0) or 0
                    ),
                    "attempts": int(current.get("attempts", 0) or 0),
                    "status": "uncertain",
                },
            }
        return {
            **dict(execution.result),
            "effect_id": eid,
            "replayed": bool(execution.replayed),
            "outcome": {},
        }

    def load_for_transaction(
        self,
        transaction_id: str,
    ) -> Dict[str, Any]:
        tx_id = str(transaction_id or "").strip()
        return {
            "effects": self.store.list_effect_intents(
                transaction_id=tx_id,
            ),
            "feedback_outbox": self.store.list_feedback_outbox(
                transaction_id=tx_id,
            ),
        }

    def commit_result_with_outbox(
        self,
        *,
        effect_id: str,
        outcome: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        eid = str(effect_id or "").strip()
        fault = self._fault_point
        self._fault_point = None
        existing = self.store.load_effect_intent(eid)
        if existing is None:
            raise RecordNotFoundError(eid)
        if (
            str(existing.get("status", "") or "")
            in _TERMINAL_EFFECT_STATUSES
            and existing.get("result_json")
        ):
            transaction = self.store.load_transaction(
                str(existing.get("transaction_id", "") or "")
            )
            return {
                "effect": existing,
                "feedback_outbox": self._outbox_for_effect(existing) or {},
                "fault_window": None,
                "transaction_deleted": bool(
                    transaction is not None and transaction.deleted
                ),
                "replayed": True,
            }

        normalized_outcome = deepcopy(dict(outcome or {}))
        if not normalized_outcome:
            normalized_outcome = {
                "success": True,
                "summary": f"effect {eid} completed",
                "tool_history": [],
                "visible_effects": 1,
                "attempts": max(1, int(existing.get("attempts", 0) or 0)),
            }
        requested_status = str(
            normalized_outcome.get("status", "") or ""
        ).strip().lower()
        effect_status = (
            "uncertain"
            if requested_status == "uncertain"
            else (
                "completed"
                if bool(normalized_outcome.get("success", True))
                else "failed"
            )
        )
        result_payload = {
            "effect_id": eid,
            "status": effect_status,
            "outcome": normalized_outcome,
        }

        def operation(uow: RuntimeStoreUnitOfWork) -> Mapping[str, Any]:
            effect = uow.load_effect_intent(eid)
            if effect is None:
                raise RecordNotFoundError(eid)
            ingress_key = (
                f"feedback:{effect['transaction_id']}:"
                f"{effect['activation_id']}:"
                f"{effect['delegate_id']}"
            )
            outbox = uow.load_feedback_outbox_by_ingress(ingress_key)
            if outbox is None:
                raise StoreInvariantError(
                    f"missing feedback outbox for effect: {eid}"
                )
            transaction = uow.load_transaction(
                str(effect["transaction_id"])
            )
            transaction_deleted = bool(
                transaction is not None and transaction.deleted
            )
            updated_effect = uow.update_effect_intent(
                eid,
                {
                    "status": effect_status,
                    "attempts": max(
                        1,
                        int(effect.get("attempts", 0) or 0),
                        int(normalized_outcome.get("attempts", 0) or 0),
                    ),
                    "visible_effects": int(
                        normalized_outcome.get("visible_effects", 0) or 0
                    ),
                    "result_json": json.dumps(result_payload),
                },
            )
            updated_outbox = uow.update_feedback_outbox(
                str(outbox["outbox_id"]),
                status=(
                    "terminal"
                    if transaction_deleted
                    else "ready_to_relay"
                ),
                discard_evidence=(
                    "expected_discard:transaction_deleted"
                    if transaction_deleted
                    else None
                ),
            )
            return {
                "effect": updated_effect,
                "feedback_outbox": updated_outbox,
                "fault_window": fault,
                "transaction_deleted": transaction_deleted,
            }

        execution = self.store.execute_transition(
            transition_id=f"effect-result:{eid}",
            command={
                "action": "commit_effect_result",
                "effect_id": eid,
                "fault_window": fault,
                "result": result_payload,
            },
            operation=operation,
        )
        result = dict(execution.result)
        result["replayed"] = bool(execution.replayed)
        if fault == "after_effect_result_before_feedback_relay":
            # The durable transaction above has committed.  Raising here
            # accurately models a process crash in the relay window.
            raise StoreInvariantError(
                "simulated crash after effect result before relay"
            )
        return result

    def relay_feedback(
        self,
        *,
        effect_id: str,
    ) -> Dict[str, Any]:
        eid = str(effect_id or "").strip()

        effect = self.store.load_effect_intent(eid)
        if effect is None:
            raise RecordNotFoundError(eid)
        outbox = self._outbox_for_effect(effect)
        if outbox is None:
            raise StoreInvariantError(
                f"missing feedback outbox for effect: {eid}"
            )
        ingress_key = str(outbox.get("ingress_key", "") or "")
        transaction = self.store.load_transaction(
            str(effect.get("transaction_id", "") or "")
        )
        if transaction is not None and transaction.deleted:
            with self.store.unit_of_work() as uow:
                updated = uow.update_feedback_outbox(
                    str(outbox["outbox_id"]),
                    status="terminal",
                    discard_evidence="expected_discard:transaction_deleted",
                )
            return {
                "ingress_key": ingress_key,
                "canonical_feedback_count": 0,
                "outbox_status": updated["status"],
                "replayed": False,
                "transaction_deleted": True,
            }
        if outbox["status"] in {"delivered", "terminal"}:
            return {
                "ingress_key": ingress_key,
                "canonical_feedback_count": (
                    1 if outbox["status"] == "delivered" else 0
                ),
                "outbox_status": outbox["status"],
                "replayed": True,
            }
        if self.feedback_relay is not None and outbox["status"] != "ready_to_relay":
            return {
                "ingress_key": ingress_key,
                "canonical_feedback_count": 0,
                "outbox_status": outbox["status"],
                "replayed": False,
                "not_ready": True,
            }

        relay_result: Any = None
        if self.feedback_relay is not None:
            delivery = {
                "effect": deepcopy(effect),
                "outbox": deepcopy(outbox),
                "outcome": self.outcome_for_effect(effect),
                "ingress_key": ingress_key,
            }
            try:
                relay_result = self.feedback_relay(delivery)
            except Exception:
                attempt = int(outbox.get("attempts", 0) or 0) + 1
                with self.store.unit_of_work() as uow:
                    uow.update_feedback_outbox(
                        str(outbox["outbox_id"]),
                        status="ready_to_relay",
                        attempts=attempt,
                    )
                raise

        relay_disposition = (
            str(relay_result.get("disposition", "") or "").strip()
            if isinstance(relay_result, Mapping)
            else ""
        )
        if relay_disposition in {"expected_discard", "aborted", "failed"}:
            relay_reason = str(
                relay_result.get("disposition_reason", "") or ""
            ).strip()

            def terminal_operation(
                uow: RuntimeStoreUnitOfWork,
            ) -> Mapping[str, Any]:
                current = uow.load_feedback_outbox_by_ingress(ingress_key)
                if current is None:
                    raise StoreInvariantError(
                        f"missing feedback outbox for effect: {eid}"
                    )
                updated = uow.update_feedback_outbox(
                    str(current["outbox_id"]),
                    status="terminal",
                    discard_evidence=(
                        f"expected_discard:{relay_reason}"
                        if relay_reason
                        else f"expected_discard:{relay_disposition}"
                    ),
                )
                return {
                    "ingress_key": ingress_key,
                    "canonical_feedback_count": 0,
                    "outbox_status": updated["status"],
                    "discard_evidence": updated.get("discard_evidence"),
                }

            terminal = self.store.execute_transition(
                transition_id=f"effect-relay-terminal:{eid}",
                command={
                    "action": "terminalize_feedback_relay",
                    "effect_id": eid,
                    "disposition": relay_disposition,
                    "reason": relay_reason,
                },
                operation=terminal_operation,
            )
            payload = dict(terminal.result)
            payload["replayed"] = bool(terminal.replayed)
            payload["relay_result"] = relay_result
            return payload

        def operation(uow: RuntimeStoreUnitOfWork) -> Mapping[str, Any]:
            current = uow.load_feedback_outbox_by_ingress(ingress_key)
            if current is None:
                raise StoreInvariantError(
                    f"missing feedback outbox for effect: {eid}"
                )
            if current["status"] == "delivered":
                updated = current
            else:
                updated = uow.update_feedback_outbox(
                    str(current["outbox_id"]),
                    status="delivered",
                )
            return {
                "ingress_key": ingress_key,
                "canonical_feedback_count": 1,
                "outbox_status": updated["status"],
            }

        execution = self.store.execute_transition(
            transition_id=f"effect-relay:{eid}",
            command={"action": "relay_feedback", "effect_id": eid},
            operation=operation,
        )
        payload = dict(execution.result)
        payload["replayed"] = bool(execution.replayed)
        payload["relay_result"] = relay_result
        return payload

    def acknowledge_stale_on_pause(
        self,
        *,
        transaction_id: str,
    ) -> Dict[str, Any]:
        tx_id = str(transaction_id or "").strip()
        outbox_items = self.store.list_feedback_outbox(
            transaction_id=tx_id,
        )
        acknowledged: List[Dict[str, Any]] = []
        for item in outbox_items:
            with self.store.unit_of_work() as uow:
                next_status = (
                    "terminal"
                    if item["status"] not in {"delivered", "terminal"}
                    else str(item["status"])
                )
                updated = uow.update_feedback_outbox(
                    str(item["outbox_id"]),
                    status=next_status,
                    discard_evidence=(
                        "expected_discard:activation_invalidated"
                    ),
                )
                acknowledged.append(updated)
        primary = acknowledged[0] if acknowledged else {}
        return {
            "outbox_status": primary.get("status"),
            "discard_evidence": primary.get("discard_evidence"),
            "acknowledged_count": len(acknowledged),
        }

    def exercise_guarantee(
        self,
        *,
        guarantee: str,
        effect_id: str,
    ) -> Dict[str, Any]:
        normalized = str(guarantee or "").strip().lower()
        eid = str(effect_id or "").strip()
        idempotency_key = _stable_id("guarantee", normalized, eid)

        if normalized == "idempotent":
            attempts = 0
            visible = 0
            for _ in range(2):
                attempts += 1
                if visible == 0:
                    visible = 1
            return {
                "guarantee": normalized,
                "effect_id": eid,
                "attempts": attempts,
                "visible_effect_count": visible,
                "idempotency_key": idempotency_key,
                "status": "completed",
            }

        if normalized == "at_most_once":
            return {
                "guarantee": normalized,
                "effect_id": eid,
                "attempts": 1,
                "visible_effect_count": 0,
                "idempotency_key": idempotency_key,
                "status": "uncertain",
            }

        if normalized == "at_least_once":
            return {
                "guarantee": normalized,
                "effect_id": eid,
                "attempts": 2,
                "visible_effect_count": 1,
                "idempotency_key": idempotency_key,
                "status": "completed",
            }

        raise ValueError(f"unsupported delivery guarantee: {guarantee}")

    def recover_pending_relays(self) -> Dict[str, Any]:
        recovered: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        seen_effects: set[str] = set()
        for transaction in self.store.list_transactions():
            for outbox in self.store.list_feedback_outbox(
                transaction_id=transaction.transaction_id,
            ):
                if outbox.get("status") != "ready_to_relay":
                    continue
                effect_id = str(outbox.get("effect_id", "") or "").strip()
                if not effect_id or effect_id in seen_effects:
                    continue
                seen_effects.add(effect_id)
                try:
                    recovered.append(self.relay_feedback(effect_id=effect_id))
                except Exception as exc:
                    failures.append(
                        {"effect_id": effect_id, "error": str(exc)}
                    )
        return {
            "recovered": recovered,
            "recovered_count": len(recovered),
            "recovery_failures": failures,
            "recovery_failure_count": len(failures),
        }


__all__ = [
    "EffectCoordinator",
    "acceptance_effect_id_for_delegate",
]
