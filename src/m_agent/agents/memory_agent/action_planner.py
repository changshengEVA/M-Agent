from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Set, TypedDict

logger = logging.getLogger(__name__)

ACTION_EVENT_DETAIL_RECALL = "EVENT_DETAIL_RECALL"
ACTION_EVENT_DETAIL_MULTI_ROUTE_RECALL = "EVENT_DETAIL_MULTI_ROUTE_RECALL"
ACTION_EVENT_TIME_RECALL = "EVENT_TIME_RECALL"
ACTION_RECALL_REMEDY_MULTI_ROUTE = "RECALL_REMEDY_MULTI_ROUTE"
ACTION_ENTITY_FEATURE_SEARCH = "ENTITY_FEATURE_SEARCH"
ACTION_ENTITY_EVENT_SEARCH = "ENTITY_EVENT_SEARCH"

_VALID_ACTION_TYPES = frozenset({
    ACTION_EVENT_DETAIL_RECALL,
    ACTION_EVENT_DETAIL_MULTI_ROUTE_RECALL,
    ACTION_EVENT_TIME_RECALL,
    ACTION_RECALL_REMEDY_MULTI_ROUTE,
    ACTION_ENTITY_FEATURE_SEARCH,
    ACTION_ENTITY_EVENT_SEARCH,
})


class ToolDescriptor(TypedDict):
    action_type: str
    description: str
    params: str


TOOL_REGISTRY: List[ToolDescriptor] = [
    {
        "action_type": ACTION_EVENT_DETAIL_RECALL,
        "description": "Semantic search over episode facts and dialogue details. Best for general questions about events, topics, or people.",
        "params": '{"detail_query": "<search query>", "topk": <int, default 10>}',
    },
    {
        "action_type": ACTION_EVENT_TIME_RECALL,
        "description": "Retrieve events within a specific date/time range. Use when the question mentions dates or time periods.",
        "params": '{"start_time": "<ISO date>", "end_time": "<ISO date>"}',
    },
    {
        "action_type": ACTION_ENTITY_FEATURE_SEARCH,
        "description": "Search features, attributes, or traits of a specific person or entity (e.g. hobbies, personality, preferences).",
        "params": '{"entity_id": "<person/entity name>", "feature_query": "<what to search>", "topk": <int, default 5>}',
    },
    {
        "action_type": ACTION_ENTITY_EVENT_SEARCH,
        "description": "Search events or activities related to a specific person or entity.",
        "params": '{"entity_id": "<person/entity name>", "event_query": "<what happened>", "topk": <int, default 5>}',
    },
    {
        "action_type": ACTION_RECALL_REMEDY_MULTI_ROUTE,
        "description": "Broad multi-route recall as a last resort when previous searches found nothing useful. Searches across multiple indexes simultaneously.",
        "params": '{"detail_query": "<search query>", "topk": <int, default 10>}',
    },
]


def build_tool_registry_from_config(
    tool_descriptions: Dict[str, Any],
    enabled_tools: List[str],
    language: str = "en",
) -> List[ToolDescriptor]:
    """Build a tool registry from YAML config (tool_descriptions + enabled_tools)."""
    registry: List[ToolDescriptor] = []
    for action_type in enabled_tools:
        tool_cfg = tool_descriptions.get(action_type)
        if not isinstance(tool_cfg, dict):
            logger.warning("tool_descriptions missing entry for %s, skipped", action_type)
            continue
        desc_node = tool_cfg.get("description", {})
        if isinstance(desc_node, dict):
            description = str(desc_node.get(language) or desc_node.get("en", ""))
        else:
            description = str(desc_node)
        params = str(tool_cfg.get("params", "{}"))
        registry.append({
            "action_type": action_type,
            "description": description,
            "params": params,
        })
    return registry


def tool_registry_for_prompt(
    force_remedy: bool = False,
    registry: Optional[List[ToolDescriptor]] = None,
) -> List[ToolDescriptor]:
    """Return the tool registry filtered for the current planning context.

    If *registry* is provided it takes precedence over the built-in TOOL_REGISTRY.
    """
    source = registry if registry is not None else TOOL_REGISTRY
    if force_remedy:
        return [t for t in source if t["action_type"] == ACTION_RECALL_REMEDY_MULTI_ROUTE]
    return [t for t in source if t["action_type"] != ACTION_RECALL_REMEDY_MULTI_ROUTE]


