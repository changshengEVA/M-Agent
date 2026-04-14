# MemoryAgent 运行方式重构方案（事件层优先，Workspace 三态闭环）

更新时间：2026-04-12  
适用范围：当前 `facts_only` 现状（仅事件层 recall 工具），并为未来实体层扩展预留接口。

## 1. 结论先行：流程可行，但需要补齐 4 个关键缺口

你提出的 4 阶段流程是可行的，而且和当前代码形态可以平滑对接。  
核心风险不在“是否能做”，而在“当前链路缺少中间状态与闭环控制”。

当前主要缺口：

1. 缺少显式 Workspace 数据结构  
   现在更多是工具调用结果直接进入最终综合，缺少“工作区过滤/保留/状态判定”这一层。
2. 事件证据仍偏 `facts` 粒度  
   你要求“入工作区后是 Episode 内容”，现状需要增加 `search_content` 扩展阶段。
3. 缺少高性能硬过滤层  
   当前 `search_details` 是 dense+BM25+RRF 召回，尚未有 cross-encoder 硬过滤。
4. 缺少三态驱动的回路控制  
   目前无“证据充足/不充分/无效”的显式状态机，补救召回也未标准化。

## 2. 现状映射（和你提案的对应关系）

当前运行主线（代码侧）：

- 问题策略：`_detect_direct_answer_strategy` / `_decompose_question`
- 工具执行：`_answer_directly` / `_solve_sub_questions`
- 最终综合：`_synthesize_final_answer`
- 收尾输出：`_finalize_recall_payload`

与新流程的对应关系：

- Query Understanding：已有雏形（策略判断与分解），但输出不够结构化。
- Initial Action Set Generation：缺失独立动作层，目前由 LLM 即时决定工具调用。
- Action Execution：已有工具执行能力，但缺“事实候选 -> Episode 扩展 -> 入工作区”标准流程。
- Workspace Processing：基本缺失，仅有最终综合，不具备三态判定与缺口分类。

## 3. 重构目标

1. 把链路升级为显式四阶段：理解 -> 动作集 -> 执行 -> Workspace 处理。
2. 在 `facts_only` 下先完成事件层闭环，不阻塞未来实体层接入。
3. 在 MemoryCore `search` 层补齐“多路并行召回接口”（多角度 query 改写 + 并行检索 + 融合）。
4. 用 cross-encoder + topk 完成工作区硬过滤，解决性能瓶颈。
5. 引入三态可答性判定与补救召回策略，避免盲目循环调用工具。
6. 保持现有对外接口兼容（`deep_recall`/`shallow_recall` 返回字段不破坏）。

## 4. 目标运行架构（事件层）

### 4.1 阶段 1：Query Understanding

输出统一 `QueryIntent`：

- `original_question`
- `sub_questions`（默认 0~1，确有收益时才拆）
- `question_type`
- `constraints`（时间锚点、是否需要原话核验等）
- `decomposition_reason`

要求：

- 默认不拆解。
- 串行依赖问题不强拆。
- 为后续动作生成提供约束，不直接触发工具。

### 4.2 阶段 2：Initial Action Set Generation

为每个（子）问题生成初始动作组 `ActionSet`（事件层）：

- `EVENT_DETAIL_RECALL`：`search_details(detail, topk)`
- `EVENT_DETAIL_MULTI_ROUTE_RECALL`：多路 query 改写后并行检索（新增）
- `EVENT_TIME_RECALL`：`search_events_by_time_range(start, end)`（仅时间范围问题）
- `EPISODE_EXPAND`：对候选 `dialogue_id:episode_id` 执行 `search_content`
- `RECALL_REMEDY_MULTI_ROUTE`：补救性多路召回（仅触发一次）

动作集规则：

- 去重、去重复查询、预算受控（动作数/轮数/topk）。
- 不允许生成与上一轮完全重复的动作。
- 事件层默认先 `EVENT_DETAIL_RECALL`，再按证据扩展到 `EPISODE_EXPAND`。
- 对 `INSUFFICIENT/INVALID` 场景优先触发 `EVENT_DETAIL_MULTI_ROUTE_RECALL` 作为补救入口。

### 4.2.1 多路并行召回接口（建议前置实现）

建议在 `src/m_agent/memory/memory_core/workflow/search` 增加多路检索能力：

- 新增：`multi_route_details_search.py`
- 新增 MemoryCore 入口：`search_details_multi_route(detail_query, topk, route_config)`

