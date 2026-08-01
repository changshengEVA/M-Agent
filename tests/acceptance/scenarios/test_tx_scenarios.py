from __future__ import annotations

import pytest

from ._support import run_contract


pytestmark = pytest.mark.acceptance


def test_tx_01_new_transaction_normal_flow(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "TX-01", "TX-01/core")


def test_tx_01_uow_replay_and_result(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(
        request,
        "TX-01",
        "TX-01/uow_replay_and_result",
    )


def test_tx_01_poc_checkpoint_resume(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(
        request,
        "TX-01",
        "TX-01/poc_checkpoint_resume",
    )


def test_tx_01_poc_sequential_fake_effects(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(
        request,
        "TX-01",
        "TX-01/poc_sequential_fake_effects",
    )


def test_tx_01_poc_stale_feedback_after_restore(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(
        request,
        "TX-01",
        "TX-01/poc_stale_feedback_after_restore",
    )


def test_tx_02_ui_pause_and_restore(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "TX-02", "TX-02/core")


def test_tx_03_complete_before_flush_reactivation(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "TX-03", "TX-03/core")


def test_tx_04_archive_only_by_flush(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "TX-04", "TX-04/core")


def test_tx_05_explicit_archive_restore(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "TX-05", "TX-05/core")


def test_tx_06_scheduled_plan_uses_original_transaction(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "TX-06", "TX-06/core")


def test_tx_06_schedule_atomicity_and_control_recovery(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(
        request,
        "TX-06",
        "TX-06/schedule_atomicity_and_control_recovery",
    )


def test_tx_07_feedback_causal_validation(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "TX-07", "TX-07/core")


def test_tx_07_effect_result_feedback_outbox(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(
        request,
        "TX-07",
        "TX-07/effect_result_feedback_outbox",
    )


def test_tx_07_capability_delivery_guarantees(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(
        request,
        "TX-07",
        "TX-07/capability_delivery_guarantees",
    )


def test_tx_08_transaction_isolation_and_delete(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "TX-08", "TX-08/core")


def test_tx_09_single_conversation_scene(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "TX-09", "TX-09/core")


def test_tx_10_flush_lifecycle(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "TX-10", "TX-10/core")


def test_tx_10_flush_fault_recovery(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(
        request,
        "TX-10",
        "TX-10/flush_fault_recovery",
    )