class QueryIntent(TypedDict):
    original_question: str
    sub_questions: List[str]
    question_type: str
    decomposition_reason: str
    constraints: Dict[str, Any]


class MemoryAction(TypedDict):
    action_id: str
    action_type: str
    query: Dict[str, Any]
    source_sub_question_idx: int
    priority: int


_ISO_DATE_PATTERN = re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b")
_QUOTE_HINTS = (
    "quote",
    "exact words",
    "who said",
    "original words",
    "原话",
    "引用",
    "谁说",
    "说过什么",
)
_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def build_query_intent(question_text: str) -> QueryIntent:
    normalized = str(question_text or "").strip()
    constraints = {
        "time_range": _extract_time_range(normalized),
        "requires_quote_verification": _requires_quote_verification(normalized),
    }
    question_type = _classify_question_type(normalized, constraints)
    return {
        "original_question": normalized,
        "sub_questions": [],
        "question_type": question_type,
        "decomposition_reason": "default_no_decomposition",
        "constraints": constraints,
    }


def intent_to_question_plan(intent: QueryIntent) -> Dict[str, Any]:
    sub_questions = intent.get("sub_questions") or []
    return {
        "goal": intent.get("original_question", ""),
        "question_type": intent.get("question_type", "direct_lookup"),
        "decomposition_reason": intent.get("decomposition_reason", "default_no_decomposition"),
        "sub_questions": sub_questions if isinstance(sub_questions, list) else [],
        "suggested_tool_order": [
            "search_events_by_time_range",
            "search_details",
            "search_content",
        ],
        "completion_criteria": "Return answer only when episode evidence supports it.",
    }


# ---------------------------------------------------------------------------
# Rule-based planner (fallback)
# ---------------------------------------------------------------------------

def plan_actions_rule_based(
    intent: QueryIntent,
    *,
    round_id: int,
    topk: int,
    max_actions: int,
    force_remedy: bool,
    previous_action_signatures: Set[str],
) -> List[MemoryAction]:
    normalized_round = max(1, int(round_id))
    safe_topk = max(1, int(topk))
    safe_max_actions = max(1, int(max_actions))
    question = str(intent.get("original_question", "") or "").strip()
    sub_questions = intent.get("sub_questions")
    if not isinstance(sub_questions, list) or not sub_questions:
        sub_questions = [question]

    constraints = intent.get("constraints") if isinstance(intent.get("constraints"), dict) else {}
    time_range = constraints.get("time_range")

    action_candidates: List[MemoryAction] = []
    action_index = 0

    def _push(action_type: str, query: Dict[str, Any], source_sub_idx: int, priority: int) -> None:
        nonlocal action_index
        action_index += 1
        action_candidates.append(
            {
                "action_id": f"r{normalized_round}_a{action_index}",
                "action_type": action_type,
                "query": query,
                "source_sub_question_idx": source_sub_idx,
                "priority": priority,
            }
        )

    if isinstance(time_range, dict) and time_range.get("start") and time_range.get("end"):
        _push(
            ACTION_EVENT_TIME_RECALL,
            {
                "start_time": str(time_range["start"]),
                "end_time": str(time_range["end"]),
            },
            source_sub_idx=0,
            priority=5,
        )

    for idx, sub_question in enumerate(sub_questions):
        q = str(sub_question or "").strip() or question
        if not q:
            continue
        if force_remedy:
            _push(
                ACTION_RECALL_REMEDY_MULTI_ROUTE,
                {"detail_query": q, "topk": safe_topk},
                source_sub_idx=idx,
                priority=100,
            )
            continue
        _push(
            ACTION_EVENT_DETAIL_RECALL,
            {"detail_query": q, "topk": safe_topk},
            source_sub_idx=idx,
            priority=10,
        )

    return _dedup_actions(action_candidates, safe_max_actions, previous_action_signatures)


