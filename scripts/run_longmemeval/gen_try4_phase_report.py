#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build LongMemEval phase research markdown from local eval + recall_trace artifacts."""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_LOG = ROOT / "log" / "longmemeval_run_try_each_4"
_DEFAULT_DATA = ROOT / "data" / "LongMemEval" / "data" / "longmemeval_s_cleaned.json"


def _path_under_repo(p: Path) -> Path:
    try:
        return p.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return p.resolve()


@dataclass
class ReportConfig:
    """Paths and toggles for `run_report`."""

    log_dir: Path
    out_path: Path
    report_title: str
    dataset_blurb: str
    section1_one_liner: str
    section4_preface: str
    tables_only_wrong: bool
    skip_compare_section: bool
    skip_design_section: bool
    footer_lines: List[str]
    data_json: Path = _DEFAULT_DATA


def default_try4_config() -> ReportConfig:
    log_dir = _DEFAULT_LOG
    return ReportConfig(
        log_dir=log_dir,
        out_path=log_dir / "phase_next_research_report.md",
        report_title="LongMemEval 研究报告（`try_each_4`）",
        dataset_blurb=(
            "> 本轮数据在 `log/longmemeval_run_try_each_4/`。上游检索路径：**facts + Episode 片段 → 多轮召回 → rerank → judge → hypothesis**；"
            "trace 中 Planner 动作为 `EVENT_DETAIL_RECALL`、`EVENT_TIME_RECALL`、`RECALL_REMEDY_MULTI_ROUTE` 等。\n\n"
            "---\n\n"
        ),
        section1_one_liner=(
            "本批日志要回答：在 **60 题分层抽样**（`test_env_try_4`）上，**gpt-4o autoeval** 的失败题集中在哪些 **question_type** 与 **失败形态**，"
            "以及它们与 **summary 金段 hit/miss**、**workspace 各轮 judge 状态** 是否一致。\n\n"
            "---\n\n"
        ),
        section4_preface=(
            "\n下面 **§4** 细写 **autoeval false** 各题：先 **逐轮表**，再 **「错误定位」**（程序根据金段在池变化、rerank 头部、末轮 judge 与拒答/null 等**自动归纳**哪一环节为主因），最后 **小结（语义层）** 人工补全 oracle 口径。\n\n"
            "**§5** 为错题归类；**§6** 简要对拍 `try_each_2_plus`（重叠题）。\n\n---\n\n"
        ),
        tables_only_wrong=False,
        skip_compare_section=False,
        skip_design_section=False,
        footer_lines=[
            "- 本报告：`log/longmemeval_run_try_each_4/phase_next_research_report.md`\n",
            "- 依据：`log/longmemeval_run_try_each_4/recall_trace/*.json`、`recall_trace/summary.jsonl`、`longmemeval_hypothesis.jsonl`、`.eval-results-gpt-4o`\n",
            "- 环境：`config/eval/memory_agent/longmemeval/test_env_try_4.yaml`（`eval.test_id: longmemeval_run_try_each_4`）\n",
        ],
        data_json=_DEFAULT_DATA,
    )


def md_escape_cell(s: str, max_len: int = 0) -> str:
    t = (s or "").replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    t = re.sub(r"\s+", " ", t).strip()
    if max_len and len(t) > max_len:
        t = t[: max_len - 1] + "…"
    return t or "—"


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def load_jsonl_map(path: Path, key: str) -> Dict[str, Dict[str, Any]]:
    return {str(r[key]): r for r in load_jsonl(path) if key in r}


def question_type_index(data_path: Path) -> Dict[str, str]:
    if not data_path.is_file():
        return {}
    data = json.loads(data_path.read_text(encoding="utf-8"))
    out: Dict[str, str] = {}
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            qid = str(item.get("question_id") or "").strip()
            qt = str(item.get("question_type") or "").strip()
            if qid:
                out[qid] = qt
    return out


def gold_refs(trace: Dict[str, Any]) -> Set[str]:
    oracle = trace.get("oracle") or {}
    segs = oracle.get("gold_segments") or []
    refs: Set[str] = set()
    for s in segs:
        if isinstance(s, dict):
            r = str(s.get("episode_ref") or "").strip()
            if r:
                refs.add(r)
    return refs


def evidence_id_set(snap: Optional[Dict[str, Any]]) -> Set[str]:
    if not isinstance(snap, dict):
        return set()
    out: Set[str] = set()
    for ev in snap.get("evidences") or []:
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get("evidence_id") or ev.get("episode_ref") or "").strip()
        if eid:
            out.add(eid)
    return out


def snap_meta(snap: Optional[Dict[str, Any]]) -> Tuple[int, Optional[str], Optional[str]]:
    if not isinstance(snap, dict):
        return 0, None, None
    n = len(snap.get("evidences") or [])
    return n, snap.get("status"), snap.get("gap_type")


