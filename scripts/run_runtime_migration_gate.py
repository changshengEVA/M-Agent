"""Repeatable observation gate for the LangGraph product runtime.

This script intentionally keeps every command independently runnable so the
JSON summary identifies the exact failed slice.  It does not mutate durable
user state; all acceptance/smoke harnesses use their own temporary stores.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Mapping, Sequence


def _run(
    *,
    name: str,
    command: Sequence[str],
    environment: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    started = time.monotonic()
    env = os.environ.copy()
    env.update(dict(environment or {}))
    completed = subprocess.run(
        list(command),
        env=env,
        check=False,
    )
    return {
        "name": name,
        "command": list(command),
        "exit_code": int(completed.returncode),
        "success": completed.returncode == 0,
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the LangGraph-only runtime observation gate."
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=3,
        help="LangGraph turn-loop smoke repetitions (default: 3).",
    )
    parser.add_argument(
        "--include-full-suite",
        action="store_true",
        help="Also run the complete pytest suite after migration gates.",
    )
    parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Run every slice even after an earlier failure.",
    )
    parser.add_argument(
        "--audit-root",
        type=Path,
        help="Explicit runtime persistence root for the strict read-only audit.",
    )
    parser.add_argument(
        "--schedules-root",
        type=Path,
        help="Explicit external schedules root paired with --audit-root.",
    )
    parser.add_argument(
        "--archive-manifest",
        type=Path,
        help="Verified retirement archive manifest paired with --audit-root.",
    )
    parser.add_argument(
        "--audit-observations",
        type=int,
        default=3,
        help="Independent audit subprocess observations when data audit is enabled (default: 3).",
    )
    args = parser.parse_args()
    audit_values = (
        args.audit_root,
        args.schedules_root,
        args.archive_manifest,
    )
    if any(value is not None for value in audit_values) and not all(
        value is not None for value in audit_values
    ):
        parser.error(
            "--audit-root, --schedules-root, and --archive-manifest "
            "must be supplied together"
        )
    if args.audit_observations < 1:
        parser.error("--audit-observations must be at least 1")
    return args


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[1]
    python = sys.executable
    pytest_temp_root = (
        project_root / ".tmp" / f"runtime-migration-gate-{os.getpid()}"
    )
    pytest_temp_root.mkdir(parents=True, exist_ok=False)

    def pytest_command(name: str, *targets: str) -> List[str]:
        return [
            python,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(pytest_temp_root / name),
            "-q",
            *targets,
        ]

    commands: List[tuple[str, List[str], Dict[str, str]]] = []
    if args.audit_root is not None:
        for observation in range(1, args.audit_observations + 1):
            commands.append(
                (
                    f"runtime_retirement_data_audit_{observation}",
                    [
                        python,
                        "scripts/audit_runtime_cutover.py",
                        "--root",
                        str(args.audit_root.resolve()),
                        "--schedules-root",
                        str(args.schedules_root.resolve()),
                        "--archive-manifest",
                        str(args.archive_manifest.resolve()),
                        "--require-legacy-retired",
                    ],
                    {},
                )
            )
    commands.extend([
        (
            "runtime_unit_tests",
            pytest_command("runtime", "tests/runtime"),
            {},
        ),
        (
            "acceptance_langgraph",
            pytest_command("acceptance", "tests/acceptance/scenarios"),
            {"M_AGENT_ACCEPTANCE_RUNTIME_ID": "langgraph_v1"},
        ),
        (
            "langgraph_turn_loop_smoke",
            [
                python,
                "scripts/smoke_langgraph_turn_loop.py",
                "--rounds",
                str(max(1, int(args.rounds))),
            ],
            {},
        ),
    ])
    if args.include_full_suite:
        commands.append(
            (
                "full_pytest_suite",
                pytest_command("full-suite"),
                {},
            )
        )

    results: List[Dict[str, Any]] = []
    old_cwd = Path.cwd()
    try:
        os.chdir(project_root)
        for name, command, environment in commands:
            result = _run(
                name=name,
                command=command,
                environment=environment,
            )
            results.append(result)
            if not result["success"] and not args.continue_on_failure:
                break
    finally:
        os.chdir(old_cwd)

    report = {
        "schema_version": 1,
        "success": len(results) == len(commands)
        and all(item["success"] for item in results),
        "completed_slices": len(results),
        "planned_slices": len(commands),
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
