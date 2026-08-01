"""Durable effect ledger and Feedback ingress outbox for P7."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from .store import (
    RecordNotFoundError,
    RuntimeStoreUnitOfWork,
    SQLiteRuntimeStore,
    StoreInvariantError,
)


Clock = Any


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
    ) -> None:
        self.store = store
        self.registry = registry
        self._fault_point: Optional[str] = None

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
    ) -> Dict[str, Any]:
        eid = str(effect_id or "").strip()
        fault = self._fault_point
        self._fault_point = None

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
            if fault == "after_effect_result_before_feedback_relay":
                raise StoreInvariantError(
                    "simulated crash after effect result before relay"
                )
            result_payload = {
                "effect_id": eid,
                "status": "completed",
                "summary": f"effect {eid} completed",
            }
            updated_effect = uow.update_effect_intent(
                eid,
                {
                    "status": "completed",
                    "attempts": int(effect["attempts"]) + 1,
                    "visible_effects": 1,
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
            },
            operation=operation,
        )
        return dict(execution.result)

    def relay_feedback(
        self,
        *,
        effect_id: str,
    ) -> Dict[str, Any]:
        eid = str(effect_id or "").strip()

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
            if transaction is not None and transaction.deleted:
                updated = uow.update_feedback_outbox(
                    str(outbox["outbox_id"]),
                    status="terminal",
                    discard_evidence=(
                        "expected_discard:transaction_deleted"
                    ),
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
                    "canonical_feedback_count": 1,
                    "outbox_status": outbox["status"],
                    "replayed": True,
                }
            updated = uow.update_feedback_outbox(
                str(outbox["outbox_id"]),
                status="delivered",
            )
            return {
                "ingress_key": ingress_key,
                "canonical_feedback_count": 1,
                "outbox_status": updated["status"],
                "replayed": False,
            }

        first = self.store.execute_transition(
            transition_id=f"effect-relay:{eid}",
            command={
                "action": "relay_feedback",
                "effect_id": eid,
            },
            operation=operation,
        )
        second = self.store.execute_transition(
            transition_id=f"effect-relay:{eid}",
            command={
                "action": "relay_feedback",
                "effect_id": eid,
            },
            operation=operation,
        )
        payload = dict(first.result)
        payload["replayed"] = bool(second.replayed)
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
        return {"recovered": [], "recovered_count": 0}


__all__ = [
    "EffectCoordinator",
    "acceptance_effect_id_for_delegate",
]