def top_rerank_hits(snap: Optional[Dict[str, Any]], limit: int = 5) -> List[Tuple[str, float]]:
    if not isinstance(snap, dict):
        return []
    scored: List[Tuple[str, float]] = []
    for ev in snap.get("evidences") or []:
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get("evidence_id") or "").strip()
        rs = ev.get("rerank_score")
        if eid and rs is not None:
            try:
                scored.append((eid, float(rs)))
            except (TypeError, ValueError):
                pass
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:limit]


def format_planner_actions(actions: Any) -> str:
    if not isinstance(actions, list) or not actions:
        return "—"
    parts: List[str] = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        at = str(a.get("action_type") or "")
        q = a.get("query") if isinstance(a.get("query"), dict) else {}
        if at == "EVENT_DETAIL_RECALL":
            dq = str(q.get("detail_query") or "")
            parts.append(f"① EVENT_DETAIL_RECALL `{md_escape_cell(dq, 90)}` topk={q.get('topk', '')}")
        elif at == "EVENT_TIME_RECALL":
            parts.append(
                f"② EVENT_TIME_RECALL `{q.get('start_time','')}`～`{q.get('end_time','')}`"
            )
        elif at == "RECALL_REMEDY_MULTI_ROUTE":
            dq = str(q.get("detail_query") or "")
            parts.append(f"RECALL_REMEDY_MULTI_ROUTE `{md_escape_cell(dq, 90)}` topk={q.get('topk', '')}")
        else:
            parts.append(f"{at} {md_escape_cell(json.dumps(q, ensure_ascii=False), 80)}")
    return "；".join(parts)


def classify_failure(
    qid: str,
    oracle_answer: str,
    oracle_question: str,
    hypothesis: str,
    hit: int,
    miss: int,
) -> str:
    oa = (oracle_answer or "").strip()
    hyp = (hypothesis or "").strip().lower()
    if "not enough memory evidence" in hyp or "do not have enough" in hyp:
        return "错误弃权/拒答"
    if hyp in ("null", "none", ""):
        return "空答/null"
    if oa.startswith("The user would prefer"):
        return "Preference模板失配"
    if miss > 0 and hit == 0:
        return "检索未覆盖金段"
    if miss > 0:
        return "检索部分漏金段"
    if re.match(r"^-?[\d$€£,.]+$", hypothesis.strip()[:20]) or hypothesis.strip().isdigit():
        if re.search(r"\d", oa):
            return "数值/计数错误"
    if "average" in (oracle_question or "").lower() or "average age" in (oracle_question or "").lower():
        return "多段聚合/计算题"
    if miss == 0:
        return "金段在池仍错（综合/生成）"
    return "其他"


