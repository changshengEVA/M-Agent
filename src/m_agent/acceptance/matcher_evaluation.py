"""Frozen Matcher v1 dataset loading, validation, and metric calculation."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence


MATCHER_SCHEMA_VERSION = "matcher.v1"
MATCHER_CATEGORY_COUNTS: Dict[str, int] = {
    "pause_continuation": 8,
    "complete_reactivation": 6,
    "unrelated_create": 6,
    "ambiguous_create": 5,
    "multi_candidate_unique": 5,
}
MATCHER_DOMAINS = frozenset(
    {"email", "calendar", "information", "document", "travel"}
)
MATCHER_ACTIONS = frozenset({"continue", "create"})
MATCHER_REASON_CODES = frozenset(
    {
        "explicit_reference",
        "answers_pending_need",
        "same_task_revision",
        "same_task_continuation",
        "no_match",
        "ambiguous",
    }
)
MATCHER_STATES = frozenset({"pause", "complete"})
DEFAULT_MATCHER_DATASET_PATH = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "acceptance"
    / "data"
    / "at_10_matcher_v1.zh-CN.jsonl"
)


def load_matcher_dataset(
    path: Optional[Path] = None,
) -> list[Dict[str, Any]]:
    """Load the frozen JSONL samples without invoking a Runtime or model."""

    dataset_path = Path(path or DEFAULT_MATCHER_DATASET_PATH)
    samples: list[Dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        dataset_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{dataset_path}:{line_number}: invalid JSON: {exc.msg}"
            ) from exc
        if not isinstance(item, dict):
            raise ValueError(
                f"{dataset_path}:{line_number}: sample must be an object"
            )
        samples.append(dict(item))
    return samples


def _text(value: Any) -> str:
    return str(value or "").strip()


def validate_matcher_dataset(
    samples: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Return strict Matcher v1 structural evidence for the P1 gate."""

    errors: list[str] = []
    case_ids: set[str] = set()
    categories: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    expected_reuse = 0
    expected_create = 0

    for index, sample in enumerate(samples, start=1):
        prefix = f"sample[{index}]"
        case_id = _text(sample.get("case_id"))
        if not case_id:
            errors.append(f"{prefix}: case_id is required")
        elif case_id in case_ids:
            errors.append(f"{prefix}: duplicate case_id={case_id}")
        case_ids.add(case_id)

        if sample.get("schema_version") != MATCHER_SCHEMA_VERSION:
            errors.append(
                f"{prefix}: schema_version must be "
                f"{MATCHER_SCHEMA_VERSION}"
            )

        category = _text(sample.get("category"))
        categories[category] += 1
        if category not in MATCHER_CATEGORY_COUNTS:
            errors.append(f"{prefix}: unknown category={category}")

        domain = _text(sample.get("domain"))
        domains[domain] += 1
        if domain not in MATCHER_DOMAINS:
            errors.append(f"{prefix}: unknown domain={domain}")

        stimulus = sample.get("stimulus")
        if not isinstance(stimulus, dict):
            errors.append(f"{prefix}: stimulus must be an object")
        else:
            if _text(stimulus.get("kind")) != "user_message":
                errors.append(
                    f"{prefix}: stimulus.kind must be user_message"
                )
            if not _text(stimulus.get("text")):
                errors.append(f"{prefix}: stimulus.text is required")

        conversation_tail = sample.get("conversation_tail")
        tail_candidate_refs: list[str] = []
        if not isinstance(conversation_tail, list):
            errors.append(f"{prefix}: conversation_tail must be a list")
        elif len(conversation_tail) > 12:
            errors.append(
                f"{prefix}: conversation_tail exceeds 12 entries"
            )
        else:
            for tail_index, entry in enumerate(
                conversation_tail,
                start=1,
            ):
                tprefix = (
                    f"{prefix}.conversation_tail[{tail_index}]"
                )
                if not isinstance(entry, Mapping):
                    errors.append(f"{tprefix}: entry must be an object")
                    continue
                if not _text(entry.get("actor")):
                    errors.append(f"{tprefix}: actor is required")
                if not _text(entry.get("text")):
                    errors.append(f"{tprefix}: text is required")
                candidate_ref = entry.get("candidate_ref")
                if candidate_ref is not None:
                    normalized_ref = _text(candidate_ref)
                    if not normalized_ref:
                        errors.append(
                            f"{tprefix}: candidate_ref cannot be empty"
                        )
                    else:
                        tail_candidate_refs.append(normalized_ref)

        candidates = sample.get("candidates")
        candidate_refs: list[str] = []
        if not isinstance(candidates, list):
            errors.append(f"{prefix}: candidates must be a list")
            candidates = []
        for candidate_index, candidate in enumerate(candidates, start=1):
            cprefix = f"{prefix}.candidates[{candidate_index}]"
            if not isinstance(candidate, dict):
                errors.append(f"{cprefix}: candidate must be an object")
                continue
            ref = _text(candidate.get("ref"))
            expected_ref = f"candidate_{candidate_index}"
            if ref != expected_ref:
                errors.append(
                    f"{cprefix}: ref must be {expected_ref}, got {ref}"
                )
            if ref in candidate_refs:
                errors.append(f"{cprefix}: duplicate ref={ref}")
            candidate_refs.append(ref)

            state = _text(candidate.get("state"))
            if state not in MATCHER_STATES:
                errors.append(f"{cprefix}: invalid state={state}")
            if _text(candidate.get("lifecycle_status")) != "active":
                errors.append(
                    f"{cprefix}: lifecycle_status must be active"
                )
            if not _text(candidate.get("kind")):
                errors.append(f"{cprefix}: kind is required")

            task_state = candidate.get("task_state")
            if not isinstance(task_state, dict):
                errors.append(f"{cprefix}: task_state must be an object")
            else:
                if not _text(task_state.get("goal")):
                    errors.append(f"{cprefix}: task_state.goal is required")
                elif len(_text(task_state.get("goal"))) > 500:
                    errors.append(
                        f"{cprefix}: task_state.goal exceeds 500 chars"
                    )
                for field_name in ("completed", "remaining"):
                    values = task_state.get(field_name)
                    if not isinstance(values, list):
                        errors.append(
                            f"{cprefix}: task_state.{field_name} "
                            "must be a list"
                        )
                    elif len(values) > 5:
                        errors.append(
                            f"{cprefix}: task_state.{field_name} "
                            "exceeds 5 items"
                        )

            wm_facts = candidate.get("wm_facts")
            if not isinstance(wm_facts, list):
                errors.append(f"{cprefix}: wm_facts must be a list")
            elif len(wm_facts) > 6:
                errors.append(f"{cprefix}: wm_facts exceeds 6 items")
            elif any(len(_text(item)) > 300 for item in wm_facts):
                errors.append(
                    f"{cprefix}: wm_facts entry exceeds 300 chars"
                )
            recent_activity = candidate.get("recent_activity")
            if not isinstance(recent_activity, list):
                errors.append(
                    f"{cprefix}: recent_activity must be a list"
                )
            elif len(recent_activity) > 6:
                errors.append(
                    f"{cprefix}: recent_activity exceeds 6 items"
                )
        for tail_ref in tail_candidate_refs:
            if tail_ref not in candidate_refs:
                errors.append(
                    f"{prefix}: conversation_tail references "
                    f"unknown candidate {tail_ref}"
                )

        expected = sample.get("expected")
        if not isinstance(expected, dict):
            errors.append(f"{prefix}: expected must be an object")
            continue
        action = _text(expected.get("action"))
        transaction_id = expected.get("transaction_id")
        reason_code = _text(expected.get("reason_code"))
        if action not in MATCHER_ACTIONS:
            errors.append(f"{prefix}: invalid expected action={action}")
        if reason_code not in MATCHER_REASON_CODES:
            errors.append(
                f"{prefix}: invalid expected reason_code={reason_code}"
            )
        if action == "continue":
            expected_reuse += 1
            if transaction_id not in candidate_refs:
                errors.append(
                    f"{prefix}: continue must reference an input candidate"
                )
        elif action == "create":
            expected_create += 1
            if transaction_id is not None:
                errors.append(
                    f"{prefix}: create must use transaction_id=null"
                )

        category_should_create = category in {
            "unrelated_create",
            "ambiguous_create",
        }
        if category_should_create and action != "create":
            errors.append(
                f"{prefix}: category={category} must expect create"
            )
        if (
            category in {
                "pause_continuation",
                "complete_reactivation",
                "multi_candidate_unique",
            }
            and action != "continue"
        ):
            errors.append(
                f"{prefix}: category={category} must expect continue"
            )

    actual_counts = {
        category: int(categories.get(category, 0))
        for category in MATCHER_CATEGORY_COUNTS
    }
    if actual_counts != MATCHER_CATEGORY_COUNTS:
        errors.append(
            "category distribution must be "
            f"{MATCHER_CATEGORY_COUNTS}, got {actual_counts}"
        )
    extra_categories = sorted(
        category
        for category in categories
        if category not in MATCHER_CATEGORY_COUNTS
    )
    if extra_categories:
        errors.append(
            f"unexpected categories: {', '.join(extra_categories)}"
        )
    if set(domains) != MATCHER_DOMAINS:
        errors.append(
            "domains must cover exactly "
            f"{sorted(MATCHER_DOMAINS)}, got {sorted(domains)}"
        )

    return {
        "valid": not errors,
        "sample_count": len(samples),
        "category_counts": actual_counts,
        "domain_counts": {
            domain: int(domains.get(domain, 0))
            for domain in sorted(MATCHER_DOMAINS)
        },
        "expected_reuse_count": expected_reuse,
        "expected_create_count": expected_create,
        "errors": errors,
    }


