#!/usr/bin/env python3
"""Generate per-question Round tables from recall_trace JSON + autoeval jsonl."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _evidence_ids(snapshot: Dict[str, Any] | None) -> Set[str]:
    if not snapshot or not isinstance(snapshot, dict):
        return set()
    out: Set[str] = set()
    for e in snapshot.get("evidences") or []:
        if isinstance(e, dict):
            eid = str(e.get("evidence_id") or "").strip()
            if eid:
                out.add(eid)
    return out


def _gold_refs(oracle: Dict[str, Any]) -> List[str]:
    refs: List[str] = []
    for g in oracle.get("gold_segments") or []:
        if isinstance(g, dict):
            r = str(g.get("episode_ref") or "").strip()
            if r:
                refs.append(r)
    return refs


def _gold_hit_miss(gold: List[str], id_set: Set[str]) -> tuple[int, int]:
    """How many gold episode_ref appear in id_set (hit) vs not (miss)."""
    if not gold:
        return 0, 0
    hit = sum(1 for g in gold if g in id_set)
    return hit, len(gold) - hit


def _judge_dict(round_rec: Dict[str, Any]) -> Dict[str, Any]:
    j = round_rec.get("judge_result") or round_rec.get("judge") or {}
    if isinstance(j, dict) and "parsed" in j and isinstance(j.get("parsed"), dict):
        return j["parsed"]
    return j if isinstance(j, dict) else {}


def _useful_evidence_id_set(round_rec: Dict[str, Any]) -> Set[str]:
    j = _judge_dict(round_rec)
    u = j.get("useful_evidence_ids") or []
    if not isinstance(u, list):
        return set()
    return {str(x).strip() for x in u if str(x).strip()}


def _useful_len(round_rec: Dict[str, Any]) -> int:
    return len(_useful_evidence_id_set(round_rec))


def _md_escape_cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def _autoeval_style(autoeval_ok: bool | None) -> tuple[str, str, str]:
    """(title_color, accent_border, badge_label) for HTML heading."""
    if autoeval_ok is True:
        return "#1a7f37", "#2da44e", "回答正确"
    if autoeval_ok is False:
        return "#cf222e", "#fa4549", "回答错误"
    return "#656d76", "#8c959f", "未知"


def question_title_html(
    idx: int,
    question_id: str,
    autoeval_ok: bool | None,
    *,
    band_highlight: bool = False,
    judge_label: str = "gpt-4o",
) -> str:
    """Colored HTML heading; band_highlight adds a light green/red/gray bar for mixed reports."""
    title_c, accent, badge = _autoeval_style(autoeval_ok)
    bg = ""
    if band_highlight:
        if autoeval_ok is True:
            bg = "background-color:rgba(26,127,55,0.14);"
        elif autoeval_ok is False:
            bg = "background-color:rgba(207,34,46,0.11);"
        else:
            bg = "background-color:rgba(101,109,118,0.10);"
    border_w = "6px" if band_highlight else "4px"
    pad = "0.5em 0.65em 0.5em 0.7em" if band_highlight else "0.35em 0 0.45em 0.55em"
    radius = "border-radius:6px;" if band_highlight else ""
    return (
        f'<h2 style="margin:1em 0 0.5em 0;padding:{pad};'
        f"border-left:{border_w} solid {accent};{bg}{radius}"
        f'color:{title_c};font-size:1.2em;font-weight:600;line-height:1.35;">'
        f"{idx}. <code>{question_id}</code> "
        f'<span style="font-size:0.82em;font-weight:500;opacity:0.92">· {badge}（{_md_escape_cell(judge_label)}）</span>'
        f"</h2>\n"
    )


def _mixed_legend_lines(judge_label: str = "gpt-4o") -> List[str]:
    """Visible legend for the mixed (all-questions) markdown file."""
    jl = _md_escape_cell(judge_label)
    return [
        "### 题块颜色图例（混合文件）",
        "",
        '<p style="margin:0.35em 0 0.85em 0;padding:0.7em 1em;border-radius:8px;border:1px solid #d0d7de;'
        "background:#f6f8fa;line-height:1.65;\">"
        f"<strong style=\"color:#24292f\">{jl} 自动标注</strong>："
        '<span style="margin-left:0.5em;color:#1a7f37;font-weight:700">■ 绿色条</span> = 本题回答<strong style="color:#1a7f37">正确</strong>　'
        '<span style="color:#cf222e;font-weight:700">■ 红色条</span> = 本题回答<strong style="color:#cf222e">错误</strong>　'
        '<span style="color:#656d76;font-weight:700">■ 灰色条</span> = eval 中无 <code>autoeval_label</code>'
        "</p>",
        "",
    ]


def build_section(
    question_id: str,
    oracle: Dict[str, Any],
    rounds: List[Dict[str, Any]],
    hypothesis: str,
    autoeval_ok: bool | None,
    *,
    judge_label: str = "gpt-4o",
) -> str:
    qtext = str(oracle.get("question") or "").strip()
    ans_raw = str(oracle.get("answer") or "").strip()
    ans_disp = ans_raw if len(ans_raw) <= 500 else ans_raw[:497] + "…"
    gold = _gold_refs(oracle)
    n_g = len(gold)
    hyp_short = hypothesis if len(hypothesis) <= 200 else hypothesis[:197] + "…"

    jl = _md_escape_cell(judge_label)
    title_c, _, _ = _autoeval_style(autoeval_ok)
    if autoeval_ok is True:
        ok_txt = f'<span style="color:{title_c};font-weight:600">是</span>（{jl} autoeval）'
    elif autoeval_ok is False:
        ok_txt = f'<span style="color:{title_c};font-weight:600">否</span>（{jl} autoeval）'
    else:
        ok_txt = f'<span style="color:{title_c};font-weight:600">未知</span>（eval 文件无此项）'

    ans_line = (
        _md_escape_cell(ans_disp)
        if ans_raw
        else "—（`oracle` 中无 `answer` 或为空）"
    )
    lines: List[str] = [
        f"- **问题（oracle.question）**：{_md_escape_cell(qtext)}",
        f"- **标准答案（oracle.answer）**：{ans_line}",
        f"- **本 run hypothesis（截断）**：{_md_escape_cell(hyp_short)}",
        f"- **是否回答正确**：{ok_txt}",
        f"- **金段数**：{n_g}（下列「全部金段均…」指这 {n_g} 条 `episode_ref` 是否**同时**出现在对应阶段证据池）",
        "",
    ]

    if not rounds:
        lines.append("*（无 `workspace_rounds` 记录）*")
        lines.append("")
        return "\n".join(lines)

    lines.extend(
        [
            "| Round | execute 条数 | 全部金段均在 execute？ | rerank 条数 | 全部金段均在 rerank？ | Judge 认可条数（`useful_evidence_ids`） | 金段在 Judge 认可（`useful_evidence_ids`）：hit · miss |",
            "|-------|-------------|------------------------|------------|------------------------|----------------------------------------|-------------------------------------------------------|",
        ]
    )

    for r in rounds:
        rid = r.get("round_id")
        ex = r.get("workspace_after_execute") or {}
        rr = r.get("workspace_after_rerank") or {}
        ex_ids = _evidence_ids(ex)
        rr_ids = _evidence_ids(rr)
        useful_ids = _useful_evidence_id_set(r)
        n_ex = len(ex.get("evidences") or []) if isinstance(ex, dict) else 0
        n_rr = len(rr.get("evidences") or []) if isinstance(rr, dict) else 0
        if gold:
            ok_ex = "是" if all(g in ex_ids for g in gold) else "否"
            ok_rr = "是" if all(g in rr_ids for g in gold) else "否"
            jh, jm = _gold_hit_miss(gold, useful_ids)
            ja_cell = f"hit {jh} · miss {jm}"
        else:
            ok_ex = ok_rr = "—（无金段）"
            ja_cell = "—（无金段）"
        ul = _useful_len(r)
        lines.append(
            f"| {rid} | {n_ex} | {ok_ex} | {n_rr} | {ok_rr} | {ul} | {ja_cell} |"
        )
    lines.append("")
    return "\n".join(lines)


def load_eval_rows(eval_path: Path) -> tuple[List[str], Dict[str, str], Dict[str, bool | None]]:
    order: List[str] = []
    hypo_by_id: Dict[str, str] = {}
    auto_by_id: Dict[str, bool | None] = {}
    for line in eval_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        qid = str(row.get("question_id") or "").strip()
        if not qid:
            continue
        order.append(qid)
        hypo_by_id[qid] = str(row.get("hypothesis") or "")
        lab = row.get("autoeval_label")
        if isinstance(lab, dict) and "label" in lab:
            v = lab.get("label")
            if isinstance(v, bool):
                auto_by_id[qid] = v
            else:
                auto_by_id[qid] = None
        else:
            auto_by_id[qid] = None
    return order, hypo_by_id, auto_by_id


def _infer_judge_label_from_eval(eval_path: Path) -> str:
    try:
        text = eval_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        lab = row.get("autoeval_label")
        if isinstance(lab, dict):
            m = lab.get("model")
            if isinstance(m, str) and m.strip():
                return m.strip()
    return ""


def _preamble_lines(
    test_id: str,
    variant_zh: str,
    n_questions: int,
    *,
    mixed_report: bool = False,
    title_prefix: str = "LongMemEval",
    eval_path_desc: str = "log/<test_id>/longmemeval_hypothesis.jsonl.eval-results-gpt-4o",
    judge_label: str = "gpt-4o",
) -> List[str]:
    jl = _md_escape_cell(judge_label)
    lines: List[str] = [
        f"# {title_prefix} 逐轮表（{variant_zh}）",
        "",
        f"> **test_id**：`{test_id}` · 本文件共 **{n_questions}** 题。",
        "",
        f"> **生成方式**：`scripts/run_longmemeval/gen_round_tables_md.py` 读取 `log/<test_id>/recall_trace/<question_id>.json` 与 eval jsonl（本 run：`{eval_path_desc}`）。",
        "",
        "## 列说明",
        "",
        "- **execute 条数** / **rerank 条数**：`workspace_after_execute` / `workspace_after_rerank` 中 `evidences` 长度。",
        "- **全部金段均在 …？**：该题 `oracle.gold_segments[*].episode_ref` 是否**全部**同时出现在该阶段各条的 `evidence_id` 集合中（金段数为 1～5 不等）。",
        "- **Judge 认可条数**：该轮 `judge` / `judge_result` 中 `useful_evidence_ids` 列表长度（与「judge 后 workspace **池**总条数」不同）。",
        "- **金段在 Judge 认可（`useful_evidence_ids`）：hit · miss**：以该轮 `judge` / `judge_result` 解析出的 **`useful_evidence_ids` 集合**（与「Judge 认可条数」同一来源）为集合，统计本题 `oracle.gold_segments[*].episode_ref` 命中（hit）与未命中（miss）；**不是** `workspace_after_judge` 全池。`miss = 0` 表示全部金段均被 Judge 标为有用。",
        f"- **是否回答正确**：eval 文件中的 **`{jl}` `autoeval_label.label`**。",
        "",
        "- **oracle.answer（每题块首）**：`recall_trace/<question_id>.json` 里 `oracle.answer`，即评测标准参考答案（与 hypothesis 对照）。",
        "",
    ]
    if mixed_report:
        lines.extend(
            [
                f"- **混合文件 · 正/误配色**：每题 **HTML 标题栏** 为绿色（正确）或红色（错误），左侧色条加宽并带**浅色整行底**便于扫读；正文「是否回答正确」中的 **是/否** 同色。无 {jl} label 为灰色。",
                "",
            ]
        )
        lines.extend(_mixed_legend_lines(judge_label))
    else:
        lines.extend(
            [
                f"- **颜色**：每题标题与「是否回答正确」行使用 HTML 颜色（绿=正确，红=错误，灰=未知）；需支持内联样式的 Markdown 预览（如 VS Code / Cursor）；纯文本或无 HTML 环境可能不显示颜色。（裁判显示名：**{jl}**）",
                "",
            ]
        )
    lines.extend(["---", ""])
    return lines


def render_markdown(
    test_id: str,
    variant_zh: str,
    question_ids: List[str],
    trace_dir: Path,
    hypo_by_id: Dict[str, str],
    auto_by_id: Dict[str, bool | None],
    *,
    mixed_report: bool = False,
    title_prefix: str = "LongMemEval",
    eval_path_desc: str = "log/<test_id>/longmemeval_hypothesis.jsonl.eval-results-gpt-4o",
    judge_label: str = "gpt-4o",
) -> str:
    out_lines: List[str] = _preamble_lines(
        test_id,
        variant_zh,
        len(question_ids),
        mixed_report=mixed_report,
        title_prefix=title_prefix,
        eval_path_desc=eval_path_desc,
        judge_label=judge_label,
    )

    if not question_ids:
        out_lines.append("*（本题集下无题目：请检查 eval 行顺序或 gpt-4o 标签筛选是否过严。）*")
        out_lines.append("")
        return "\n".join(out_lines) + "\n"

    for idx, qid in enumerate(question_ids, 1):
        tpath = trace_dir / f"{qid}.json"
        if not tpath.is_file():
            out_lines.append(
                question_title_html(
                    idx,
                    qid,
                    auto_by_id.get(qid),
                    band_highlight=mixed_report,
                    judge_label=judge_label,
                )
            )
            out_lines.append(f"*（缺少 `recall_trace/{qid}.json`）*\n\n---\n\n")
            continue
        data = json.loads(tpath.read_text(encoding="utf-8"))
        oracle = data.get("oracle") or {}
        mr = data.get("model_recall") or {}
        result = mr.get("result") or {}
        rounds = result.get("workspace_rounds") or []
        if not isinstance(rounds, list):
            rounds = []
        out_lines.append(
            question_title_html(
                idx,
                qid,
                auto_by_id.get(qid),
                band_highlight=mixed_report,
                judge_label=judge_label,
            )
        )
        out_lines.append(
            build_section(
                qid,
                oracle if isinstance(oracle, dict) else {},
                [x for x in rounds if isinstance(x, dict)],
                hypo_by_id.get(qid, ""),
                auto_by_id.get(qid),
                judge_label=judge_label,
            )
        )
        out_lines.append("---\n\n")

    return "\n".join(out_lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Write three round-table markdown files under log/<test_id>/ (LongMemEval or LOCOMO)."
    )
    ap.add_argument(
        "test_id",
        help="Log subdirectory name under repo log/, e.g. longmemeval_run_try_each_4_without_time_recall",
    )
    ap.add_argument(
        "--eval-file",
        default="",
        help=(
            "Path to eval jsonl (question_id, hypothesis, autoeval_label). "
            "Default: log/<test_id>/longmemeval_hypothesis.jsonl.eval-results-gpt-4o"
        ),
    )
    ap.add_argument(
        "--trace-dir",
        default="",
        help="recall_trace directory. Default: log/<test_id>/recall_trace. Relative paths are under log/<test_id>/.",
    )
    ap.add_argument(
        "--title-prefix",
        default="LongMemEval",
        help="Report title prefix (e.g. LOCOMO). Default: LongMemEval",
    )
    ap.add_argument(
        "--judge-label",
        default="",
        help="Display name for judge in prose (default: infer from eval jsonl autoeval_label.model, else gpt-4o).",
    )
    args = ap.parse_args()
    test_id = str(args.test_id).strip()
    if not test_id:
        print("test_id must be non-empty", file=sys.stderr)
        sys.exit(2)

    repo = _repo_root()
    log_dir = repo / "log" / test_id
    eval_arg = str(args.eval_file or "").strip()
    if eval_arg:
        eval_path = Path(eval_arg)
        if not eval_path.is_absolute():
            eval_path = (repo / eval_path).resolve()
    else:
        eval_path = (log_dir / "longmemeval_hypothesis.jsonl.eval-results-gpt-4o").resolve()

    trace_arg = str(args.trace_dir or "").strip()
    if trace_arg:
        trace_dir = Path(trace_arg)
        if not trace_dir.is_absolute():
            trace_dir = (log_dir / trace_dir).resolve()
    else:
        trace_dir = (log_dir / "recall_trace").resolve()

    if not log_dir.is_dir():
        print(f"Log directory not found: {log_dir}", file=sys.stderr)
        sys.exit(1)
    if not eval_path.is_file():
        print(f"Eval jsonl not found: {eval_path}", file=sys.stderr)
        sys.exit(1)
    if not trace_dir.is_dir():
        print(f"recall_trace dir not found: {trace_dir}", file=sys.stderr)
        sys.exit(1)

    order, hypo_by_id, auto_by_id = load_eval_rows(eval_path)
    if not order:
        print(f"No questions in eval file: {eval_path}", file=sys.stderr)
        sys.exit(1)

    title_prefix = str(args.title_prefix or "").strip() or "LongMemEval"
    judge_label = str(args.judge_label or "").strip()
    if not judge_label:
        judge_label = _infer_judge_label_from_eval(eval_path) or "gpt-4o"

    try:
        eval_path_desc = str(eval_path.resolve().relative_to(repo))
    except ValueError:
        eval_path_desc = str(eval_path)

    correct_ids = [q for q in order if auto_by_id.get(q) is True]
    wrong_ids = [q for q in order if auto_by_id.get(q) is False]

    jl_short = _md_escape_cell(judge_label)
    jobs: List[tuple[str, str, List[str], bool]] = [
        ("phase_60_questions_round_tables.md", "混合 · eval 行顺序", order, True),
        ("phase_60_questions_round_tables_correct.md", f"仅 {jl_short} 正确", correct_ids, False),
        ("phase_60_questions_round_tables_wrong.md", f"仅 {jl_short} 错误", wrong_ids, False),
    ]

    for filename, variant_zh, qids, mixed_report in jobs:
        path = log_dir / filename
        text = render_markdown(
            test_id,
            variant_zh,
            qids,
            trace_dir,
            hypo_by_id,
            auto_by_id,
            mixed_report=mixed_report,
            title_prefix=title_prefix,
            eval_path_desc=eval_path_desc,
            judge_label=judge_label,
        )
        path.write_text(text, encoding="utf-8")
        print("Wrote", path, "questions", len(qids))

    print(f"Done test_id={test_id}")


if __name__ == "__main__":
    main()