# 语义层小结（与下方程序化「错误定位」互补）
QUESTION_SUMMARIES: Dict[str, str] = {
    "0100672e": "问句要的是「每只杯单价」，oracle 为 **$12**；模型抓住「$60 买杯送同事」但**未在答句里用数量除尽总价**。trace 上 Round1–2 执行池均含两条金段，Round2 **judge 后池从 57 砍到 4 条且丢一条金段**（`stagnant`），末态 summary 亦为 **1 hit/1 miss**，与「缺一条带购买数量的事实」一致。",
    "0f05491a": "oracle **120 stars**，hypothesis **125**。两金段在 execute/rerank/judge 池全程在且 rerank 对对话段打分 **0.89+**，说明**不是没搜到**；更可能是 **证据内多版本数字 / judge 仍 INSUFFICIENT 未强制消解** 后，**写答案环节**取了偏高的 125。",
    "1c0ddc50": "oracle 为长 **preference**（通勤要听 **history 类新播客/有声书**，避免 true crime/self-improvement、避免费眼活动）。**首轮 judge 即 SUFFICIENT** 且金段在池，说明 workspace 认为「够答」；hypothesis 却列泛化播客/音乐/语言 App，**属答句生成未按 preference 模板组织**（非检索缺失）。",
    "1da05512": "oracle 强调 **现有外置硬盘痛点 + NAS 是否对症** 的偏好回应；hypothesis 变成 **NAS 型号与价位清单**。Round2 **judge 后池曾出现金段不在池**（中间轮噪声膨胀），Round3 金段回到窄池但 judge 仍 **INSUFFICIENT**；最终仍输出清单式答案，**主因是生成侧把题当成「买哪款 NAS」而非「结合用户存储史给建议」**。",
    "38146c39": "oracle 要求围绕用户已试的 **turbinado sugar** 给「额外一招」；hypothesis 给的是通用 **盐/焦化黄油**。金段始终在池；末轮 judge **INSUFFICIENT + stagnant**，但答句仍是通用烘焙技巧，**主因在 hypothesis：未从金段事实中抽取 sugar 语境**。",
    "3b6f954b": "oracle 为 **University of Melbourne in Australia**；hypothesis 只有校名。**首轮 judge=SUFFICIENT**、金段在池，说明检索与 judge 闭环已放行；**错在最终答句漏写「in Australia」**，属要素级截断或 autoeval 对地理限定过严。",
    "5a7937c8": "oracle **3 days**，hypothesis **2**。多轮检索后 summary **2 hit / 1 miss**，说明有一条金段**从未进入末态 `evidence_episode_refs`**；同时即便在池片段也可能未覆盖全部「十二月信仰活动」日程，**计数在聚合层算少**（检索漏 + 推理漏叠加）。",
    "6b7dfb22": "oracle preference 要 **色彩理论、混合媒介、艺术家研究、突破舒适区** 等；hypothesis 变成 **社媒找灵感 + 30 天挑战** 泛建议。金段在池且末轮 judge **INSUFFICIENT**，但答句未体现 preference 列出的维度，**主因是生成未对齐长 oracle 要点**（检索有、模板无）。",
    "95228167": "oracle 为 **Strat vs Les Paul 手感/重量/音色** 对比类 preference；hypothesis **明确拒答**。summary **0 hit / 1 miss** 表示末态引用片段**未覆盖金段**；三轮 judge 多为 **INSUFFICIENT**，**根因链：检索/裁剪未把可答金段稳定留在末池 → 策略上选择弃权**（不等同于「真无记忆」）。",
    "afdc33df": "oracle 要的是 **厨房清洁** 相关偏好（台面、油渍等侧重点）；hypothesis 扩成 **餐具收纳、垃圾处理器、花岗岩、水龙头** 大杂烩。金段 summary 全中，**首轮即 SUFFICIENT**，错误几乎纯 **答句跑题/过度泛化**，非检索。",
    "caf03d32": "oracle 为 **slow cooker / 素食高蛋白** 等偏好约束下的建议；summary **0 hit / 2 miss** 说明两条金段**从未进末态引用**；hypothesis 又是通用蔬菜/酸奶技巧，**检索漏金段为主、生成为辅**。",
    "gpt4_15e38248": "家具「买/装/卖/修」计数，oracle 非 **1**；summary **0 hit**（5 条金段均未进末态池）。多轮 execute 池很大但 judge 长期 **INSUFFICIENT**，**主因链：检索虽广但未把跨会话金段全部抬进最终证据闭包**（或 judge 反复收窄仍丢）。",
    "gpt4_2f56ae70": "问「**most recently** 用的流媒体」；summary **1 hit / 2 miss**，hypothesis 列 **Netflix/Hulu/Prime** 三家。**主因：时间序相关的部分金段未进末池**，生成器只能凭残片列多个平台而无法锁定「最近一个」。",
    "gpt4_d12ceb0e": "oracle 数值 **59.6**（平均年龄）；三轮 execute/rerank **三条金段均在池**，但 judge 持续 **INSUFFICIENT**，`next_query` 纠结 **祖母去世是否计入平均** 等歧义，**未形成 SUFFICIENT 闭环**；最终 hypothesis **null**，**主因：judge/策略在歧义前中止，未进入算术聚合答句**（不是没搜到年龄）。",
    "gpt4_f420262c": "oracle 航司序 **JetBlue, Delta, United, American**；hypothesis 仅 **United, American**。summary **1 hit / 4 miss**，多轮 judge **INSUFFICIENT**；execute 池常含部分金段但 **JetBlue/Delta 等段未稳定留在 judge 后窄池**，**主因：检索+时间线整合不足 + judge 窄池丢段 → 排序答错**。",
}


def _top_ids_are_scene_only(tops: List[Tuple[str, float]], k: int = 2) -> bool:
    if not tops:
        return False
    for eid, _ in tops[:k]:
        s = str(eid)
        if not (s.startswith("time:scene_") or s.startswith("time:")):
            return False
    return True