单次多路召回流程：

1. Query 改写（routes）  
   从多个角度生成改写 query，例如：实体、动作、时间、关系、主题。
2. 并行检索  
   对每个 route 并行调用现有 `search_details`（或其底层逻辑）。
3. 融合去重  
   按 `dialogue_id:episode_id + atomic_fact` 去重，采用 RRF/加权融合 route 分数。
4. 输出  
   返回统一结果 + route 诊断信息（每路 query、命中数、融合分数来源）。

工程要求：

- route 数量受控（默认 3~5），并发受控；
- route 失败不影响全局，支持部分成功；
- 全失败时自动降级为单路 `search_details`。

### 4.3 阶段 3：Action Execution

执行顺序建议：

1. 执行 recall 动作拿候选 facts（高召回，低精度）。
2. 从候选中抽取 `dialogue_id:episode_id`，执行 `search_content` 做 Episode 扩展。
3. 将 Episode 证据写入 Workspace（后续 rerank/过滤都基于 Episode）。

关键点：

- 入工作区后不再以 `facts` 为唯一证据单元，`facts` 仅用于召回入口。
- 对 `search_content` 增加批量化与缓存（避免重复 I/O）。

### 4.4 阶段 4：Workspace Processing

分两段：

1. 过滤与保留（性能优先）  
   - Cross-encoder 批量打分（query/subq × episode evidence）  
   - 仅保留 `score >= hard_threshold` 的候选（宁缺毋滥）  
   - 在通过阈值的候选中按分数排序，最多保留 N 条（`max_keep`）  
   - 若通过阈值后为空，允许 Workspace 为空并进入补救或终止分支
2. 可答性判断与缺口分类（三态）  
   - `SUFFICIENT`：证据充足，直接回答  
   - `INSUFFICIENT`：部分有效，可继续生成非重复动作  
   - `INVALID`：证据无效，无法支持后续动作

补救策略：

- `INSUFFICIENT`：允许进入下一轮动作生成；事件层可先触发一次补救多路召回。
- `INVALID`：若补救多路召回后仍无效，终止并返回证据不足。

## 5. 状态机设计（防无限循环）

建议显式状态机：

`INIT -> UNDERSTAND -> PLAN_ACTIONS -> EXECUTE -> WORKSPACE_JUDGE -> {ANSWER | NEXT_ROUND | REMEDY_ONCE | STOP_INSUFFICIENT}`

约束：

- `max_rounds`（默认 2~3）
- `max_actions_per_round`
- `max_episode_candidates_in_workspace`
- `remedy_recall_max_times = 1`

## 6. 关键数据结构（建议）

```python
class QueryIntent(TypedDict):
    original_question: str
    sub_questions: list[str]
    question_type: str
    decomposition_reason: str
    constraints: dict

class MemoryAction(TypedDict):
    action_id: str
    action_type: str
    query: dict
    source_sub_question_idx: int
    priority: int

class EpisodeEvidence(TypedDict):
    evidence_id: str
    dialogue_id: str
    episode_id: str
    turn_span: list[int] | None
    turns: list[dict]
    source_action_id: str
    recall_score: float | None
    rerank_score: float | None

class WorkspaceState(TypedDict):
    round_id: int
    evidences: list[EpisodeEvidence]
    kept_evidence_ids: list[str]
    status: str  # SUFFICIENT / INSUFFICIENT / INVALID
    gap_type: str | None
```

## 7. 代码落地方案（基于当前仓库最小侵入）

## 7.1 新增模块（建议）

- `src/m_agent/agents/memory_agent/workspace.py`  
  Workspace 容器、去重、状态快照、统计。
- `src/m_agent/agents/memory_agent/action_planner.py`  
  QueryIntent -> ActionSet。
- `src/m_agent/agents/memory_agent/action_executor.py`  
  执行动作、批量 episode 扩展、缓存。
- `src/m_agent/agents/memory_agent/answerability.py`  
  三态判定、缺口分类、补救触发条件。
- `src/m_agent/load_model/reranker_call.py`  
  cross-encoder 批量推理封装。

## 7.2 修改点（现有文件）

- `src/m_agent/agents/memory_agent/mixins/execution.py`  
  将 `deep_recall` 改为“多轮状态机主循环”，替代一次性综合。
- `src/m_agent/agents/memory_agent/mixins/tooling.py`  
  增加 episode 批量扩展工具封装（或内部 helper），保留现有 tracing。