# ---------------------------------------------------------------------------
# LLM-based planner
# ---------------------------------------------------------------------------

def plan_actions_llm(
    *,
    llm_func: Callable[[str], Any],
    prompt_text: str,
    round_id: int,
    max_actions: int,
    previous_action_signatures: Set[str],
) -> List[MemoryAction]:
    """Call LLM to generate an action plan, parse and validate the result."""
    raw_response = llm_func(prompt_text)
    response_text = _extract_text(raw_response)
    actions = parse_llm_action_plan(response_text, round_id=round_id)
    return _dedup_actions(actions, max(1, int(max_actions)), previous_action_signatures)


def parse_llm_action_plan(response_text: str, *, round_id: int = 1) -> List[MemoryAction]:
    """Parse LLM response into a list of validated MemoryAction."""
    match = _JSON_BLOCK_RE.search(response_text or "")
    if not match:
        logger.warning("LLM action planner returned no JSON block")
        return []
    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse action plan JSON: %s", exc)
        return []

    raw_actions = parsed.get("actions")
    if not isinstance(raw_actions, list):
        logger.warning("LLM action plan missing 'actions' list")
        return []

    normalized_round = max(1, int(round_id))
    actions: List[MemoryAction] = []
    for idx, item in enumerate(raw_actions):
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("action_type", "")).strip()
        if action_type not in _VALID_ACTION_TYPES:
            logger.warning("LLM planner produced invalid action_type: %s, skipping", action_type)
            continue
        query = item.get("query", {})
        if not isinstance(query, dict):
            query = {}
        actions.append({
            "action_id": f"r{normalized_round}_a{idx + 1}",
            "action_type": action_type,
            "query": query,
            "source_sub_question_idx": 0,
            "priority": max(1, len(raw_actions) - idx),
        })

    return actions


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _dedup_actions(
    action_candidates: List[MemoryAction],
    max_actions: int,
    previous_action_signatures: Set[str],
) -> List[MemoryAction]:
    deduped: List[MemoryAction] = []
    local_signatures: Set[str] = set()
    for action in sorted(action_candidates, key=lambda item: int(item.get("priority", 0)), reverse=True):
        sig = action_signature(action)
        if sig in local_signatures or sig in previous_action_signatures:
            continue
        local_signatures.add(sig)
        deduped.append(action)
        if len(deduped) >= max_actions:
            break
    return deduped


def action_signature(action: MemoryAction) -> str:
    action_type = str(action.get("action_type", "")).strip()
    query = action.get("query", {})
    if not isinstance(query, dict):
        query = {}
    query_json = json.dumps(query, ensure_ascii=False, sort_keys=True)
    return f"{action_type}|{query_json}"


def _extract_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if hasattr(response, "content"):
        return str(response.content)
    return str(response)


def _extract_time_range(question_text: str) -> Optional[Dict[str, str]]:
    if not question_text:
        return None
    hits = _ISO_DATE_PATTERN.findall(question_text)
    if len(hits) < 2:
        return None
    return {
        "start": hits[0],
        "end": hits[1],
    }


def _requires_quote_verification(question_text: str) -> bool:
    normalized = str(question_text or "").strip().lower()
    if not normalized:
        return False
    return any(token in normalized for token in _QUOTE_HINTS)


def _classify_question_type(question_text: str, constraints: Dict[str, Any]) -> str:
    lowered = str(question_text or "").strip().lower()
    if not lowered:
        return "direct_lookup"
    if isinstance(constraints.get("time_range"), dict):
        return "temporal"
    if any(token in lowered for token in ("when", "before", "after", "timeline", "date", "time")):
        return "temporal"
    if any(token in lowered for token in ("why", "reason", "because", "cause")):
        return "causal"
    if any(token in lowered for token in ("compare", "difference", "different", "similar")):
        return "comparison"
    if any(token in lowered for token in ("how many", "count", "number of")):
        return "counting"
    if any(token in lowered for token in ("summary", "summarize", "overview")):
        return "summary"
    return "direct_lookup"