def build_error_diagnosis(
    trace: Dict[str, Any],
    gold: Set[str],
    hyp: str,
    oa: str,
    summary_hit: int,
    summary_miss: int,
) -> str:
    """Structured 错在哪 / 哪一环节 / 因何 — 以 trace 可观测字段为主。"""
    out: List[str] = []
    out.append("#### 错误定位（错在哪 → 哪一环节 → 因何）\n\n")

    hyp_s = (hyp or "").strip()
    hyp_low = hyp_s.lower()
    oa_s = (oa or "").strip()

    # 判题层
    if "not enough memory evidence" in hyp_low or "do not have enough" in hyp_low:
        out.append(
            "- **错在哪（相对 oracle）**：输出了**拒答/证据不足**类句子，而 oracle 期望基于用户记忆的**具体对比或偏好陈述**（见截断 oracle）。\n"
        )
    elif hyp_low in ("null", "none") or hyp_s == "":
        out.append(
            "- **错在哪（相对 oracle）**：hypothesis 为 **空/null**，与 oracle 的**数值/事实答案**完全对不上。\n"
        )
    else:
        out.append(
            f"- **错在哪（相对 oracle）**：oracle 要点：**「{md_escape_cell(oa_s, 160)}」**；"
            f"模型输出：**「{md_escape_cell(hyp_s, 160)}」**。二者在 gpt-4o autoeval 下判为不等价（体裁/事实/要素缺失等见语义小结）。\n"
        )

    result = (trace.get("model_recall") or {}).get("result") or {}
    wr = result.get("workspace_rounds")
    if not isinstance(wr, list) or not wr:
        out.append(
            f"- **哪一环节 / 因何（可观测度低）**：无 `workspace_rounds` 记录。仅知 summary 金段 **{summary_hit} hit / {summary_miss} miss**；"
            "若 miss>0，优先怀疑 **末态 `evidence_episode_refs` 未覆盖全部金段**（检索闭环或 judge 裁剪）。\n\n"
        )
        return "".join(out)

    n_gold = max(len(gold), 1)
    per: List[Dict[str, Any]] = []
    for rd in wr:
        if not isinstance(rd, dict):
            continue
        rid = rd.get("round_id")
        se = evidence_id_set(rd.get("workspace_after_execute"))
        sr = evidence_id_set(rd.get("workspace_after_rerank"))
        sj = evidence_id_set(rd.get("workspace_after_judge"))
        ne, nr, nj = len(se), len(sr), len(sj)
        ge, gr, gj = len(gold & se), len(gold & sr), len(gold & sj)
        judge = rd.get("judge") if isinstance(rd.get("judge"), dict) else rd.get("judge_result")
        jstat = str((judge or {}).get("status") or "") if isinstance(judge, dict) else ""
        jgap = str((judge or {}).get("gap_type") or "") if isinstance(judge, dict) else ""
        snap_r = rd.get("workspace_after_rerank")
        tops = top_rerank_hits(snap_r, 4)
        gold_in_top = any(t[0] in gold for t in tops) if tops else False
        per.append(
            {
                "rid": rid,
                "ge": ge,
                "gr": gr,
                "gj": gj,
                "ne": ne,
                "nr": nr,
                "nj": nj,
                "jstat": jstat,
                "jgap": jgap,
                "tops": tops,
                "gold_in_top": gold_in_top,
            }
        )

    # 1) 首轮检索是否覆盖金段
    if per:
        p0 = per[0]
        if gold and p0["ge"] < len(gold):
            out.append(
                f"- **哪一环节：Planner + execute（Round {p0['rid']}）**。首轮 execute 后金段仅 **{p0['ge']}/{len(gold)}** 在池、池大小 **{p0['ne']}**。"
                "**因**：detail/time 查询与 topk 截断未同时捞回全部对话金段，后续只能在子集上推理。\n"
            )
        elif gold and p0["ge"] == len(gold):
            out.append(
                f"- **Planner + execute（Round {p0['rid']}）**：首轮 execute 后 **全部金段已在池**（{p0['ne']} 条），**检索侧未丢金段**。\n"
            )

    # 2) rerank 头部是否被 time:scene 占据且金段不在 top
    for p in per:
        if p["gr"] < len(gold) and p["ge"] >= len(gold):
            out.append(
                f"- **哪一环节：rerank（Round {p['rid']}）**：execute 金段满覆盖，但 rerank 后金段降为 **{p['gr']}/{len(gold)}**（池 {p['nr']} 条）。"
                "**因**：重排/keep 规则在该轮把部分对话金段挤出候选。\n"
            )
            break
    for p in per:
        tops = p.get("tops") or []
        if (
            tops
            and len(gold) > 0
            and p["gr"] >= len(gold)
            and p["gr"] > 0
            and not p.get("gold_in_top")
            and _top_ids_are_scene_only(tops, 2)
        ):
            out.append(
                f"- **哪一环节：rerank 打分（Round {p['rid']}）**：rerank 后金段条数仍齐（**{p['gr']}/{len(gold)}**），但 **top 分前几名均为 `time:scene_*`**，对话金段未出现在该头部列表。"
                "**因**：混排下 **时间片在分数上压过对话金段**，易影响后续 **useful/judge** 对「该读哪几条」的注意分配。\n"
            )
            break

    # 3) 同轮 rerank → judge 后金段变少
    for p in per:
        if p["gr"] > p["gj"]:
            out.append(
                f"- **哪一环节：judge / keep（Round {p['rid']}）**：rerank 后金段 **{p['gr']}/{len(gold)}**，judge 后池 **{p['nj']}** 条、金段 **{p['gj']}/{len(gold)}**；"
                f"judge `status={p['jstat']}`, `gap_type={p['jgap']}`。**因**：**judge 选出的有用证据集合或 kept 列表未保留全部金段**，后续只能基于子集作答或继续追问。\n"
            )
            break

    # 4) 末轮 judge 与最终输出的关系（拒答优先于其他 INSUFFICIENT 叙事）
    last = per[-1]
    if "not enough" in hyp_low:
        out.append(
            "- **哪一环节：拒答策略**：输出显式「证据不足」类句子；末轮 judge 常为 **INSUFFICIENT** 或末池金段覆盖不足。"
            "**因**：**在 judge/策略视角可答闭包未成立**（常与 summary **0 hit** 或末池缺金段一致），与 oracle 仍期望具体答案冲突。\n"
        )
    elif last["jstat"] == "SUFFICIENT" and last["gj"] > 0:
        out.append(
            "- **哪一环节：hypothesis / 最终答句生成**：末轮 judge 已为 **SUFFICIENT** 且 judge 后池仍含金段。"
            "**因**：**写答案模型未把 oracle 要求的 preference 要素或地理/数值细节写全**（workspace 侧已放行）。\n"
        )
    elif last["jstat"] == "INSUFFICIENT" and (hyp_low in ("null", "none") or hyp_s == ""):
        out.append(
            "- **哪一环节：judge 闭环未收敛 → 输出空**：末轮 judge **INSUFFICIENT**，hypothesis 为 null/空。"
            "**因**：**歧义或 need_more_evidence 未解开**时策略中止，未把已有片段上的数字**聚合为最终数值答案**。\n"
        )
    elif last["jstat"] == "INSUFFICIENT" and hyp_low not in ("null", "none", ""):
        out.append(
            f"- **哪一环节：hypothesis 与 judge 闭环脱钩**：末轮 judge 仍为 **INSUFFICIENT**（`gap_type={last['jgap'] or '—'}`），但最终仍输出较长答句。"
            "**因**：**生成路径未与「证据不足」约束严格对齐**，或证据内存在冲突数字时仍强行给单值答案。\n"
        )

    # 5) summary 与末轮对齐提示
    if summary_miss > 0:
        out.append(
            f"- **与 summary 对齐**：`trace_longmemeval_evidence.py` 末态为 **{summary_hit} hit / {summary_miss} miss**，表示 **至少一条金段未出现在最终 `evidence_episode_refs`**；"
            "若上文某轮已显示「金段曾进池」，则还存在 **末态引用集与中间轮池不一致**（以 summary 为末态准绳）。\n"
        )

    out.append("\n")
    return "".join(out)


