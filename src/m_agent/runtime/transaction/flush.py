"""User-visible logical Flush boundary for the P2 reference store."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping, Optional

from .store import SQLiteRuntimeStore


class FlushCoordinator:
    """Commit Scene watermark and eligible archive changes atomically."""

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
        """Arm a deterministic crash window used by the reference Gate.

        P2 models commit-before-materialisation with a durable outbox.  It
        does not claim that external Dialogue/RAG destinations share the
        SQLite transaction.
        """

        point = str(fault_point or "").strip()
        self._fault_point = point or None
        return {
            "fault_point": point,
            "armed": bool(point),
            "scope": "p2_reference_store",
        }

    def trigger(
        self,
        *,
        conversation_id: str,
        flush_id: str,
        payload: Optional[Dict[str, Any]] = None,
        through_seq: Optional[int] = None,
        eligible_revisions: Optional[Mapping[str, int]] = None,
        outbox_payloads: Optional[
            Mapping[str, Mapping[str, Any]]
        ] = None,
    ) -> Dict[str, Any]:
        cid = str(conversation_id or "").strip()
        fault = self._fault_point
        self._fault_point = None
        fault_outbox = (
            {
                "acceptance-materialization": {
                    "flush_id": flush_id,
                    "conversation_id": cid,
                    "through_seq": through_seq,
                }
            }
            if fault == "after_flush_commit_before_materialize"
            else {}
        )
        outbox = {
            str(destination): dict(materialization)
            for destination, materialization in dict(
                outbox_payloads or {}
            ).items()
        }
        outbox.update(fault_outbox)
        committed = self.store.commit_flush(
            flush_id=flush_id,
            conversation_id=cid,
            through_seq=through_seq,
            payload=payload,
            eligible_revisions=eligible_revisions,
            outbox_payloads=outbox,
            result_extra={
                "fault_window": fault,
                "materialization_status": (
                    "pending" if outbox else "not_requested"
                ),
            },
        )
        result = deepcopy(committed.result)
        if self.registry is not None:
            refresh = getattr(self.registry, "refresh_from_store", None)
            if callable(refresh):
                refresh()
        result["current_materialization_status"] = (
            self.store.flush_materialization_status(flush_id)
        )
        return result

    def recover_materializations(self) -> Dict[str, Any]:
        pending = self.store.list_pending_flush_outbox()
        recovered = []
        for flush_id in sorted(
            {
                str(item["flush_id"])
                for item in pending
            }
        ):
            for destination in self.store.materialize_flush_outbox(
                flush_id
            ):
                recovered.append(
                    {
                        "flush_id": flush_id,
                        "destination": destination,
                    }
                )
        return {
            "recovered": recovered,
            "recovered_count": len(recovered),
        }


__all__ = ["FlushCoordinator"]
