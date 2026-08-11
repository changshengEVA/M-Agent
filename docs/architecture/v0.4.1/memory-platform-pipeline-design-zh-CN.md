# v0.4.1 记忆平台 Pipeline 方案

## 1. 约束

- 输入是已经渲染完成的 Scene 描述文本。
- `observation_*`、`thinking`、`tool_use_*` 具有相同录入地位。
- `item_type` 只用于渲染和溯源，不参与抽取、冲突裁决或排序权重。
- 安全审核独立于记忆处理。
- 原始 Scene 是唯一事实源，派生信息只用于检索和组织。
- 不使用图数据库。

```text
Scene
  -> SourceScene 原始证据
  -> MemoryAtom 原子记忆
  -> Exact + BM25 + Dense ANN
       -> DeepRecall：证据闭包与重建
       -> ShallowRecall：刺激原子激活与过滤
```

## 2. 内部数据结构

### 2.1 SourceScene

```text
thread_id / conv_id / scene_id
items[] / rendered_text
item_spans[]
occurred_at / scene_digest
```

`item_spans` 记录每个 `item_id` 在 `rendered_text` 中的字符范围。

### 2.2 MemoryAtom

```json
{
  "memory_id": "memory-001",
  "version": 1,
  "state": "active",
  "fact_text": "LoCoMo 数据导入后，日期仍然偏移一天。",
  "referent": "LoCoMo 数据",
  "predicate": "导入后日期存在偏移",
  "object_or_value": "偏移一天",
  "polarity": "positive",
  "modality": "certain",
  "temporal_qualifier": "仍然",
  "anchors": ["LoCoMo", "日期", "偏移一天"],
  "occurred_from": null,
  "occurred_to": null,
  "source_spans": [
    {
      "conv_id": "conv-001",
      "scene_id": "scene-003",
      "item_id": "item-004",
      "char_start": 12,
      "char_end": 31
    }
  ]
}
```

- `fact_text` 是自包含的最小事实、事件或状态描述。
- 每个字段必须能由 `source_spans` 支持。
- 不生成原文没有表达的意图、目标或未来行动。
- 一个 Scene 可以生成零到多个 `MemoryAtom`，但 `SourceScene` 始终保留。
- `state` 取值为 `active`、`superseded` 或 `disputed`。

## 3. Memory_build

```text
请求校验与安全审核
  -> 按 item_seq 渲染并保存 SourceScene
  -> 形成抽取窗口
  -> 抽取 MemoryAtom
  -> 与已有记忆对齐
  -> 写入索引并原子发布
  -> 返回 build_id、status、memory_refs
```

### 3.1 抽取

- Scene 不超过 600 tokens 时整体抽取。
- 超过 600 tokens 时，在 item 或句子边界形成 300～600 token 窗口。
- 相邻窗口重叠一个 item。
- 每个原子抽取 `referent`、`predicate`、`object_or_value`、否定/模态/时间修饰、anchors 和 source spans。
- 不根据 `item_type` 使用不同抽取规则。

### 3.2 对齐

新原子从同一 `thread_id` 召回最多 8 个相近旧原子，批量判定：

- `ADD`：新增。
- `MERGE_SUPPORT`：语义相同，增加来源。
- `SUPERSEDE`：原文明示更新、纠正或状态变化。
- `DISPUTE`：内容互斥且无法确定覆盖关系。
- `NOOP`：幂等重放或完全重复。

旧版本不物理删除。

### 3.3 索引

`MemoryAtom` 建立：

- Exact：名称、ID、版本号、数字、日期等 anchors。
- BM25：`fact_text + anchors`。
- Dense ANN：`embed(fact_text)`。
- Metadata：`thread_id`、`conv_id`、时间、版本和状态。

`SourceScene` 额外建立 BM25 和 Dense ANN 索引，只供 DeepRecall 回退检索。

## 4. 共享候选召回函数

```python
def batch_hybrid_retrieve(
    *,
    scope: RecallScope,
    probes: list[RetrievalProbe],
    corpus: Literal["memory", "source"],
    routes: set[Literal["exact", "lexical", "dense"]],
    budget: RetrievalBudget,
    exclude_refs: set[SourceRef],
    deadline_ms: int,
) -> RetrievalBatch:
    ...
```

候选返回：

```text
document_id / memory_id
matched_probe_ids[] / matched_fields[]
route_ranks{} / source_refs[]
index_revision
```

处理规则：

- `thread_id`、显式时间范围、版本和状态在检索前过滤。
- 同一 probe 的不同检索路用 RRF 融合，初始 `rank_constant = 60`。
- 不同 probe 保留独立排名，按轮询配额合并和去重。
- 不直接比较 BM25、cosine 或 RRF 的绝对分数。
- DeepRecall 与 ShallowRecall 共享该函数，不共享 probe 生成、最终过滤和输出生成。

## 5. Deep_recall

```text
query + filters
  -> EvidencePlan
  -> 批量混合召回
  -> CrossEncoder 精排
  -> 回查 SourceScene
  -> EvidenceLedger
  -> 证据闭包检查
       -> 闭合：重建
       -> 缺口：一次定向补查
  -> 逐主张验引
  -> success / partial / insufficient / failed
```

### 5.1 EvidencePlan 与召回

- 最多生成 4 个 evidence needs。
- 原始 query 始终保留为全局 probe。
- 无依赖的 needs 并行检索。
- 显式 `filters` 是硬过滤；推断出的模糊时间只作为检索信号。