def build_round_section(trace: Dict[str, Any], gold: Set[str]) -> str:
    res = (trace.get("model_recall") or {}).get("result") or {}
    rounds = res.get("workspace_rounds")
    if not isinstance(rounds, list) or not rounds:
        return (
            "**workspace_rounds**：本 trace 未记录多轮 workspace（仅可从 `model_recall.result` 顶层字段推断）。\n"
        )
    lines: List[str] = []
    for rd in rounds:
        if not isinstance(rd, dict):
            continue
        rid = rd.get("round_id")
        lines.append(f"\n**Round {rid}**\n\n")
        lines.append("| 步骤 | 内容 |\n|------|------|\n")
        planner = format_planner_actions(rd.get("actions"))
        lines.append(f"| **Planner** | {md_escape_cell(planner, 500)} |\n")

        for label, key in (
            ("execute", "workspace_after_execute"),
            ("rerank", "workspace_after_rerank"),
            ("judge 后池", "workspace_after_judge"),
        ):
            snap = rd.get(key)
            n, st, gap = snap_meta(snap)
            pool = evidence_id_set(snap)
            gh = gold_hits(gold, pool)
            miss_g = sorted(gold - set(gh))
            gh_s = "是（" + ", ".join(gh[:2]) + ("…" if len(gh) > 2 else "") + ")" if gh else "否"
            if miss_g and gh:
                gh_s += f"；未命中金段: {md_escape_cell(','.join(miss_g[:2]), 120)}"
            row = f"条数 **{n}**；`status={st}`；`gap_type={gap}`；金段在池: **{gh_s}**"
            if label == "rerank":
                tops = top_rerank_hits(snap, 4)
                if tops:
                    ts = "；".join(f"`{md_escape_cell(t[0], 60)}` **{t[1]:.3f}**" for t in tops)
                    row += f"；rerank 分头部: {ts}"
                else:
                    row += "；rerank 分: （多为 null）"
            lines.append(f"| **{label}** | {row} |\n")

        judge = rd.get("judge") if isinstance(rd.get("judge"), dict) else rd.get("judge_result")
        if isinstance(judge, dict):
            jtxt = (
                f"`status={judge.get('status')}`；`gap_type={judge.get('gap_type')}`；"
                f"useful_evidence_ids 数={len(judge.get('useful_evidence_ids') or [])}"
            )
            nq = judge.get("next_query")
            if nq:
                jtxt += f"；next_query: {md_escape_cell(str(nq), 150)}"
            lines.append(f"| **judge 输出** | {jtxt} |\n")
        lines.append("\n")
    return "".join(lines)


def gold_hits(gold: Set[str], pool: Set[str]) -> List[str]:
    return sorted(gold & pool)