- `src/m_agent/memory/memory_core/memory_system.py`  
  增加批量内容检索入口（建议 `search_contents_by_episode_refs`）。
- `src/m_agent/memory/memory_core/workflow/search/details_search.py`  
  输出候选诊断信息（dense/sparse/fused 分数），便于动作与工作区调度。
- `src/m_agent/memory/memory_core/workflow/search/multi_route_details_search.py`  
  多路 query 改写、并行检索、融合去重。

## 7.3 配置新增（建议）

在 `config/agents/memory/*.yaml` 新增：

- `workspace.max_rounds`
- `workspace.max_episode_candidates`
- `workspace.max_keep`
- `workspace.answerability_model`
- `workspace.remedy_recall_max_times`
- `workspace.cross_encoder.model_name`
- `workspace.cross_encoder.batch_size`
- `workspace.cross_encoder.hard_threshold`

在 `config/memory/core/*.yaml` 新增（多路召回）：

- `detail_search_multi_route.enable`
- `detail_search_multi_route.route_count`
- `detail_search_multi_route.route_types`（entity/action/time/relation/topic）
- `detail_search_multi_route.per_route_topk`
- `detail_search_multi_route.fusion`（rrf/weighted）
- `detail_search_multi_route.max_workers`

## 8. Prompt 与判定策略

新增 runtime prompt（`config/agents/memory/runtime/*.yaml`）：

1. `answerability_judge_prompt`  
   输入：问题 + 保留证据摘要  
   输出：`SUFFICIENT/INSUFFICIENT/INVALID` + `gap_type` + 简短理由
2. `gap_to_action_prompt`  
   输入：`gap_type` + 历史动作  
   输出：下一轮非重复动作建议
3. `final_answer_from_workspace_prompt`  
   输入：最终保留证据  
   输出：`answer/gold_answer/evidence`

建议：  
三态判定尽量“少调用 + 批处理 + 结构化输出”，避免把性能瓶颈从 rerank 转移到 LLM。

## 9. 测试与验收

## 9.1 单测新增

- QueryIntent 不拆/拆解边界测试
- Action 去重与预算控制测试
- Workspace 三态判定测试
- 补救召回最多一次测试
- cross-encoder 硬过滤阈值与 topk 测试

## 9.2 集成测试新增

- `facts_only` 模式下全链路回归
- 证据从 `facts` 扩展为 Episode 后的答案稳定性
- 失败路径：补救后仍无效时正确终止

## 9.3 评估指标（建议）

- Answer 质量：F1 / B1 / LLMJudge
- 证据有效性：Episode recall / Evidence coverage
- 性能：P50/P95 延迟、每轮工具调用数、Workspace 候选规模

## 10. 分阶段实施计划

### Phase A（低风险，先搭框架）

1. 引入 `QueryIntent`、`ActionSet`、`Workspace` 数据结构。
2. 先完成 MemoryCore 多路并行召回接口（query 改写 + 并行检索 + 融合）。
3. 主流程接入状态机，但先用规则版 Workspace 判定（无 cross-encoder）。
4. 保持现有输出字段兼容。

### Phase B（核心能力）

1. 接入 cross-encoder 批量硬过滤 + topk。
2. 完成 “facts 候选 -> Episode 扩展 -> Workspace” 闭环。
3. 上线三态判定与一次性补救召回（补救优先走多路并行召回）。

### Phase C（优化与稳态）

1. `search_content` 批量化、缓存化。
2. 调优阈值与动作预算。
3. LoCoMo 回归与线上压测。

## 11. 工程实现计划

### 11.1 迭代节奏（建议 3 个迭代）

1. Iteration 1（1 周）：多路并行召回接口 + 状态机骨架  
   目标：先补 `workflow/search` 多路召回能力，再把主流程改造成可循环的 4 阶段流程。
2. Iteration 2（1 周）：硬过滤与 Episode 化证据闭环  
   目标：接入 cross-encoder 批量打分，并完成 facts -> episode -> workspace 的证据流。
3. Iteration 3（1 周）：稳定性、评估与灰度  
   目标：补齐性能优化、自动化评测、灰度发布与回滚预案。

### 11.2 任务拆解（按文件/模块）

1. 主流程重构  
   文件：`src/m_agent/agents/memory_agent/mixins/execution.py`  
   任务：引入状态机主循环、轮次预算、补救召回触发点。  
   验收：`deep_recall` 能输出三态与轮次轨迹，不出现无限循环。