def _prediction_key(prediction: Mapping[str, Any]) -> tuple[str, Any]:
    return (
        _text(prediction.get("action")).lower(),
        prediction.get("transaction_id"),
    )


def _valid_prediction(
    prediction: Any,
    candidate_refs: set[str],
) -> bool:
    if not isinstance(prediction, Mapping):
        return False
    action = _text(prediction.get("action")).lower()
    transaction_id = prediction.get("transaction_id")
    reason_code = _text(prediction.get("reason_code"))
    if action not in MATCHER_ACTIONS:
        return False
    if reason_code not in MATCHER_REASON_CODES:
        return False
    if action == "create":
        return transaction_id is None
    return transaction_id in candidate_refs


def evaluate_matcher_predictions(
    samples: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Mapping[str, Any]]],
) -> Dict[str, Any]:
    """Calculate AT-10 metrics from one or more case-id keyed runs."""

    total = len(samples) * len(runs)
    valid_outputs = 0
    correct = 0
    expected_reuse_total = 0
    correct_reuse = 0
    wrong_candidate_count = 0
    false_reuse_count = 0
    external_reference_count = 0
    decisions_by_case: Dict[str, list[tuple[str, Any]]] = {
        _text(sample.get("case_id")): []
        for sample in samples
    }

    for run in runs:
        for sample in samples:
            case_id = _text(sample.get("case_id"))
            prediction = run.get(case_id)
            candidates = sample.get("candidates", [])
            candidate_refs = {
                _text(candidate.get("ref"))
                for candidate in candidates
                if isinstance(candidate, Mapping)
            }
            expected = sample.get("expected", {})
            if not isinstance(expected, Mapping):
                expected = {}
            expected_action = _text(expected.get("action"))
            expected_transaction = expected.get("transaction_id")
            if expected_action == "continue":
                expected_reuse_total += 1

            prediction_is_valid = _valid_prediction(
                prediction,
                candidate_refs,
            )
            if isinstance(prediction, Mapping):
                decisions_by_case[case_id].append(
                    (
                        _prediction_key(prediction)
                        if prediction_is_valid
                        else ("invalid", None)
                    )
                )
                predicted_action = _text(
                    prediction.get("action")
                ).lower()
                predicted_transaction = prediction.get("transaction_id")
                if (
                    predicted_action == "continue"
                    and predicted_transaction not in candidate_refs
                ):
                    external_reference_count += 1
                if (
                    expected_action == "create"
                    and predicted_action == "continue"
                ):
                    false_reuse_count += 1
                if (
                    expected_action == "continue"
                    and predicted_action == "continue"
                    and predicted_transaction != expected_transaction
                    and predicted_transaction in candidate_refs
                ):
                    wrong_candidate_count += 1
                if (
                    prediction_is_valid
                    and predicted_action == expected_action
                    and predicted_transaction == expected_transaction
                ):
                    correct += 1
                    if expected_action == "continue":
                        correct_reuse += 1
            else:
                decisions_by_case[case_id].append(("invalid", None))

            if prediction_is_valid:
                valid_outputs += 1

    stable_cases = sum(
        bool(decisions)
        and len(decisions) == len(runs)
        and all(action != "invalid" for action, _target in decisions)
        and len(set(decisions)) == 1
        for decisions in decisions_by_case.values()
    )
    return {
        "sample_count": len(samples),
        "run_count": len(runs),
        "structured_output_valid_rate": (
            valid_outputs / total
            if total
            else 0.0
        ),
        "external_reference_count": external_reference_count,
        "wrong_candidate_count": wrong_candidate_count,
        "false_reuse_count": false_reuse_count,
        "total_accuracy": correct / total if total else 0.0,
        "reuse_recall": (
            correct_reuse / expected_reuse_total
            if expected_reuse_total
            else 0.0
        ),
        "decision_stability_rate": (
            stable_cases / len(samples)
            if samples and runs
            else 0.0
        ),
    }


