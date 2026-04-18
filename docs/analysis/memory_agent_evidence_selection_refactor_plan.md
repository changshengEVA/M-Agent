# MemoryAgent 证据选择层改造方案（面向多步检索与最终回答前证据定稿）

## 1. 现状诊断（基于当前代码）

当前 `ask -> deep_recall` 的真实链路是：

1. 直答路径：`_answer_directly`
2. 失败回退后：`_decompose_question -> _solve_sub_questions -> _synthesize_final_answer`
3. 最后 `_finalize_recall_payload` 附带 `tool_calls` 和 `sub_question_results`

关键问题不是“没有证据”，而是“**没有证据定稿阶段**”：

- 子问题阶段只保留了 `answer/gold_answer/evidence` 文本摘要，未形成统一证据索引。
- 最终综合 `_synthesize_final_answer` 输入的是子问题结果，不是显式选择后的证据集合。
- `tool_calls` 虽然记录了工具结果，但目前仅作为追踪日志暴露给最终 payload，没有被统一转成可选择证据。

对应位置：

- `_solve_sub_questions`: [memory_agent.py:1389](F:/AI/M-Agent/src/m_agent/agents/memory_agent.py:1389)
- `_synthesize_final_answer`: [memory_agent.py:1465](F:/AI/M-Agent/src/m_agent/agents/memory_agent.py:1465)
- `_finalize_recall_payload`: [memory_agent.py:1533](F:/AI/M-Agent/src/m_agent/agents/memory_agent.py:1533)
- `tool_calls` 记录机制：[_record_tool_call](F:/AI/M-Agent/src/m_agent/agents/memory_agent.py:217), [_finalize_tool_call](F:/AI/M-Agent/src/m_agent/agents/memory_agent.py:230)

结论：当前更像“工具检索 + 文本汇总”，而不是“检索 -> 证据筛选 -> 回答”。

---

## 2. 改造目标

在“最终回答前”新增一个**证据选择层（Evidence Selection Layer）**，让输出从“有证据摘要”升级成“有证据清单、可追溯、可评测覆盖”。

改造后目标：

1. 将各工具结果统一转换为 `evidence candidates`
2. 对候选证据去重、评分、选择
3. 最终回答只基于“选中的证据”生成
4. 最终 payload 返回可机器评测的证据索引（支持你的 Support Coverage）

---

## 3. 新增数据契约（最小侵入）

在 `ask` 最终返回中新增字段（保留旧字段不变）：

1. `evidence_index`: 候选证据列表（TopN）
2. `selected_evidence_ids`: 最终采纳证据 ID 列表
3. `evidence_summary`: 人类可读证据摘要（保留现有 `evidence` 语义）
4. `evidence_selection_stats`: 选择过程统计（候选数、去重后数量、多源覆盖情况）

证据项结构建议：

- `evidence_id`（例如 `E1/E2/...`）
- `source_tool`（`search_details/search_content/...`）
- `sub_question_index`（直答为 0）
- `dialogue_id`
- `episode_id`
- `turn_span`
- `scene_id`
- `atomic_fact` / `snippet`
- `raw_score`（工具原始分）
- `fused_score`（统一评分）
- `is_selected`（是否入选）

---

## 4. 证据选择层流程（回答前新增 Stage）

在现有流程里新增一个阶段：

`SubQ solved` -> `Evidence Build & Select` -> `Final Synthesis`

## 4.1 Candidate Build（候选构建）

输入来源：`tool_calls`（不是子问题的自由文本 evidence）。

按工具结果转候选：

1. `search_details`：每个 result 直接映射候选（有 `Atomic fact + episode_id + dialogue_id + similarity`）
2. `search_content`：映射为 episode/turn 级候选（带 `turn_span` 与关键 turns 摘要）
3. `search_entity_feature/event/events_by_time`：从命中项中抽取 source（dialogue_id/episode_id）并映射候选
4. `search_events_by_time_range`：scene 级候选（置信度较低，可作为补充）

## 4.2 Candidate Normalize（归一化）

统一证据主键：

`key = dialogue_id + episode_id + turn_span + atomic_fact_hash`

没有 turn_span 时用 episode 粒度兜底：

`key = dialogue_id + episode_id + atomic_fact_hash`

## 4.3 Candidate Score（评分）

第一版使用规则分（无需额外模型）：

