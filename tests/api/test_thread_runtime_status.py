from __future__ import annotations

from m_agent.api.thread_runtime_status import THREAD_RUNTIME_STATUS
from m_agent.runtime.think_life.scheduler.cpu_state import THREAD_CPU_STATE


def test_thread_runtime_processing_when_in_flight() -> None:
    tid = "thread-in-flight"
    THREAD_RUNTIME_STATUS.set_pending_stimuli(tid, 0)
    THREAD_CPU_STATE.set_in_flight(tid, stimulus_id="stim_1", transaction_id="txn_abc", priority=10)
    snap = THREAD_RUNTIME_STATUS.snapshot(tid)
    assert snap.runtime_phase == "processing"
    assert snap.effective_depth == 1
    assert snap.busy is False
    assert snap.in_flight_stimulus_id == "stim_1"
    THREAD_CPU_STATE.clear_in_flight(tid)


def test_thread_runtime_not_busy_when_only_queued() -> None:
    tid = "thread-queued-only"
    THREAD_RUNTIME_STATUS.set_pending_stimuli(tid, 3)
    THREAD_RUNTIME_STATUS.set_drainer_active(tid, False)
    snap = THREAD_RUNTIME_STATUS.snapshot(tid)
    assert snap.busy is True
    assert snap.runtime_phase == "busy"
    assert snap.effective_depth == 3
    assert snap.pending_stimuli == 3


def test_thread_runtime_busy_when_queued_and_in_flight() -> None:
    tid = "thread-busy-depth"
    THREAD_RUNTIME_STATUS.set_pending_stimuli(tid, 2)
    THREAD_CPU_STATE.set_in_flight(tid, stimulus_id="stim_x", transaction_id="txn_x", priority=10)
    snap = THREAD_RUNTIME_STATUS.snapshot(tid)
    assert snap.busy is True
    assert snap.runtime_phase == "busy"
    assert snap.effective_depth == 3
    THREAD_CPU_STATE.clear_in_flight(tid)