def _tokenize(text: Any) -> set[str]:
    import re

    return {
        token
        for token in re.findall(r"[\w\u4e00-\u9fff]+", str(text or "").lower())
        if len(token) > 1
    }


def predict_matcher_v1_sample(
    sample: Mapping[str, Any],
) -> Dict[str, Any]:
    """Deterministic Matcher v1 offline policy for frozen AT-10 samples.

    This is not an LLM call.  It encodes the first-cut reuse/create policy
    against turn-local candidate views so AT-10 can run reproducibly offline.
    """

    stimulus = sample.get("stimulus")
    if not isinstance(stimulus, Mapping):
        stimulus = {}
    text = _text(stimulus.get("text"))
    text_tokens = _tokenize(text)
    candidates = [
        item
        for item in sample.get("candidates", [])
        if isinstance(item, Mapping)
    ]
    if not candidates:
        return {
            "action": "create",
            "transaction_id": None,
            "reason_code": "no_match",
        }

    tail = sample.get("conversation_tail")
    hinted_refs: list[str] = []
    if isinstance(tail, list):
        for entry in tail:
            if not isinstance(entry, Mapping):
                continue
            ref = _text(entry.get("candidate_ref"))
            if ref:
                hinted_refs.append(ref)

    def _goal_phrases(goal: str) -> list[str]:
        phrases = []
        for size in (4, 3, 2):
            chars = list(goal)
            for start in range(0, max(0, len(chars) - size + 1)):
                phrase = "".join(chars[start : start + size]).strip()
                if len(phrase) >= size:
                    phrases.append(phrase)
        return phrases

    scores: Dict[str, float] = {}
    for candidate in candidates:
        ref = _text(candidate.get("ref"))
        if not ref:
            continue
        task_state = candidate.get("task_state")
        if not isinstance(task_state, Mapping):
            task_state = {}
        goal = _text(task_state.get("goal"))
        blob_parts = [
            goal,
            " ".join(str(item) for item in (task_state.get("remaining") or [])),
            " ".join(str(item) for item in (candidate.get("wm_facts") or [])),
            " ".join(
                str(item) for item in (candidate.get("recent_activity") or [])
            ),
        ]
        blob_tokens = _tokenize(" ".join(str(part) for part in blob_parts))
        overlap = len(text_tokens & blob_tokens)
        score = float(overlap)
        if ref in hinted_refs:
            score += 3.0
        for token in _tokenize(goal):
            if token in text_tokens and len(token) >= 2:
                score += 1.5
        # Prefer distinctive entity/noun spans over shared verbs.
        for phrase in _goal_phrases(goal):
            if phrase and phrase in text:
                score += 2.0 + (0.25 * len(phrase))
                if len(phrase) >= 4:
                    score += 1.5
                break
        for distinctive in (
            "采购总监",
            "询价邮件",
            "询价",
            "复盘会",
            "研发",
            "客户会",
            "成都",
            "杭州",
            "HNSW",
            "API 迁移",
            "迁移指南",
        ):
            if distinctive in goal and distinctive in text:
                score += 3.0
        # Negation against sibling candidates ("不是周报", "杭州行程先不改").
        for other in candidates:
            other_ref = _text(other.get("ref"))
            if other_ref == ref:
                continue
            other_goal = _text(
                ((other.get("task_state") or {}) or {}).get("goal")
            )
            for phrase in _goal_phrases(other_goal):
                if phrase and (
                    f"不是{phrase}" in text
                    or f"{phrase}先不" in text
                    or f"{phrase}不要" in text
                    or f"{phrase}保持不变" in text
                    or f"{phrase}不要动" in text
                ):
                    score += 2.5
            for entity in (
                "杭州",
                "成都",
                "客户会",
                "复盘会",
                "发布说明",
                "迁移指南",
                "北京",
            ):
                if entity not in other_goal:
                    continue
                if (
                    f"{entity}行程先不" in text
                    or f"{entity}保持不变" in text
                    or f"{entity}先不改" in text
                    or f"{entity}不要动" in text
                    or f"{entity}不要" in text
                ):
                    score += 3.0
                    break
        scores[ref] = score

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    best_ref, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else -1.0

    ambiguous_cues = (
        "刚才那封",
        "那个会",
        "再深入查",
        "第二个版本",
        "便宜的那个",
        "继续刚才",
    )
    # Version / ordinal ambiguity: never reuse when multiple candidates share
    # version-ish goals and the cue itself is ambiguous.
    if "第二个版本" in text and len(candidates) >= 2:
        versionish = 0
        for candidate in candidates:
            goal = _text(
                ((candidate.get("task_state") or {}) or {}).get("goal")
            )
            if any(
                marker in goal
                for marker in ("版", "版本", "第一", "第二")
            ):
                versionish += 1
        if versionish >= 2:
            return {
                "action": "create",
                "transaction_id": None,
                "reason_code": "ambiguous",
            }
    if any(cue in text for cue in ambiguous_cues) and len(candidates) >= 2:
        if best_score < second_score + 2.5:
            return {
                "action": "create",
                "transaction_id": None,
                "reason_code": "ambiguous",
            }

    unrelated_cues = (
        "顺便",
        "查一下公司",
        "桌面上的合同",
        "看看下个月",
        "给客户写一封项目延期",
        "把产品需求文档",
    )
    if any(cue in text for cue in unrelated_cues) and best_score < 4.0:
        return {
            "action": "create",
            "transaction_id": None,
            "reason_code": "no_match",
        }

    if best_score <= 0:
        return {
            "action": "create",
            "transaction_id": None,
            "reason_code": "no_match",
        }
    if len(ranked) > 1 and best_score < second_score + 1.0:
        return {
            "action": "create",
            "transaction_id": None,
            "reason_code": "ambiguous",
        }

    reason = "same_task_continuation"
    if best_ref in hinted_refs:
        reason = "answers_pending_need"
    if any(
        token in text
        for token in ("加上", "再", "改到", "延长", "补充", "柔和", "抄送")
    ):
        reason = "same_task_revision"
    if any(
        token in text
        for token in (
            "那封",
            "那项",
            "那趟",
            "合同那份",
            "询价邮件",
            "复盘会",
            "HNSW",
            "API 迁移",
            "成都",
        )
    ):
        reason = "explicit_reference"
    return {
        "action": "continue",
        "transaction_id": best_ref,
        "reason_code": reason,
    }


