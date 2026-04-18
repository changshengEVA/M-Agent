#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run LongMemEval upstream ``src/evaluation/evaluate_qa.py``.

Configuration (in order of precedence: CLI > ``official_eval.yaml`` > environment variables):

- ``config/eval/memory_agent/longmemeval/official_eval.yaml`` (copy from ``official_eval.example.yaml``)
- ``LONGMEMEVAL_ROOT``, ``LONGMEMEVAL_PYTHON``
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

THIS = Path(__file__).resolve()
PROJECT_ROOT = THIS.parents[2]
RUN_LOCOMO = PROJECT_ROOT / "scripts" / "run_locomo"
if str(RUN_LOCOMO) not in sys.path:
    sys.path.insert(0, str(RUN_LOCOMO))

from _bootstrap import bootstrap_project

bootstrap_project()

from _shared import DEFAULT_LONGMEMEVAL_ENV_CONFIG_PATH, load_env_config
from m_agent.paths import LOG_DIR, resolve_project_path

logger = logging.getLogger("run_official_evaluate_qa")

DEFAULT_OFFICIAL_EVAL_REL = "config/eval/memory_agent/longmemeval/official_eval.yaml"
EXAMPLE_OFFICIAL_EVAL_REL = "config/eval/memory_agent/longmemeval/official_eval.example.yaml"


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    import yaml

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _apply_api_to_env(env: Dict[str, str], api: Dict[str, Any]) -> None:
    if not api:
        return
    key_map = {
        "openai_api_key": "OPENAI_API_KEY",
        "openai_base_url": "OPENAI_BASE_URL",
        "openai_organization": "OPENAI_ORGANIZATION",
    }
    for yaml_key, env_key in key_map.items():
        val = api.get(yaml_key)
        if val is None:
            continue
        s = str(val).strip()
        if s:
            env[env_key] = s