- `score_tool`: 工具原始分标准化（如 similarity）
- `score_support`: 被多少子问题间接支持（同一证据在多个 subq 相关 tool call 出现）
- `score_granularity`: turn 级 > episode 级 > scene 级
- `score_conflict_penalty`: 与已选证据冲突时减分

建议初始公式：

`fused = 0.50*score_tool + 0.30*score_support + 0.20*score_granularity - conflict_penalty`

## 4.4 Evidence Select（选择）

按问题类型选择证据数量：

1. `direct_lookup`: 2~4 条
2. `comparison/causal/multi_hop`: 4~8 条
3. `summary`: 5~10 条（强调覆盖）

多源保障策略：

- 若检测到多子问题或比较型问题，约束至少来自 `>=2` 个 episode（可配置）
- 不满足时从未覆盖 source 中强制补 1 条高分证据

## 4.5 Final Synthesis（最终回答）

`_synthesize_final_answer` 的输入改为：

- `question_text`
- `question_plan`
- `selected_evidence`（结构化）

要求模型输出时必须引用 `evidence_id`（例如 `E2/E5`），并生成：

- `answer`
- `gold_answer`
- `evidence`（摘要）
- `used_evidence_ids`（可选）

---

## 5. 代码改造落点（函数级）

## 5.1 MemoryAgent 主体

文件：[memory_agent.py](F:/AI/M-Agent/src/m_agent/agents/memory_agent.py)

新增函数建议：

1. `_build_evidence_candidates(tool_calls, sub_question_results) -> List[Dict]`
2. `_dedupe_evidence_candidates(candidates) -> List[Dict]`
3. `_score_evidence_candidates(candidates, question_plan) -> List[Dict]`
4. `_select_evidence(candidates, question_plan) -> Tuple[selected, stats]`
5. `_build_final_synthesis_prompt_with_evidence(...)`

流程插入点：

1. `deep_recall` 在 `sub_question_results` 之后、`_synthesize_final_answer` 之前插入 evidence selection stage
2. `direct path` 成功返回前也走同样 evidence selection（避免仅在 decomposition 生效）

## 5.2 Prompt 层

文件：[agent_runtime.yaml](F:/AI/M-Agent/config/agents/memory/runtime/agent_runtime.yaml)

新增 prompt：

1. `evidence_selection_policy_prompt`（可选，先规则后 LLM）
2. `final_synthesis_with_evidence_prompt`

并将 `final_synthesis_prompt` 替换为“显式证据输入版本”。

---

## 6. 与你现有评测体系对齐（LLMJudge + Support Coverage）

你不需要先上 Recall/MRR，先做这两项就够：

1. `LLMJudge`（主）
2. `Support Coverage`（证据覆盖）

因为新增了 `selected_evidence_ids + evidence_index`，可直接计算：

1. `Episode Coverage@K`：gold episode 集合被选中证据覆盖比例
2. `All-Source-Hit@K`：多源题是否所有必需来源都命中

这会比只看 `evidence` 文本摘要稳定很多。

---

## 7. 实施顺序（建议）

## Phase A（1-2 天，低风险）

1. 仅新增 `evidence_index/selected_evidence_ids/evidence_selection_stats`
2. 先用规则法选择，不改现有回答 prompt
3. 保持旧输出兼容

## Phase B（2-3 天，中风险）

1. 最终综合改为只吃 `selected_evidence`
2. 回答中要求引用 `evidence_id`
3. 增加冲突检测与多源补齐

## Phase C（1-2 天，评测）

1. 接入 `Support Coverage` 统计
2. 跑 LOCOMO 子集对比（旧链路 vs 新链路）
3. 若 LLMJudge 升但 Coverage 降，优先修选择策略

---

## 8. 风险与兜底

1. 风险：证据选择过严导致可用上下文减少  
   兜底：保留“topN fallback”（例如至少保留 4 条）

2. 风险：结构化证据转换丢字段  
   兜底：`raw_payload` 冗余保存，转换失败时回退旧摘要逻辑

3. 风险：多源约束导致噪声证据混入  
   兜底：多源补齐仅在 `question_type in {comparison, causal, multi_hop}` 启用

---

## 9. 你现在就能先做的最小改动（MVP）

MVP 只做三件事：

1. 从 `tool_calls` 生成 `evidence_index`
2. 选 TopK 到 `selected_evidence_ids`
3. 最终 payload 带上这两个字段

只做这三步，你就能马上把“评测与回答证据链”打通，不需要一次性重写全流程。