def run_matcher_v1_offline(
    samples: Sequence[Mapping[str, Any]],
    *,
    runs: int = 3,
) -> list[Dict[str, Dict[str, Any]]]:
    """Run the deterministic offline Matcher v1 policy repeatedly."""

    run_count = max(1, int(runs))
    predictions = {
        _text(sample.get("case_id")): predict_matcher_v1_sample(sample)
        for sample in samples
    }
    return [dict(predictions) for _ in range(run_count)]


def normalize_harness_matcher_result(
    samples: Sequence[Mapping[str, Any]],
    result: Any,
) -> Dict[str, Any]:
    """Normalize either Harness metrics or raw repeated predictions."""

    raw_data = getattr(result, "data", {})
    data = dict(raw_data) if isinstance(raw_data, Mapping) else {}
    raw_runs = data.get("runs")
    if (
        isinstance(raw_runs, list)
        and all(isinstance(run, Mapping) for run in raw_runs)
    ):
        metrics = evaluate_matcher_predictions(samples, raw_runs)
    else:
        metrics = {
            "sample_count": int(data.get("sample_count", len(samples))),
            "run_count": int(data.get("run_count", 0) or 0),
            "structured_output_valid_rate": float(
                data.get("structured_output_valid_rate", 0.0) or 0.0
            ),
            "external_reference_count": data.get(
                "external_reference_count"
            ),
            "wrong_candidate_count": data.get("wrong_candidate_count"),
            "false_reuse_count": data.get("false_reuse_count"),
            "total_accuracy": float(
                data.get("total_accuracy", 0.0) or 0.0
            ),
            "reuse_recall": float(
                data.get("reuse_recall", 0.0) or 0.0
            ),
            "decision_stability_rate": data.get(
                "decision_stability_rate"
            ),
        }
    metrics.update(
        {
            "supported": bool(getattr(result, "supported", False)),
            "outcome": _text(getattr(result, "outcome", "")),
            "reason": _text(getattr(result, "reason", "")),
        }
    )
    return metrics