def _hypothesis_path_from_env_config(env_config_path: str) -> tuple[Path, Path]:
    payload, path = load_env_config(env_config_path)
    eval_cfg = payload.get("eval", {})
    if not isinstance(eval_cfg, dict):
        eval_cfg = {}
    test_id = str(eval_cfg.get("test_id", "longmemeval_run") or "").strip() or "longmemeval_run"
    hypothesis_name = str(
        eval_cfg.get("hypothesis_jsonl", "longmemeval_hypothesis.jsonl") or ""
    ).strip() or "longmemeval_hypothesis.jsonl"
    hypothesis_path = (LOG_DIR / test_id / hypothesis_name).resolve()
    return hypothesis_path, path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run LongMemEval official evaluate_qa.py (see official_eval.example.yaml).",
    )
    p.add_argument(
        "--official-eval-config",
        default=DEFAULT_OFFICIAL_EVAL_REL,
        help="YAML with longmemeval_root, python, api.* (default: %s)" % DEFAULT_OFFICIAL_EVAL_REL,
    )
    p.add_argument(
        "--longmemeval-root",
        default=None,
        help="LongMemEval repo root (overrides file / LONGMEMEVAL_ROOT).",
    )
    p.add_argument(
        "--env-config",
        default=None,
        help="M-Agent eval YAML for hypothesis path (default: file m_agent_env_config or test_env.yaml).",
    )
    p.add_argument(
        "--hypothesis-jsonl",
        default=None,
        help="Override hypothesis jsonl path (relative to project root ok).",
    )
    p.add_argument(
        "--oracle-json",
        default=None,
        help="Override oracle json path.",
    )
    p.add_argument(
        "--judge-model",
        default=None,
        help="Judge model name passed to evaluate_qa.py (default: file or gpt-4o).",
    )
    p.add_argument(
        "--python",
        default=None,
        help="Python exe with requirements-lite (default: file or LONGMEMEVAL_PYTHON or current).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved paths and command only.",
    )
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()

    cfg_path = resolve_project_path(args.official_eval_config)
    file_cfg = _load_yaml(cfg_path)
    example_path = resolve_project_path(EXAMPLE_OFFICIAL_EVAL_REL)

    root = (args.longmemeval_root or file_cfg.get("longmemeval_root") or os.environ.get("LONGMEMEVAL_ROOT") or "")
    root = str(root).strip()
    if not root:
        logger.error(
            "未配置 LongMemEval 仓库路径。\n"
            "  1) 复制 %s\n"
            "     为 config/eval/memory_agent/longmemeval/official_eval.yaml\n"
            "  2) 填写 longmemeval_root、python、api.openai_api_key（等）\n"
            "或设置环境变量 LONGMEMEVAL_ROOT，或使用参数 --longmemeval-root",
            example_path,
        )
        return 2

    py_raw = args.python or file_cfg.get("python") or os.environ.get("LONGMEMEVAL_PYTHON")
    py_exe = str(py_raw).strip() if py_raw else ""
    if not py_exe:
        py_exe = sys.executable

    judge = (args.judge_model or file_cfg.get("judge_model") or "gpt-4o")
    judge = str(judge).strip() or "gpt-4o"

    env_config_arg = args.env_config
    if env_config_arg is None or (str(env_config_arg).strip() == ""):
        m = file_cfg.get("m_agent_env_config")
        env_config_for_hyp = str(m).strip() if m else DEFAULT_LONGMEMEVAL_ENV_CONFIG_PATH
    else:
        env_config_for_hyp = str(env_config_arg).strip()

    hypothesis_override = args.hypothesis_jsonl
    if hypothesis_override is None or str(hypothesis_override).strip() == "":
        ho = file_cfg.get("hypothesis_jsonl")
        hypothesis_override = str(ho).strip() if ho else ""

    oracle_override = args.oracle_json
    if oracle_override is None or str(oracle_override).strip() == "":
        oo = file_cfg.get("oracle_json")
        oracle_override = str(oo).strip() if oo else ""

    longmem_root = Path(root).expanduser().resolve()
    eval_dir = longmem_root / "src" / "evaluation"
    evaluate_qa = eval_dir / "evaluate_qa.py"
    if not evaluate_qa.is_file():
        logger.error("找不到 evaluate_qa.py：%s\n请检查 longmemeval_root。", evaluate_qa)
        return 2

    if hypothesis_override:
        hypothesis_path = resolve_project_path(hypothesis_override)
    else:
        hypothesis_path, cfg_used = _hypothesis_path_from_env_config(env_config_for_hyp)
        logger.info("Hypothesis 由 %s 解析 -> %s", cfg_used, hypothesis_path)

    if oracle_override:
        oracle_path = resolve_project_path(oracle_override)
    else:
        oracle_path = resolve_project_path("data/LongMemEval/data/longmemeval_oracle.json")

    if not hypothesis_path.is_file():
        logger.error("找不到 hypothesis jsonl：%s\n请先运行 eval_longmemeval.py 生成，或检查 test_env.yaml 里 eval.test_id。", hypothesis_path)
        return 2
    if not oracle_path.is_file():
        logger.error("找不到 oracle：%s\n请从上游下载 longmemeval_oracle.json 到 data/LongMemEval/data/。", oracle_path)
        return 2

    cmd = [
        str(Path(py_exe).expanduser()),
        str(evaluate_qa),
        judge,
        str(hypothesis_path.resolve()),
        str(oracle_path.resolve()),
    ]

    logger.info("配置文件：%s %s", cfg_path, "(已加载)" if file_cfg else "(不存在，仅使用参数与环境变量)")
    logger.info("工作目录：%s", eval_dir)
    logger.info("命令：%s", " ".join(cmd))

    if args.dry_run:
        return 0

    env = dict(os.environ)
    api = file_cfg.get("api") if isinstance(file_cfg.get("api"), dict) else {}
    _apply_api_to_env(env, api)

    completed = subprocess.run(cmd, cwd=str(eval_dir), env=env)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