每个 need 执行：

```text
BM25 top 32 + Dense ANN top 32 -> RRF
```

- 全部 needs 的候选 union 最多 96 条。
- CrossEncoder 批量精排前 32 条。
- 展开最高的 12～16 条候选对应的原始 Scene/items。

### 5.2 EvidenceLedger 与闭包

每个 need 记录：

```text
coverage: covered | missing | conflicted
stance: supports | contradicts | context_only | irrelevant
evidence[] / normalized_claim / event_time
```

证据选择器只返回 `item_id + char_start + char_end`，平台负责回读原文。

闭合条件：

- 每个 required need 至少有一条原始证据支持。
- 多步关系中的桥接信息有证据。
- 时间结论有时间依据。
- 所有待输出事实都有来源。
- 不存在会改变答案的未解决冲突。

未闭合时只将具体 gap 转成补查 probe。初版最多补查一次，总检索轮数为 2；补查可同时检索 `memory` 和 `source` corpus。

### 5.3 重建

重建器先生成 `claim + evidence_ids`，随后逐项检查：

- evidence 和 excerpt 是否真实存在。
- evidence 是否支持 claim。
- 事实性 claim 是否全部带引用。
- claim 是否超出证据范围。

不受支持的 claim 删除或转为 gap。最终 evidence 只返回重建文本实际引用的原始材料。

## 6. Shallow_recall

```text
scene_items[-1]
  -> 指代消解与保真原子化
  -> Exact、BM25、Dense 三路并检
  -> 候选合并与硬过滤
  -> 原子—记忆关系判断
  -> 去重和限额
  -> 0～2 hints 或 empty
```

### 6.1 上下文边界

`scene_items[0:-1]` 只用于：

- 消解代词、别名和省略主语。
- 补全具有唯一指向的对象。
- 标准化相对时间。
- 判断候选是否已在当前 Scene 中出现。

不得从上下文推导意图、目标或未来行动作为 probe，也不得把全部上下文重新加入检索。

### 6.2 刺激原子化

```json
{
  "anchors": ["LoCoMo"],
  "compound_terms": [
    "LoCoMo 日期",
    "导入 日期",
    "日期 偏移一天"
  ],
  "atoms": [
    {
      "referent": "LoCoMo 数据",
      "predicate": "导入后日期存在偏移",
      "object_or_value": "偏移一天",
      "polarity": "positive",
      "modality": "certain",
      "temporal_qualifier": "仍然"
    }
  ]
}
```

关键词或片段满足以下任一条件时保留：

- 是名称、ID、版本、日期、数字等精确锚点。
- 删除后会改变原子命题的含义或真假。
- 与其他片段组合后能形成有区分度的检索项。

限制：

- anchors 最多 8 个，compound terms 最多 12 个，atoms 最多 4 条。
- 宽泛词不得单独形成 probe。
- 否定、时间、数量、持续等修饰信息附着到对应 atom，不单独检索。

### 6.3 三路并检

1. anchors 查询 Exact 索引。
2. compound terms 查询 BM25。
3. atoms 渲染为自包含短句，批量 embedding 后查询 Dense ANN。

全部 probe 批量提交，三类索引并行查询。每个 probe 保留独立排名和匹配轨迹，不先拼成一个大查询。

### 6.4 过滤与输出

硬过滤：

- 排除当前 Scene 自身来源和当前 Scene 已表达的同义内容。
- 排除已废止、时间不兼容或没有原始 source 的候选。
- 未解决冲突不直接生成 hint。

剩余候选批量判断原子关系：

- `same_fact`：同一事实或事件。
- `historical_state`：同一对象或属性的历史状态。
- `update_or_conflict`：明确更新、持续、修正或冲突。
- `cause_or_result`：原文明示的原因或结果。
- `reference_context`：补全刺激中的明确指代。
- `none`：只有宽泛话题或实体重合。

保留条件：

```text
relation != none
AND 提供当前 Scene 中没有的历史信息
AND 时间与版本关系可解释
AND 具有原始 source
```

最终最多返回 2 条不同关系的 hint，总文本建议不超过 120 tokens；每条 hint 必须带 `source_refs`。没有候选通过时返回 `empty`。

## 7. 初始预算与性能

| 项目 | ShallowRecall | DeepRecall |
| --- | ---: | ---: |
| probe 上限 | 8 anchors + 12 compounds + 4 atoms | 4 evidence needs |
| 每检索路 top-k | 16～24 | 32 |
| union 上限 | 64 | 96 |
| 关系判断/精排输入 | 16 | CrossEncoder 32 |
| 原始证据展开 | 最终候选 | 12～16 个候选 Scene |
| 检索轮数 | 1 | 2，含一次补查 |
| 输出 | 0～2 hints | reconstruction |

性能规则：

- 在 `Memory_build` 阶段预计算 MemoryAtom、embedding 和倒排索引。
- 一个请求内的全部 atoms 使用一次 batch embedding。
- Exact、BM25 和 ANN 并行查询。
- `thread_id`、时间、版本和状态使用索引前过滤。
- 候选截断后再批量读取完整 source。
- 缓存键包含 `thread_memory_revision`、probe hash、filters 和模型版本。
- ShallowRecall 在无有效 atom、无候选、硬过滤为空或关系判断全拒绝时提前返回 `empty`。
