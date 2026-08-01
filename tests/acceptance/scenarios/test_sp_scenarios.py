from __future__ import annotations

import pytest

from ._support import run_contract


@pytest.mark.acceptance
def test_sp_01_admission_and_preconsume_gate(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "SP-01", "SP-01/core")


@pytest.mark.acceptance
def test_sp_02_priority_order(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "SP-02", "SP-02/core")


@pytest.mark.acceptance
def test_sp_03_atomic_acceptance_fifo(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "SP-03", "SP-03/core")


@pytest.mark.acceptance
def test_sp_04_serial_thinking_async_effect(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "SP-04", "SP-04/core")


@pytest.mark.acceptance
def test_sp_05_concurrent_producers(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "SP-05", "SP-05/core")


@pytest.mark.acceptance
def test_sp_06_worker_exit_wakeup(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "SP-06", "SP-06/core")


@pytest.mark.acceptance
def test_sp_07_next_selection_priority(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "SP-07", "SP-07/core")


@pytest.mark.acceptance
def test_sp_08_conversation_isolation(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(request, "SP-08", "SP-08/core")


@pytest.mark.acceptance
def test_sp_01_durable_ingress_restart(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(
        request,
        "SP-01",
        "SP-01/durable_ingress_restart",
    )


@pytest.mark.acceptance
def test_sp_01_lease_takeover_fencing(
    request: pytest.FixtureRequest,
) -> None:
    run_contract(
        request,
        "SP-01",
        "SP-01/lease_takeover_fencing",
    )
