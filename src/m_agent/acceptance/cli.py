"""Command-line client for the M-Agent semantic acceptance platform."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .artifacts import resolve_project_root
from .catalog import PHASE0_SUITE, catalog_payload
from .runner import RunBusyError
from .scenario_catalog import contract_catalog_payload
from .scenario_runner import ScenarioAcceptanceRunner


def _ui_port(value: str) -> int:
    """Parse a valid TCP port for the local acceptance UI."""

    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _ui_url(host: str, port: int) -> str:
    """Return a browser-friendly loopback URL, including IPv6 brackets."""

    url_host = f"[{host}]" if ":" in host else host
    return f"http://{url_host}:{port}/"


def _json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_catalog() -> None:
    payload = catalog_payload()
    coverage = payload["coverage"]
    print(
        f"{payload['title']}  "
        f"coverage={coverage['specified']}/{coverage['required']}  "
        f"catalog={'valid' if coverage['complete'] else 'invalid'}"
    )
    print()
    for item in payload["invariants"]:
        gap = "  [KNOWN GAP]" if item.get("known_gap") else ""
        print(f"{item['id']}  {item['title']}  · {item['layer']}{gap}")
        print(f"       {item['acceptance']}")
        print(
            f"       gate={len(item['gate_tests'])} "
            f"supporting={len(item['supporting_tests'])}"
        )


def _print_result(payload: Dict[str, Any]) -> None:
    if payload.get("result_kind") == "scenario_contract":
        _print_contract_result(payload)
        return
    print(
        f"run={payload['run_id']}  status={payload['status']}  "
        f"profile={payload['profile']}  duration={payload['duration_seconds']:.2f}s"
    )
    if payload.get("error"):
        print(f"error: {payload['error']}")
    print()
    symbols = {
        "passed": "PASS",
        "known_gap": "GAP ",
        "failed": "FAIL",
        "skipped": "SKIP",
        "timeout": "TIME",
        "cancelled": "CANC",
    }
    for invariant in payload.get("invariants", []):
        status = str(invariant.get("status", "failed"))
        print(f"{symbols.get(status, status.upper()[:4]):4}  {invariant['id']}  {invariant['title']}")
        for case in invariant.get("cases", []):
            print(f"      {case['outcome']:9} {case['nodeid']}")
            if case.get("message") and case["outcome"] in {"failed", "error", "xpassed"}:
                first = str(case["message"]).strip().splitlines()[0]
                print(f"                  {first}")


def _print_contract_catalog() -> None:
    payload = contract_catalog_payload()
    coverage = payload["coverage"]
    execution = payload["execution"]
    print(
        f"{payload['title']}  "
        f"scenarios={coverage['specified_scenarios']}/"
        f"{coverage['required_scenarios']}  "
        f"catalog={'valid' if coverage['catalog_complete'] else 'invalid'}"
    )
    print(
        "executable: "
        + ", ".join(
            f"{runtime}={count}"
            for runtime, count in execution[
                "executable_variants_by_runtime"
            ].items()
        )
    )
    print()
    for scenario in payload["scenarios"]:
        bindings = []
        for variant in scenario["variants"]:
            if variant["layer"] not in {"core", "matcher_evaluation"}:
                continue
            bindings.extend(
                f"{item['runtime_id']}:{item['availability']}"
                for item in variant["bindings"]
            )
        print(
            f"{scenario['id']}  [{scenario['domain']}] "
            f"{scenario['title']}  · {' · '.join(bindings)}"
        )


def _print_contract_result(payload: Dict[str, Any]) -> None:
    print(
        f"run={payload['run_id']}  status={payload['status']}  "
        f"runtime={payload.get('runtime_id') or '—'}  "
        f"layers={payload['profile']}  "
        f"duration={payload['duration_seconds']:.2f}s"
    )
    if payload.get("error"):
        print(f"error: {payload['error']}")
    print()
    symbols = {
        "passed": "PASS",
        "known_gap": "GAP ",
        "failed": "FAIL",
        "not_implemented": "NIMP",
        "not_covered": "NCOV",
        "future": "FUTR",
        "timeout": "TIME",
        "cancelled": "CANC",
    }
    for scenario in payload.get("scenarios", []):
        status = str(scenario.get("status", "not_covered"))
        print(
            f"{symbols.get(status, status.upper()[:4]):4}  "
            f"{scenario['id']}  {scenario['title']}"
        )
        for variant in scenario.get("variants", []):
            variant_status = str(variant.get("status", "not_covered"))
            print(
                f"      {symbols.get(variant_status, variant_status.upper()[:4]):4} "
                f"{variant['id']}  [{variant['layer']}]"
            )
            for case in variant.get("cases", []):
                print(f"           {case['outcome']:9} {case['nodeid']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="m-agent-acceptance",
        description="Run the curated M-Agent runtime semantic acceptance suite.",
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="M-Agent repository root. Defaults to automatic discovery.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="Show all 14 runtime invariants.")
    list_parser.add_argument("--format", choices=("text", "json"), default="text")

    coverage = sub.add_parser("coverage", help="Validate catalog completeness.")
    coverage.add_argument("--format", choices=("text", "json"), default="text")

    run = sub.add_parser("run", help="Run allowlisted semantic tests.")
    run.add_argument("suite", nargs="?", default=PHASE0_SUITE, choices=(PHASE0_SUITE,))
    run.add_argument(
        "--invariant",
        "--case",
        action="append",
        dest="invariants",
        help="Run one invariant ID; repeat to select several. Default: all.",
    )
    run.add_argument("--profile", choices=("gate", "full"), default="gate")
    run.add_argument("--timeout", type=float, default=300.0)
    run.add_argument("--format", choices=("text", "json"), default="text")

    show = sub.add_parser("show", help="Show a persisted run report.")
    show.add_argument("run_id")
    show.add_argument("--format", choices=("text", "json"), default="text")

    ui = sub.add_parser("ui", help="Start the local acceptance dashboard.")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=_ui_port, default=8788)

    contract = sub.add_parser(
        "contract",
        help="Inspect or run the P1 TX/SP/AT cross-Runtime contract.",
    )
    contract_sub = contract.add_subparsers(
        dest="contract_command",
        required=True,
    )
    contract_list = contract_sub.add_parser(
        "list",
        help="List P1 scenarios, variants, and Runtime availability.",
    )
    contract_list.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    contract_coverage = contract_sub.add_parser(
        "coverage",
        help="Show structural and executable P1 contract coverage.",
    )
    contract_coverage.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    contract_run = contract_sub.add_parser(
        "run",
        help="Run allowlisted P1 scenario variants.",
    )
    contract_run.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        help="Run one scenario ID; repeat to select several. Default: all.",
    )
    contract_run.add_argument(
        "--runtime",
        choices=("think_life_v1", "langgraph_v1"),
        default="think_life_v1",
    )
    contract_run.add_argument(
        "--layer",
        action="append",
        choices=(
            "core",
            "robustness",
            "matcher",
            "matcher_evaluation",
        ),
        dest="layers",
        help="Select a test layer; repeat for several. Default: core.",
    )
    contract_run.add_argument(
        "--all-layers",
        action="store_true",
        help="Run Core, Robustness, and Matcher Evaluation together.",
    )
    contract_run.add_argument("--timeout", type=float, default=300.0)
    contract_run.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = resolve_project_root(
        Path(args.project_root) if args.project_root else None
    )

    if args.command == "list":
        if args.format == "json":
            _json(catalog_payload())
        else:
            _print_catalog()
        return 0
    if args.command == "coverage":
        coverage = catalog_payload()["coverage"]
        if args.format == "json":
            _json(coverage)
        else:
            print(
                f"specified={coverage['specified']}/{coverage['required']} "
                f"complete={str(coverage['complete']).lower()}"
            )
            for error in coverage["errors"]:
                print(f"- {error}")
        return 0 if coverage["complete"] else 1
    if args.command == "contract":
        contract_payload = contract_catalog_payload()
        if args.contract_command == "list":
            if args.format == "json":
                _json(contract_payload)
            else:
                _print_contract_catalog()
            return 0
        if args.contract_command == "coverage":
            coverage = contract_payload["coverage"]
            if args.format == "json":
                _json(coverage)
            else:
                print(
                    f"scenarios={coverage['specified_scenarios']}/"
                    f"{coverage['required_scenarios']} "
                    f"core={coverage['specified_core']}/"
                    f"{coverage['required_core']} "
                    f"robustness={coverage['specified_robustness']}/"
                    f"{coverage['required_robustness']} "
                    f"matcher={coverage['specified_matcher']}/"
                    f"{coverage['required_matcher']} "
                    f"catalog_complete="
                    f"{str(coverage['catalog_complete']).lower()} "
                    f"p1_exit_ready="
                    f"{str(coverage['p1_exit_ready']).lower()}"
                )
                for error in coverage["errors"]:
                    print(f"- {error}")
            return 0 if coverage["catalog_complete"] else 1
        if args.contract_command == "run":
            runner = ScenarioAcceptanceRunner(project_root=project_root)
            try:
                result = runner.run_contract(
                    scenario_ids=args.scenarios,
                    runtime_id=args.runtime,
                    layers=(
                        (
                            "core",
                            "robustness",
                            "matcher_evaluation",
                        )
                        if args.all_layers
                        else args.layers or ("core",)
                    ),
                    timeout_seconds=args.timeout,
                )
            except (ValueError, RunBusyError) as exc:
                print(str(exc), file=sys.stderr)
                return 2
            payload = result.to_dict()
            if args.format == "json":
                _json(payload)
            else:
                _print_result(payload)
            return 0 if result.status == "passed" else 1
        return 2

    runner = ScenarioAcceptanceRunner(project_root=project_root)
    if args.command == "show":
        result = runner.store.load_result(args.run_id)
        if result is None:
            print(f"run not found: {args.run_id}", file=sys.stderr)
            return 2
        payload = result.to_dict()
        if args.format == "json":
            _json(payload)
        else:
            _print_result(payload)
        return 0 if result.status == "passed" else 1
    if args.command == "ui":
        if args.host not in {"127.0.0.1", "localhost", "::1"}:
            print(
                "The acceptance UI is local-only; bind to 127.0.0.1, localhost, or ::1.",
                file=sys.stderr,
            )
            return 2
        try:
            import uvicorn

            from .web import create_app
        except ImportError as exc:
            print(f"UI dependencies are unavailable: {exc}", file=sys.stderr)
            return 2
        dashboard_url = _ui_url(args.host, args.port)
        print(
            "M-Agent 语义验收 UI 正在启动 / Acceptance UI is starting:\n"
            f"  {dashboard_url}\n"
            "按 Ctrl+C 停止服务 / Press Ctrl+C to stop.",
            flush=True,
        )
        uvicorn.run(
            create_app(runner=runner),
            host=args.host,
            port=args.port,
            log_level="warning",
        )
        return 0
    if args.command == "run":
        try:
            result = runner.run(
                invariant_ids=args.invariants,
                profile=args.profile,
                timeout_seconds=args.timeout,
            )
        except (ValueError, RunBusyError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        payload = result.to_dict()
        if args.format == "json":
            _json(payload)
        else:
            _print_result(payload)
        return 0 if result.status == "passed" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