2. 多路并行召回接口（前置）  
   文件：`src/m_agent/memory/memory_core/workflow/search/multi_route_details_search.py`、`src/m_agent/memory/memory_core/memory_system.py`  
   任务：实现 query 多路改写、并行检索、融合去重、统一返回格式。  
   验收：开启配置后可稳定返回多路融合结果；关闭配置时退化为单路。
3. Workspace 与动作层落地  
   文件：`src/m_agent/agents/memory_agent/workspace.py`、`action_planner.py`、`action_executor.py`、`answerability.py`  
   任务：实现 QueryIntent/ActionSet/WorkspaceState 结构与处理逻辑。  
   验收：可从工具结果稳定构建 Workspace，并给出 `SUFFICIENT/INSUFFICIENT/INVALID`。
4. Episode 证据扩展  
   文件：`src/m_agent/memory/memory_core/memory_system.py`、`workflow/search/content_search.py`  
   任务：增加批量 episode 内容获取能力，减少重复 I/O。  
   验收：同一轮中重复 episode 查询命中缓存，P95 延迟下降。
5. Cross-encoder 硬过滤  
   文件：`src/m_agent/load_model/reranker_call.py`、`details_search.py`、agent 配置  
   任务：批量打分、阈值过滤、`max_keep` 截断。  
   验收：严格遵守“不过阈值不保留”，并可通过配置调参。
6. Prompt 与输出契约  
   文件：`config/agents/memory/runtime/*.yaml`  
   任务：新增 answerability/gap/final synthesis prompt。  
   验收：保留现有外部字段兼容，新增字段不破坏 chat 调用方。
7. 测试与评估  
   文件：`tests/test_memory_agent_*`、`tests/test_details_search_hybrid.py`、`scripts/run_locomo/run_eval_locomo.py`  
   任务：单测 + 集成测试 + LoCoMo 回归脚本。  
   验收：关键路径测试全绿，LoCoMo 指标无明显回退。

### 11.3 里程碑与准入门槛

1. M1（状态机可运行）  
   准入：多路召回接口可用；基础回归通过；`max_rounds` 生效；异常路径可终止。
2. M2（硬过滤可运行）  
   准入：`hard_threshold + max_keep` 生效；Workspace 允许空集；补救分支可触发。
3. M3（灰度可发布）  
   准入：P95 延迟在预算内；LoCoMo F1/B1/Recall 无显著退化；回滚脚本验证通过。

### 11.4 灰度发布与回滚

1. 灰度开关  
   增加 `workspace.enable_state_machine`、`workspace.enable_cross_encoder` 双开关，支持逐步放量。
2. 灰度顺序  
   先离线回放 -> 小流量在线 -> 全量。每一步都记录三态分布和空 Workspace 比例。
3. 回滚策略  
   任一关键指标异常时，先关闭 `enable_cross_encoder`，必要时退回旧 `deep_recall` 路径。

### 11.5 人力与工时估算（建议）

1. 核心开发：1 人 × 2~3 周  
2. 测试与评估：1 人 × 1 周（可并行）  
3. 灰度与运维支持：0.5 人 × 1 周

## 12. 风险与回退

1. 风险：cross-encoder 过严导致召回塌陷  
   回退：优先触发补救多路召回；必要时小步下调阈值，但不设置最少保留下限。
2. 风险：Episode 扩展导致时延升高  
   回退：限制扩展候选上限 + 批量查询 + 缓存。
3. 风险：状态机循环过多  
   回退：`max_rounds` + `remedy_recall_max_times=1` 强约束。
4. 风险：与现有 ChatController 契约不兼容  
   回退：保留 `answer/gold_answer/evidence/tool_calls/question_plan/sub_questions` 字段。

## 13. 对“当前现状”的最终建议

在你现在“事实构建链路已切断、仅事件 recall 可用”的阶段，最优先做三件事：

1. 先补齐多路并行召回接口（在 `workflow/search` 做 query 多角度改写 + 并行检索 + 融合）。
2. 再把 Workspace 三态闭环立起来（即使先不用 cross-encoder，也要先有状态机和补救回路）。
3. 最后接入 cross-encoder 硬过滤，把性能瓶颈从 LLM 判别转为批量模型判分。

这样可以在不依赖实体层的前提下，把 MemoryAgent 从“工具调用驱动”升级为“证据状态驱动”，并且和你后续的实体层扩展天然兼容。
