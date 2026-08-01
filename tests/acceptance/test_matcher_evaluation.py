from __future__ import annotations

from copy import deepcopy

from m_agent.acceptance.matcher_evaluation import (
    MATCHER_CATEGORY_COUNTS,
    MATCHER_DOMAINS,
    evaluate_matcher_predictions,
    load_matcher_dataset,
    validate_matcher_dataset,
)


def _expected_runs(samples: list[dict], count: int = 3) -> list[dict]:
    prediction = {
        sample["case_id"]: deepcopy(sample["expected"])
        for sample in samples
    }
    return [deepcopy(prediction) for _index in range(count)]


def test_matcher_v1_dataset_is_frozen_and_valid() -> None:
    samples = load_matcher_dataset()
    result = validate_matcher_dataset(samples)

    assert result["valid"] is True
    assert result["errors"] == []
    assert result["sample_count"] == 30
    assert result["category_counts"] == MATCHER_CATEGORY_COUNTS
    assert set(result["domain_counts"]) == MATCHER_DOMAINS
    assert result["expected_reuse_count"] == 19
    assert result["expected_create_count"] == 11


def test_matcher_evaluator_reports_perfect_three_run_baseline() -> None:
    samples = load_matcher_dataset()

    metrics = evaluate_matcher_predictions(
        samples,
        _expected_runs(samples),
    )

    assert metrics == {
        "sample_count": 30,
        "run_count": 3,
        "structured_output_valid_rate": 1.0,
        "external_reference_count": 0,
        "wrong_candidate_count": 0,
        "false_reuse_count": 0,
        "total_accuracy": 1.0,
        "reuse_recall": 1.0,
        "decision_stability_rate": 1.0,
    }


def test_invalid_output_cannot_inflate_matcher_accuracy() -> None:
    samples = load_matcher_dataset()
    runs = _expected_runs(samples, count=1)
    first = samples[0]
    # The action/id still match, but the required reason_code is absent.
    runs[0][first["case_id"]].pop("reason_code")

    metrics = evaluate_matcher_predictions(samples, runs)

    assert metrics["structured_output_valid_rate"] == 29 / 30
    assert metrics["total_accuracy"] == 29 / 30
    assert metrics["reuse_recall"] == 18 / 19
    assert metrics["decision_stability_rate"] == 29 / 30