def run_report(cfg: ReportConfig) -> Tuple[int, int, int, Path]:
    """Write report to cfg.out_path. Returns (n_total, n_ok, n_wrong, out_path)."""
    log_dir = cfg.log_dir.resolve()
    trace_root = log_dir / "recall_trace"
    eval_path = log_dir / "longmemeval_hypothesis.jsonl.eval-results-gpt-4o"
    hyp_path = log_dir / "longmemeval_hypothesis.jsonl"
    summary_path = trace_root / "summary.jsonl"

    eval_rows = load_jsonl(eval_path)
    hyp_map = load_jsonl_map(hyp_path, "question_id")
    summary_map = load_jsonl_map(summary_path, "question_id")
    qtypes = question_type_index(cfg.data_json)

    n_total = len(eval_rows)
    n_ok = sum(1 for r in eval_rows if (r.get("autoeval_label") or {}).get("label") is True)
    acc = n_ok / n_total if n_total else 0.0

    wrong_ids = [str(r["question_id"]) for r in eval_rows if (r.get("autoeval_label") or {}).get("label") is not True]

    lines: List[str] = []
    lines.append(f"# {cfg.report_title}\n\n")
    lines.append(cfg.dataset_blurb)

    lines.append("## 1. 想搞清的一件事（一句话）\n\n")
    lines.append(cfg.section1_one_liner)

    rel_log = _path_under_repo(log_dir)
    lines.append("## 2. 总览（带题干）\n\n")
    lines.append(
        f"数据来源：`{rel_log}/longmemeval_hypothesis.jsonl`、"
        f"`{rel_log}/longmemeval_hypothesis.jsonl.eval-results-gpt-4o`、"
        f"`{rel_log}/recall_trace/summary.jsonl`、各题 `{rel_log}/recall_trace/<id>.json`。\n\n"
        f"**autoeval**：上游 `evaluate_qa.py` + gpt-4o（见 eval-results 文件内 `autoeval_label.model`）。\n\n"
        f"**summary 金段 hit/miss**：`trace_longmemeval_evidence.py` 按最终 `evidence_episode_refs` 与金段集合对齐；"
        f"若与某一回合池内 `evidence_id` 不一致，以 **trace 逐轮** 为准核对。\n\n"
        f"**workspace 轮数**：`len(model_recall.result.workspace_rounds)`（缺字段则记为 N/A）。\n\n"
        f"**本轮 Accuracy（autoeval）**：**{acc:.4f}**（{n_ok}/{n_total}）。\n\n"
    )

    lines.append(
        "| ID | 问题原文（oracle.question，截断） | hypothesis（极短意） | autoeval | workspace 轮数 | gold 段 hit/miss（summary） |\n"
        "|----|--------------------------------------|----------------------|----------|----------------|-----------------------------|\n"
    )

    wrong_by_type: Dict[str, List[str]] = defaultdict(list)
    wrong_failure: Dict[str, List[str]] = defaultdict(list)

    for er in eval_rows:
        qid = str(er.get("question_id") or "")
        label = (er.get("autoeval_label") or {}).get("label")
        ok = label is True

        hyp_row = hyp_map.get(qid, {})
        hypothesis = str(hyp_row.get("hypothesis") or "")
        summ = summary_map.get(qid, {})

        trace_path = trace_root / f"{qid}.json"
        qtext = ""
        n_rounds = "N/A"
        if trace_path.is_file():
            try:
                trace = json.loads(trace_path.read_text(encoding="utf-8"))
                qtext = str((trace.get("oracle") or {}).get("question") or "")
                res = (trace.get("model_recall") or {}).get("result") or {}
                wr = res.get("workspace_rounds")
                if isinstance(wr, list):
                    n_rounds = str(len(wr))
            except Exception:
                qtext = ""

        hit = int(summ.get("hit_gold_segment_count", 0) or 0)
        miss = int(summ.get("missed_gold_segment_count", 0) or 0)
        oa = str(summ.get("oracle_answer") or "")

        if not ok:
            qt = qtypes.get(qid, "（未知）")
            wrong_by_type[qt].append(qid)
            fc = classify_failure(qid, oa, qtext, hypothesis, hit, miss)
            wrong_failure[fc].append(qid)

        lines.append(
            f"| {qid} | {md_escape_cell(qtext, 100)} | {md_escape_cell(hypothesis, 72)} | "
            f"{'true' if ok else '**false**'} | {n_rounds} | {hit} hit / {miss} miss |\n"
        )

    lines.append(cfg.section4_preface)

    lines.append("## 3. 读 trace 时几个字段啥意思（避免看不懂）\n\n")
    lines.append(
        f"均来自 `{rel_log}/recall_trace/<id>.json` → `model_recall.result.workspace_rounds[]`：\n\n"
        "- **Planner**：`actions[].action_type` + `actions[].query`（`detail_query`、`start_time` / `end_time` 等）。\n"
        "- **execute 之后**：`workspace_after_execute` 的 `evidences` 条数与 `evidence_id`；`status` 为 `INVALID` 且 `gap_type` 为 `round_produced_nothing` 时表示该回合元数据上「未产生有效增量」，但池内仍可能保留旧证据。\n"
        "- **rerank 之后**：`workspace_after_rerank`；条数减少表示被裁剪；`rerank_score` 全为 `null` 时本轮可能未逐条重打。\n"
        "- **judge**：`judge` / `judge_result` 的 `status`（`INSUFFICIENT` / `SUFFICIENT` / `INVALID`）、`gap_type`、`next_query`。\n"
        "- **金段在池**：`oracle.gold_segments[].episode_ref` 是否出现在对应阶段 `evidences[].evidence_id` 中。\n\n"
        "---\n\n"
    )

    lines.append("## 4. 错题：按「每轮」写清 Planner → execute → rerank → judge → 金段\n\n")
    lines.append(f"**autoeval=false 共 {len(wrong_ids)} 题**：`{', '.join(wrong_ids)}`。\n\n")

    for qid in wrong_ids:
        trace_path = trace_root / f"{qid}.json"
        if not trace_path.is_file():
            lines.append(f"### 4.x `{qid}` —— trace 缺失，跳过。\n\n")
            continue
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        oracle = trace.get("oracle") or {}
        qfull = str(oracle.get("question") or "")
        oa = str(oracle.get("answer") or "")
        hyp = str((hyp_map.get(qid) or {}).get("hypothesis") or "")
        summ = summary_map.get(qid, {})
        gold = gold_refs(trace)

        lines.append(f"### `{qid}` —— *{md_escape_cell(qfull, 120)}*\n\n")
        lines.append(f"**oracle 答案（截断）**：{md_escape_cell(oa, 220)}\n\n")
        lines.append(f"**本轮 hypothesis（截断）**：{md_escape_cell(hyp, 280)}\n\n")
        lines.append("**autoeval**：false。\n\n")
        if gold:
            lines.append(
                "**金段 episode_ref**："
                + "；".join(f"`{md_escape_cell(g, 100)}`" for g in sorted(gold)[:6])
                + ("…" if len(gold) > 6 else "")
                + "\n\n"
            )
        hit = int(summ.get("hit_gold_segment_count", 0) or 0)
        miss = int(summ.get("missed_gold_segment_count", 0) or 0)
        lines.append(f"**summary 金段**：{hit} hit / {miss} miss。\n\n")
        lines.append(build_round_section(trace, gold))
        if cfg.tables_only_wrong:
            lines.append(
                "\n#### 根因分析（人工）\n\n"
                "_（本节由报告撰写者根据上表逐轮填写：金段首次丢失发生在 execute / rerank / judge 哪一步，"
                "或末态全中仍错时写清 hypothesis 生成侧原因。）_\n\n"
            )
        else:
            lines.append(build_error_diagnosis(trace, gold, hyp, oa, hit, miss))
            one_liner = QUESTION_SUMMARIES.get(qid, "（见 §5.1 失败形态归类。）")
            lines.append(f"**小结（语义层）**：{one_liner}\n\n")
        lines.append("---\n\n")

    lines.append("## 5. 错题归类（taxonomy）\n\n")
    lines.append("### 5.1 按失败形态（结合 oracle 句式、summary、逐轮 trace）\n\n")
    for cat in sorted(wrong_failure.keys()):
        ids = wrong_failure[cat]
        lines.append(f"- **{cat}**（{len(ids)}）：" + "、".join(f"`{x}`" for x in ids) + "\n")
    lines.append(
        "\n说明：少数题兼有多类因素（例如 `caf03d32` 同时有 preference 失配与检索漏金段），上表按 **优先规则**（拒答/空答 → preference 句式 → 检索覆盖 → 其余）归入主类。\n\n"
    )

    lines.append("### 5.2 按数据集 `question_type`（来自 `longmemeval_s_cleaned.json`）\n\n")
    if not qtypes:
        lines.append("（未能读取 `data/LongMemEval/data/longmemeval_s_cleaned.json`，本节略。）\n\n")
    else:
        lines.append("| question_type | 错题数 | question_id |\n|---------------|--------|-------------|\n")
        for qt in sorted(wrong_by_type.keys(), key=lambda x: (-len(wrong_by_type[x]), x)):
            ids = wrong_by_type[qt]
            lines.append(f"| {qt} | {len(ids)} | " + "、".join(f"`{i}`" for i in ids) + " |\n")
        lines.append("\n")

    sec = 6
    if not cfg.skip_compare_section:
        lines.append("---\n\n")
        lines.append("## 6. 与 `try_each_2_plus` 对拍（重叠题简表）\n\n")
        lines.append(
            "`try_each_2_plus` 小样本报告中的题：`00ca467f`, `06f04340`, `2b8f3739`, `4100d0a0`, `488d3006`, `4d6b87c8`, `75f70248`, `86f00804`, `8b9d4367`, `945e3d21`, `gpt4_2312f94c`, `gpt4_78cf46a3`。\n\n"
            "其中本轮 `_4` 亦包含：`488d3006`, `4d6b87c8`, `75f70248`, `945e3d21` 等。\n\n"
            "| question_id | try_each_2_plus（参考报告） | try_each_4（本轮） |\n"
            "|-------------|-----------------------------|---------------------|\n"
            "| 488d3006 | autoeval true | autoeval true |\n"
            "| 4d6b87c8 | autoeval true | autoeval true |\n"
            "| 75f70248 | autoeval **false**（preference） | autoeval **true**（hypothesis 仍为常识向 Yes）——**裁判标签翻转**，说明 preference 题对 autoeval 方差大。 |\n"
            "| 945e3d21 | autoeval true | autoeval true |\n"
        )
        lines.append(
            "\n**归因提醒**：两批 **eval.test_id、抽样题集、运行时刻** 不同；若无 git/配置冻结记录，避免强因果断言。\n\n"
        )
        sec = 7

    if not cfg.skip_design_section:
        lines.append("---\n\n")
        lines.append(f"## {sec}. 从这些现象里能抽的设计思路（简要）\n\n")
        lines.append(
            "**A —— preference 题**：即使金段在池，输出仍常为常识句；autoeval 在跨 run 间可能翻转，需与任务约束或显式「材料覆盖」指标并用。\n\n"
            "**B —— 数值/聚合**：计数或平均年龄类错误时，核对 judge 是否长期 `INSUFFICIENT` 与最终短答是否脱钩。\n\n"
            "**C —— 拒答**：在 evidence 实际可支撑答案时仍输出「证据不足」→ 需在 policy 层区分「真无证据」与「未检索到」。\n\n"
            "**D —— rerank 全 null / INVALID**：排障时对照 execute 池大小与 `gap_type`，避免误读为「完全无召回」。\n\n"
            "---\n\n"
        )
        sec += 1

    lines.append(f"## {sec}. 本文件与数据路径\n\n")
    for fl in cfg.footer_lines:
        lines.append(fl)
    if not cfg.footer_lines:
        lines.append(f"- 本报告：`{cfg.out_path.as_posix()}`\n")

    cfg.out_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.out_path.write_text("".join(lines), encoding="utf-8")
    print(f"Wrote {cfg.out_path} ({n_total} rows, {len(wrong_ids)} wrong, acc={acc:.4f})")
    return n_total, n_ok, len(wrong_ids), cfg.out_path


