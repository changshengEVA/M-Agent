"""State + data contracts for the thinking layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Dict, List, Optional


SILENT_MODES = frozenset({"silent", "wait", "defer"})


def normalize_thinking_mode(mode: Any) -> str:
    """Return a canonical planning mode string."""
    value = str(mode or "").strip().lower()
    if value in SILENT_MODES:
        return "silent"
    if value == "execute":
        return "execute"
    if value in {"answer_directly", "reply"}:
        return "answer_directly"
    return value or "answer_directly"


def is_silent_mode(mode: Any) -> bool:
    return normalize_thinking_mode(mode) == "silent"


def is_execute_mode(mode: Any) -> bool:
    return normalize_thinking_mode(mode) == "execute"


def is_reply_mode(mode: Any) -> bool:
    return normalize_thinking_mode(mode) == "answer_directly"


def request_is_complete(decision: "ThinkingDecision") -> bool:
    return bool(getattr(decision, "request_complete", None))


@dataclass
class ThinkingDecision:
    """Structured output of the thinking layer's planning pass.

    ``mode`` is one of:

    * ``"execute"`` — delegate one tool via the execution layer.
    * ``"answer_directly"`` — send a user-visible reply (Think-life: ``reply_to_user``).
    * ``"silent"`` — neither delegate nor reply; keep the transaction open unless
      ``request_complete`` is true.

    At most one of ``instruction`` or ``answer`` is populated, matching the
    chosen mode. ``episode_note`` is optional and appended to the episode buffer.
    """

    mode: str = "answer_directly"
    tool_name: Optional[str] = None
    instruction: Optional[str] = None
    answer: Optional[str] = None
    episode_note: Optional[str] = None
    capability_hint: Optional[List[str]] = None
    request_complete: Optional[bool] = None
    reasoning: Optional[str] = None


@dataclass
class ThinkingSummary:
    """Structured output of the thinking layer's summarize pass (after execution).

    Only used when ``ThinkingDecision.mode == "execute"``: after the execution
    layer returns, the thinking layer runs a second LLM pass that produces the
    final user-facing answer and (optionally) a fresh ``episode_note``.
    """

    answer: str = ""
    episode_note: Optional[str] = None


@dataclass
class ConversationState:
    """Per-conversation in-memory state owned by the thinking layer.

    A conversation is the interval between two flush events; both
    ``wm_entries`` (working memory) and ``episode_buffer`` are cleared when
    :py:meth:`reset` is called. The state is intentionally NOT persisted
    across process restarts — the long-term store is the MemoryCore-backed
    episode library, populated via ``EpisodeRecorder.flush``.
    """

    conversation_id: str
    thread_id: str
    wm_entries: List[Dict[str, Any]] = field(default_factory=list)
    episode_buffer: List[Dict[str, Any]] = field(default_factory=list)
    turn_count: int = 0

    def reset(self) -> None:
        """Clear working memory and episode buffer for this conversation."""
        self.wm_entries.clear()
        self.episode_buffer.clear()
        self.turn_count = 0


class ConversationStateRegistry:
    """Thread-safe registry mapping ``conversation_id`` -> :class:`ConversationState`.

    The thinking layer keeps one of these per process. The perception layer
    derives a ``conversation_id`` from ``thread_id`` plus a flush marker, so
    a single thread can roll over to a fresh conversation by bumping the marker.
    """

    def __init__(self) -> None:
        self._states: Dict[str, ConversationState] = {}
        self._lock = RLock()

    def get(self, conversation_id: str) -> Optional[ConversationState]:
        with self._lock:
            return self._states.get(conversation_id)

    def get_or_create(self, conversation_id: str, *, thread_id: str) -> ConversationState:
        with self._lock:
            state = self._states.get(conversation_id)
            if state is None:
                state = ConversationState(conversation_id=conversation_id, thread_id=thread_id)
                self._states[conversation_id] = state
            return state

    def drop(self, conversation_id: str) -> Optional[ConversationState]:
        with self._lock:
            return self._states.pop(conversation_id, None)

    def snapshot(self, conversation_id: str) -> Optional[ConversationState]:
        """Return a deep-ish copy of the state for read-only callers (e.g. thread_state API).

        We return the same dataclass instance — callers MUST treat lists as
        read-only. For now this avoids deepcopy cost; if a writer pattern emerges,
        upgrade to deepcopy.
        """
        return self.get(conversation_id)
