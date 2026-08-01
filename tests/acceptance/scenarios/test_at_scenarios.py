"""Runtime-neutral AT attribution contract scenarios."""

from __future__ import annotations

import pytest

from ._support import run_contract


@pytest.mark.acceptance
def test_at_01_sourced_stimulus_validation(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "AT-01", "AT-01/core")


@pytest.mark.acceptance
def test_at_02_candidate_scope(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "AT-02", "AT-02/core")


@pytest.mark.acceptance
def test_at_03_match_paused_transaction(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "AT-03", "AT-03/core")


@pytest.mark.acceptance
def test_at_04_match_unflushed_complete(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "AT-04", "AT-04/core")


@pytest.mark.acceptance
def test_at_05_no_match_creates_transaction(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "AT-05", "AT-05/core")


@pytest.mark.acceptance
def test_at_06_ambiguous_match_creates_transaction(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "AT-06", "AT-06/core")


@pytest.mark.acceptance
def test_at_07_no_cross_conversation_match(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "AT-07", "AT-07/core")


@pytest.mark.acceptance
def test_at_08_attribution_stays_stable(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "AT-08", "AT-08/core")


@pytest.mark.acceptance
def test_at_09_deterministic_matcher_contract(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "AT-09", "AT-09/core")


@pytest.mark.acceptance
def test_at_10_real_matcher_evaluation(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(
        request,
        "AT-10",
        "AT-10/matcher_evaluation",
    )
