"""Workspace answerability evaluation.

Provides both a fast rule-based pre-filter and an LLM-based deep judge.

The LLM judge returns a structured decision containing:
- ``status``: ``SUFFICIENT`` / ``INSUFFICIENT`` / ``INVALID``
- ``useful_evidence_ids``: which evidence the judge considers genuinely useful
- ``reason``: human-readable explanation
- ``next_query``: when ``INSUFFICIENT``, the query for the next retrieval round
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, TypedDict

from .workspace import Workspace, WorkspaceDocument, WorkspaceStatus

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")
_EVIDENCE_REF_PREFIX_RE = re.compile(r"^\s*ref\s*[:：]\s*", flags=re.IGNORECASE)


class JudgeDecision(TypedDict):
    status: WorkspaceStatus
    useful_evidence_ids: List[str]
    reason: str
    next_query: str | None
    gap_type: str | None


# ---------------------------------------------------------------------------
# Fast rule-based pre-filter (zero LLM calls)
# ---------------------------------------------------------------------------

def quick_reject(workspace: Workspace, new_evidence_ids: List[str]) -> Optional[JudgeDecision]:
    """Return a decision immediately if the workspace is obviously empty.

    Returns ``None`` when the quick check cannot decide and LLM judge is needed.
    """
    kept = workspace.kept_evidences()
    if not kept:
        return {
            "status": "INVALID",
            "useful_evidence_ids": [],
            "reason": "Workspace has no kept evidence at all.",
            "next_query": None,
            "gap_type": "no_evidence",
        }

    has_any = any(_has_any_useful_content(ev) for ev in kept)
    if not has_any:
        return {
            "status": "INVALID",
            "useful_evidence_ids": [],
            "reason": "All kept evidence lack both turn content and facts.",
            "next_query": None,
            "gap_type": "empty_evidence",
        }

    return None


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------

def llm_judge_workspace(
    workspace: Workspace,
    new_evidence_ids: List[str],
    *,
    llm_func: Callable[[str], Any],
    prompt_text: str,
) -> JudgeDecision:
    """Call the LLM to evaluate workspace evidence sufficiency.

    ``prompt_text`` should already have all placeholders rendered.
    ``llm_func`` is the model invocation callable (same as used elsewhere).
    """
    quick = quick_reject(workspace, new_evidence_ids)
    if quick is not None:
        return quick

    raw_response = llm_func(prompt_text)
    response_text = _extract_text(raw_response)
    parsed = _parse_judge_response(response_text)

    status = parsed.get("status", "").strip().upper()
    if status not in {"SUFFICIENT", "INSUFFICIENT", "INVALID"}:
        logger.warning("LLM judge returned unexpected status '%s', defaulting to INSUFFICIENT", status)
        status = "INSUFFICIENT"

    useful_ids = parsed.get("useful_evidence_ids", [])
    if not isinstance(useful_ids, list):
        useful_ids = []
    useful_ids = [_normalize_evidence_id(eid) for eid in useful_ids]
    useful_ids = [eid for eid in useful_ids if eid]

    reason = str(parsed.get("reason", "") or "").strip() or "LLM judge decision."
    next_query = parsed.get("next_query")
    if isinstance(next_query, str):
        next_query = next_query.strip() or None
    else:
        next_query = None

    if status == "INVALID":
        new_set = set(new_evidence_ids)
        any_new_useful = any(eid in new_set for eid in useful_ids)
        if any_new_useful:
            status = "INSUFFICIENT"
            logger.info("LLM said INVALID but selected new evidence as useful; upgrading to INSUFFICIENT")

    gap_type: str | None = None
    if status == "INSUFFICIENT":
        gap_type = "need_more_evidence"
    elif status == "INVALID":
        gap_type = "round_produced_nothing"

    return {
        "status": status,  # type: ignore[typeddict-item]
        "useful_evidence_ids": useful_ids,
        "reason": reason,
        "next_query": next_query,
        "gap_type": gap_type,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if hasattr(response, "content"):
        return str(response.content)
    return str(response)


def _parse_judge_response(text: str) -> Dict[str, Any]:
    match = _JSON_BLOCK_RE.search(text or "")
    if not match:
        logger.warning("Could not extract JSON from judge response")
        return {}
    try:
        return json.loads(match.group())
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse judge JSON: %s", exc)
        return {}


def _has_any_useful_content(doc: WorkspaceDocument) -> bool:
    return bool(str(doc.get("content", "") or "").strip())


def _normalize_evidence_id(raw: Any) -> str:
    """Normalize evidence ids returned by the LLM judge.

    The workspace prompt labels evidences as ``ref: <evidence_id>``. Some models
    copy the ``ref:`` prefix back into ``useful_evidence_ids``; downstream
    workspace pruning expects the bare ``evidence_id``.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    text = _EVIDENCE_REF_PREFIX_RE.sub("", text).strip()
    # Defensive stripping for common wrappers the judge might emit.
    if (text.startswith("`") and text.endswith("`")) or (text.startswith('"') and text.endswith('"')):
        text = text[1:-1].strip()
    return text
