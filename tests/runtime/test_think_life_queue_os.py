from __future__ import annotations

from m_agent.api.thread_runtime_status import THREAD_RUNTIME_STATUS
from m_agent.runtime.think_life.config import ThinkLifeConfig, ThinkLifeSchedulerConfig
from m_agent.runtime.think_life.perception.inbox import StimulusInbox
from m_agent.runtime.think_life.scheduler.cpu_state import THREAD_CPU_STATE, compute_runtime_phase


def test_compute_runtime_phase_depths() -> None:
    assert compute_runtime_phase(inbox_pending=0, in_flight=False) == (0, "ready")
    assert compute_runtime_phase(inbox_pending=0, in_flight=True) == (1, "processing")
    assert compute_runtime_phase(inbox_pending=2, in_flight=False) == (2, "busy")
    assert compute_runtime_phase(inbox_pending=1, in_flight=True) == (2, "busy")


def test_inbox_peek_next_priority() -> None:
    from m_agent.layers.perception.contracts import Stimulus, StimulusKind
    from m_agent.runtime.think_life.contracts import StimulusEnvelope

    inbox = StimulusInbox()
    inbox.push(
        StimulusEnvelope(
            stimulus_id="low",
            thread_id="t1",
            conversation_id="t1::0",
            stimulus=Stimulus(
                kind=StimulusKind.SCHEDULED_PLAN,
                text="scheduled plan",
                payload={},
            ),
            occurred_at="2026-01-01T00:00:00Z",
        ),
        priority=40,
    )
    inbox.push(
        StimulusEnvelope(
            stimulus_id="high",
            thread_id="t1",
            conversation_id="t1::0",
            stimulus=Stimulus(
                kind=StimulusKind.USER_MESSAGE,
                text="user message",
                payload={},
            ),
            occurred_at="2026-01-01T00:00:01Z",
        ),
        priority=10,
    )
    assert inbox.peek_next_priority("t1") == 10


def test_config_max_preempt_loaded() -> None:
    cfg = ThinkLifeConfig(
        scheduler=ThinkLifeSchedulerConfig(preempt_enabled=True, max_preempt_per_stimulus=5),
    )
    assert cfg.scheduler.max_preempt_per_stimulus == 5


def test_effective_depth_not_double_counted_after_pop() -> None:
    """After pop, pending_stimuli must be 0 so in_flight alone yields processing not busy."""
    tid = "thread-after-pop"
    THREAD_RUNTIME_STATUS.set_runtime_profile(tid, "think_life")
    THREAD_RUNTIME_STATUS.set_pending_stimuli(tid, 0)
    THREAD_CPU_STATE.set_in_flight(tid, stimulus_id="stim_u", transaction_id="txn_u", priority=10)
    snap = THREAD_RUNTIME_STATUS.snapshot(tid, default_profile="think_life")
    assert snap.effective_depth == 1
    assert snap.runtime_phase == "processing"
    assert snap.busy is False
    THREAD_CPU_STATE.clear_in_flight(tid)


def test_thread_runtime_ready_after_clear_in_flight() -> None:
    tid = "thread-ready"
    THREAD_RUNTIME_STATUS.set_runtime_profile(tid, "think_life")
    THREAD_RUNTIME_STATUS.set_pending_stimuli(tid, 0)
    THREAD_CPU_STATE.set_in_flight(tid, stimulus_id="s", transaction_id="t", priority=10)
    THREAD_CPU_STATE.clear_in_flight(tid)
    snap = THREAD_RUNTIME_STATUS.snapshot(tid, default_profile="think_life")
    assert snap.runtime_phase == "ready"
    assert snap.effective_depth == 0