def without_time_recall_config() -> ReportConfig:
    log_dir = ROOT / "log" / "longmemeval_run_try_each_4_without_time_recall"
    return ReportConfig(
        log_dir=log_dir,
        out_path=log_dir / "phase_next_research_report.md",
        report_title="LongMemEval 研究报告（`try_each_4_without_time_recall`）",
        dataset_blurb=(
            "> 本轮数据在 `log/longmemeval_run_try_each_4_without_time_recall/`。"
            "配置为 **不启用时间窗召回**（无 `EVENT_TIME_RECALL` 路径）；其余仍为 **facts + Episode → 多轮 detail 召回 → rerank → judge → hypothesis**。\n\n"
            "---\n\n"
        ),
        section1_one_liner=(
            "本批日志要回答：在同一套 **60 题分层抽样** 上，去掉时间召回后 **gpt-4o autoeval** 错题各自在 **每一轮 workspace** 里卡在哪一步，"
            "以及 **summary 末态金段** 与逐轮池是否一致。\n\n"
            "---\n\n"
        ),
        section4_preface=(
            "\n**§4** 中 **逐轮表** 由脚本从 trace **原样摘录**；**「根因分析（人工）」** 为逐题撰写的因果说明（非脚本泛泛生成）。\n\n"
            "**§5** 为脚本按规则预归类；可在人工补全 §4 后回头微调本节表述。\n\n---\n\n"
        ),
        tables_only_wrong=True,
        skip_compare_section=True,
        skip_design_section=True,
        footer_lines=[
            f"- 本报告：`{_path_under_repo(log_dir)}/phase_next_research_report.md`\n",
            f"- 依据：`{_path_under_repo(log_dir)}/recall_trace/*.json`、`recall_trace/summary.jsonl`、`longmemeval_hypothesis.jsonl`、`.eval-results-gpt-4o`\n",
            "- 环境：`config/eval/memory_agent/longmemeval/test_env_try_4.yaml`（`eval.test_id: longmemeval_run_try_each_4_without_time_recall`）\n",
        ],
        data_json=_DEFAULT_DATA,
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Generate LongMemEval phase research markdown.")
    p.add_argument(
        "--preset",
        choices=("try_each_4", "without_time_recall"),
        default="try_each_4",
        help="try_each_4: original bundled report; without_time_recall: tables-only wrong + no §6/7.",
    )
    p.add_argument("--log-dir", type=str, default="", help="Override log directory (absolute or relative to repo root).")
    p.add_argument("--out", type=str, default="", help="Output markdown path.")
    args = p.parse_args()

    if args.preset == "without_time_recall":
        cfg = without_time_recall_config()
    else:
        cfg = default_try4_config()

    if args.log_dir.strip():
        cfg.log_dir = (ROOT / args.log_dir).resolve() if not Path(args.log_dir).is_absolute() else Path(args.log_dir)
        if args.preset == "try_each_4":
            cfg.out_path = cfg.log_dir / "phase_next_research_report.md"
    if args.out.strip():
        cfg.out_path = Path(args.out).resolve() if Path(args.out).is_absolute() else (ROOT / args.out).resolve()

    run_report(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
