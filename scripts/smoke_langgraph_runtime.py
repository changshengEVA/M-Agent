"""R1 internal smoke check for the production LangGraph runtime host.

Drives the non-acceptance production path end to end:
``create_runtime_host(runtime_engine="langgraph_v1")`` -> ``submit_user_message``
-> ``run_thread``, then cross-checks the Transaction Store, the Scene log and the
LangGraph checkpoint.  The R1 exit criteria are that ``runtime_engine``, working
memory, ``revision`` and ``graph_phase`` agree across those three surfaces, that
the run repeats without failure, and that the state survives a host restart on
the same persistence root.

The host runs with ``turn_loop`` off, so this script pins the MVP
``record_progress`` drain, which doubles as the R2 rollback target. The R2 turn
loop has its own entry point: ``scripts/smoke_langgraph_turn_loop.py``.

The agent is a stub with no model provider, so the run is offline and
deterministic.

Usage:
    python scripts/smoke_langgraph_runtime.py --rounds 5
    python scripts/smoke_langgraph_runtime.py --rounds 5 --verbose
    python scripts/smoke_langgraph_runtime.py --persist-root .tmp/lg-smoke --keep
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if _SRC_ROOT.is_dir() and str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from m_agent.runtime.host import create_runtime_host  # noqa: E402
from m_agent.runtime.langgraph.runtime import LangGraphRuntime  # noqa: E402
from m_agent.runtime.routing import LANGGRAPH_RUNTIME_ENGINE  # noqa: E402
from m_agent.runtime.domain.contracts import (  # noqa: E402
    SceneActor,
    TransactionRecord,
    TransactionState,
)
from m_agent.runtime.transaction.predicates import (  # noqa: E402
    project_compat_status,
)

EXPECTED_GRAPH_PHASE = "step_committed"
LANGGRAPH_EXTRA_HINT = 'install the checkpoint extra: pip install -e ".[langgraph]"'


class SmokeError(RuntimeError):
    """Raised when the smoke run cannot be performed at all."""


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class CheckLog:
    checks: List[Check] = field(default_factory=list)

    def expect(self, name: str, ok: Any, detail: str = "") -> bool:
        passed = bool(ok)
        self.checks.append(Check(name=name, ok=passed, detail=detail))
        return passed

    def expect_equal(self, name: str, actual: Any, expected: Any) -> bool:
        return self.expect(
            name,
            actual == expected,
            f"expected={expected!r} actual={actual!r}",
        )

    @property
    def failures(self) -> List[Check]:
        return [item for item in self.checks if not item.ok]


class _StubThinkingAgent:
    """Semantic resolver stub: always let the attributor open a new line."""

    def resolve_transaction(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _stub_agent(thread_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        thinking_agent=_StubThinkingAgent(),
        config={
            "runtime": {
                # R1 covers the MVP drain, which is also the R2 rollback target.
                "langgraph": {"turn_loop": False},
            }
        },
        user_name="User",
        assistant_name="Assistant",
        default_thread_id=thread_id,
        persist_memory=False,
    )


def _open_runtime(*, persist_root: Path, thread_id: str, owner_id: str) -> LangGraphRuntime:
    host = create_runtime_host(
        agent=_stub_agent(thread_id),  # type: ignore[arg-type]
        owner_id=owner_id,
        runtime_engine=LANGGRAPH_RUNTIME_ENGINE,
        persist_root=persist_root,
    )
    if not isinstance(host, LangGraphRuntime):
        raise SmokeError(
            "create_runtime_host did not return a LangGraphRuntime: "
            f"{type(host).__name__}"
        )
    return host


def _transaction_view(
    runtime: LangGraphRuntime,
    record: TransactionRecord,
) -> Dict[str, Any]:
    """Join one Store record with its LangGraph checkpoint state."""

    checkpoint = runtime.graph_engine.get_checkpoint_state(
        thread_id=record.transaction_id,
    )
    return {
        "transaction_id": record.transaction_id,
        "conversation_id": record.conversation_id,
        "runtime_engine": record.runtime_engine,
        "state": record.state.value,
        "status": project_compat_status(record),
        "revision": int(record.revision),
        "wm_entries": len(record.wm_entries),
        "goal": str(record.task_state.goal or ""),
        "checkpoint_phase": str(checkpoint.get("graph_phase", "") or ""),
        "checkpoint_revision": int(checkpoint.get("transaction_revision", -1)),
        "checkpoint_wm_entries": len(checkpoint.get("wm_snapshot") or []),
        "checkpoint_conversation_id": str(checkpoint.get("conversation_id", "") or ""),
    }


def _transaction_views(
    runtime: LangGraphRuntime,
    *,
    thread_id: str,
) -> List[Dict[str, Any]]:
    records = sorted(
        runtime.registry.list(thread_id=thread_id),
        key=lambda item: item.transaction_id,
    )
    return [_transaction_view(runtime, record) for record in records]


def _check_alignment(
    log: CheckLog,
    views: List[Dict[str, Any]],
    *,
    prefix: str,
) -> None:
    """Assert Store and checkpoint agree on engine, WM, revision and phase."""

    for index, view in enumerate(views, start=1):
        tag = f"{prefix}.tx{index}"
        log.expect_equal(f"{tag}.runtime_engine", view["runtime_engine"], LANGGRAPH_RUNTIME_ENGINE)
        log.expect_equal(f"{tag}.state", view["state"], TransactionState.CONTINUE.value)
        log.expect(
            f"{tag}.revision_advanced",
            view["revision"] > 0,
            f"revision={view['revision']}",
        )
        log.expect(
            f"{tag}.wm_not_empty",
            view["wm_entries"] > 0,
            f"wm_entries={view['wm_entries']}",
        )
        log.expect(f"{tag}.goal_recorded", bool(view["goal"]), f"goal={view['goal']!r}")
        log.expect_equal(f"{tag}.graph_phase", view["checkpoint_phase"], EXPECTED_GRAPH_PHASE)
        log.expect_equal(
            f"{tag}.checkpoint_revision_matches_store",
            view["checkpoint_revision"],
            view["revision"],
        )
        log.expect_equal(
            f"{tag}.checkpoint_wm_matches_store",
            view["checkpoint_wm_entries"],
            view["wm_entries"],
        )
        log.expect_equal(
            f"{tag}.checkpoint_conversation_id",
            view["checkpoint_conversation_id"],
            view["conversation_id"],
        )


def _check_turn_result(
    log: CheckLog,
    result: Dict[str, Any],
    *,
    tag: str,
) -> None:
    log.expect(f"{tag}.success", result.get("success") is True, json.dumps(result, default=str))
    log.expect_equal(f"{tag}.runtime_engine_id", result.get("runtime_engine_id"), LANGGRAPH_RUNTIME_ENGINE)
    results = list(result.get("results") or [])
    if not log.expect_equal(f"{tag}.result_count", len(results), 1):
        return
    entry = results[0]
    log.expect_equal(f"{tag}.graph_phase", entry.get("graph_phase"), EXPECTED_GRAPH_PHASE)
    log.expect_equal(f"{tag}.result_runtime_engine", entry.get("runtime_engine"), LANGGRAPH_RUNTIME_ENGINE)
    log.expect(
        f"{tag}.result_revision",
        int(entry.get("revision", 0)) > 0,
        f"revision={entry.get('revision')}",
    )


def _check_scene(
    log: CheckLog,
    runtime: LangGraphRuntime,
    *,
    conversation_id: str,
    expected_turns: int,
    prefix: str,
) -> int:
    entries = runtime.scene_system.reader.tail(conversation_id, limit=200)
    seqs = [int(item.seq) for item in entries]
    log.expect(
        f"{prefix}.scene.seq_strictly_increasing",
        all(later > earlier for earlier, later in zip(seqs, seqs[1:])),
        f"seqs={seqs}",
    )
    user_entries = [item for item in entries if item.actor == SceneActor.USER]
    work_entries = [item for item in entries if item.actor == SceneActor.WORK]
    log.expect_equal(f"{prefix}.scene.user_utterances", len(user_entries), expected_turns)
    log.expect_equal(f"{prefix}.scene.work_entries", len(work_entries), expected_turns)
    log.expect(
        f"{prefix}.scene.entries_bound_to_transaction",
        all(str(item.transaction_id or "").strip() for item in entries),
        "some Scene entries have no transaction_id",
    )
    return len(entries)


def _check_persistence_files(log: CheckLog, persist_root: Path) -> None:
    store_db = persist_root / "runtime" / "langgraph.sqlite3"
    checkpoint_db = persist_root / "runtime" / "langgraph-checkpoints.sqlite3"
    log.expect("persist.store_db_exists", store_db.is_file(), str(store_db))
    log.expect(
        "persist.checkpoint_db_persistent",
        checkpoint_db.is_file() and checkpoint_db.stat().st_size > 0,
        f"{checkpoint_db} missing or empty; {LANGGRAPH_EXTRA_HINT}",
    )


def _check_health(
    log: CheckLog,
    runtime: LangGraphRuntime,
    *,
    expected_transactions: int,
    prefix: str,
) -> None:
    health = runtime.health()
    log.expect_equal(f"{prefix}.health.profile", health.get("profile"), "langgraph")
    log.expect_equal(
        f"{prefix}.health.runtime_engine_id",
        health.get("runtime_engine_id"),
        LANGGRAPH_RUNTIME_ENGINE,
    )
    log.expect_equal(f"{prefix}.health.pending_stimuli", health.get("pending_stimuli"), 0)
    log.expect_equal(f"{prefix}.health.transactions", health.get("transactions"), expected_transactions)


def _drive_turn(
    log: CheckLog,
    runtime: LangGraphRuntime,
    *,
    thread_id: str,
    conversation_id: str,
    text: str,
    tag: str,
) -> Dict[str, Any]:
    stimulus_id = runtime.submit_user_message(
        thread_id=thread_id,
        conversation_id=conversation_id,
        text=text,
        schedule_drainer=False,
    )
    log.expect(f"{tag}.stimulus_id", bool(stimulus_id), f"stimulus_id={stimulus_id!r}")
    log.expect_equal(f"{tag}.pending_before_drain", runtime.inbox.pending_count(thread_id), 1)
    result = runtime.run_thread(thread_id)
    _check_turn_result(log, result, tag=tag)
    log.expect_equal(f"{tag}.pending_after_drain", runtime.inbox.pending_count(thread_id), 0)
    return result


def run_round(
    *,
    round_index: int,
    persist_root: Path,
    thread_id: str,
    turns: int,
) -> Dict[str, Any]:
    """Run one cold-start -> drain -> restart cycle against a fresh persist root."""

    conversation_id = f"{thread_id}::0"
    log = CheckLog()
    started = time.monotonic()
    runtime = _open_runtime(
        persist_root=persist_root,
        thread_id=thread_id,
        owner_id=f"smoke-user-{round_index}",
    )
    restarted: Optional[LangGraphRuntime] = None
    try:
        log.expect_equal("host.runtime_engine_id", runtime.runtime_engine_id, LANGGRAPH_RUNTIME_ENGINE)

        for turn in range(1, turns + 1):
            _drive_turn(
                log,
                runtime,
                thread_id=thread_id,
                conversation_id=conversation_id,
                text=f"smoke round {round_index} turn {turn}",
                tag=f"turn{turn}",
            )

        views = _transaction_views(runtime, thread_id=thread_id)
        log.expect_equal("store.transaction_count", len(views), turns)
        _check_alignment(log, views, prefix="cold")

        # Second step on an existing line: revision and WM must both advance
        # while the checkpoint stays in sync with the Store.
        if views:
            target = views[0]
            advanced = runtime.advance_transaction(
                target["transaction_id"],
                user_text=f"smoke round {round_index} follow-up",
            )
            log.expect("advance.success", advanced.get("success") is True, json.dumps(advanced, default=str))
            log.expect_equal("advance.graph_phase", advanced.get("graph_phase"), EXPECTED_GRAPH_PHASE)
            log.expect_equal("advance.runtime_engine", advanced.get("runtime_engine"), LANGGRAPH_RUNTIME_ENGINE)
            reloaded = runtime.registry.get(target["transaction_id"])
            if reloaded is None:
                raise SmokeError(
                    f"transaction vanished from Store: {target['transaction_id']}"
                )
            after = _transaction_view(runtime, reloaded)
            log.expect(
                "advance.revision_increased",
                after["revision"] > target["revision"],
                f"before={target['revision']} after={after['revision']}",
            )
            log.expect(
                "advance.wm_grew",
                after["wm_entries"] > target["wm_entries"],
                f"before={target['wm_entries']} after={after['wm_entries']}",
            )
            log.expect_equal(
                "advance.checkpoint_revision_matches_store",
                after["checkpoint_revision"],
                after["revision"],
            )

        scene_entries = _check_scene(
            log,
            runtime,
            conversation_id=conversation_id,
            expected_turns=turns,
            prefix="cold",
        )
        _check_persistence_files(log, persist_root)
        _check_health(log, runtime, expected_transactions=turns, prefix="cold")

        before_restart = _transaction_views(runtime, thread_id=thread_id)
        runtime.shutdown()

        restarted = _open_runtime(
            persist_root=persist_root,
            thread_id=thread_id,
            owner_id=f"smoke-user-{round_index}",
        )
        after_restart = _transaction_views(restarted, thread_id=thread_id)
        log.expect_equal("restart.transaction_count", len(after_restart), len(before_restart))
        log.expect_equal("restart.state_identical", after_restart, before_restart)
        _check_alignment(log, after_restart, prefix="restart")
        log.expect_equal(
            "restart.scene_entries",
            len(restarted.scene_system.reader.tail(conversation_id, limit=200)),
            scene_entries,
        )
        _check_health(log, restarted, expected_transactions=turns, prefix="restart")

        _drive_turn(
            log,
            restarted,
            thread_id=thread_id,
            conversation_id=conversation_id,
            text=f"smoke round {round_index} post-restart",
            tag="restart.turn",
        )
        post_restart = _transaction_views(restarted, thread_id=thread_id)
        log.expect_equal("restart.transaction_count_after_turn", len(post_restart), turns + 1)
        _check_alignment(log, post_restart, prefix="post_restart")

        return {
            "round": round_index,
            "ok": not log.failures,
            "persist_root": str(persist_root),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "checks_total": len(log.checks),
            "checks_failed": len(log.failures),
            "failures": [item.to_dict() for item in log.failures],
            "checks": [item.to_dict() for item in log.checks],
            "transactions": post_restart,
        }
    except Exception as exc:
        return {
            "round": round_index,
            "ok": False,
            "persist_root": str(persist_root),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "checks_total": len(log.checks),
            "checks_failed": len(log.failures) + 1,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
            "failures": [item.to_dict() for item in log.failures],
            "checks": [item.to_dict() for item in log.checks],
            "transactions": [],
        }
    finally:
        for host in (restarted, runtime):
            if host is None:
                continue
            try:
                host.shutdown()
            except Exception:
                pass


def run_smoke(
    *,
    rounds: int,
    persist_root: Path,
    thread_id: str,
    turns: int,
    progress: bool = True,
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for index in range(1, rounds + 1):
        round_root = persist_root / f"round-{index:02d}"
        result = run_round(
            round_index=index,
            persist_root=round_root,
            thread_id=thread_id,
            turns=turns,
        )
        results.append(result)
        if progress:
            status = "PASS" if result["ok"] else "FAIL"
            print(
                f"[round {index}/{rounds}] {status} "
                f"checks={result['checks_total']} "
                f"failed={result['checks_failed']} "
                f"{result['duration_ms']}ms",
                file=sys.stderr,
            )
    passed = sum(1 for item in results if item["ok"])
    return {
        "runtime_engine": LANGGRAPH_RUNTIME_ENGINE,
        "rounds": rounds,
        "passed": passed,
        "failed": rounds - passed,
        "turns_per_round": turns,
        "thread_id": thread_id,
        "persist_root": str(persist_root),
        "ok": passed == rounds,
        "results": results,
    }


def _summarize(report: Dict[str, Any], *, verbose: bool) -> Dict[str, Any]:
    if verbose:
        return report
    trimmed = dict(report)
    trimmed["results"] = [
        {key: value for key, value in item.items() if key != "checks"}
        for item in report["results"]
    ]
    return trimmed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rounds",
        type=int,
        default=5,
        help="Number of independent repetitions (R1 exit criterion is 5).",
    )
    parser.add_argument(
        "--turns",
        type=int,
        default=2,
        help="User messages drained through the inbox per round.",
    )
    parser.add_argument(
        "--thread-id",
        default="smoke-langgraph",
        help="Thread id used for the internal conversation.",
    )
    parser.add_argument(
        "--persist-root",
        type=Path,
        help="Persistence root for runtime artifacts. Defaults to a temp dir.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the temp persistence root instead of deleting it.",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Also write the full report (including every check) to this path.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Include every individual check in the stdout report.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-round progress lines on stderr.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.rounds < 1:
        print("--rounds must be >= 1", file=sys.stderr)
        return 2
    if args.turns < 1:
        print("--turns must be >= 1", file=sys.stderr)
        return 2

    temp_dir: Optional[tempfile.TemporaryDirectory[str]] = None
    try:
        if args.persist_root is not None:
            persist_root = args.persist_root.resolve()
            persist_root.mkdir(parents=True, exist_ok=True)
        elif args.keep:
            persist_root = Path(tempfile.mkdtemp(prefix="m-agent-lg-smoke-")).resolve()
        else:
            temp_dir = tempfile.TemporaryDirectory(
                prefix="m-agent-lg-smoke-",
                ignore_cleanup_errors=True,
            )
            persist_root = Path(temp_dir.name).resolve()

        try:
            report = run_smoke(
                rounds=args.rounds,
                persist_root=persist_root,
                thread_id=args.thread_id,
                turns=args.turns,
                progress=not args.quiet,
            )
        except SmokeError as exc:
            print(f"smoke setup error: {exc}", file=sys.stderr)
            return 2

        if args.json_out is not None:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        print(
            json.dumps(
                _summarize(report, verbose=args.verbose),
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0 if report["ok"] else 1
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
