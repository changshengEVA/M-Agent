# v0.4.1 记忆平台接口设计

> 状态：草案。当前记录 `Memory_build`、`Shallow_recall` 与 `Deep_recall` 接口，其他接口后续补充。

v0.4.1 先独立搭建记忆平台，不接入 Runtime 主链。这里先定义未来 Flush 可使用的构建接口，以及深召回、浅召回的独立平台接口。一次 `Memory_build` 请求提交一个 Scene，`items` 是该 Scene 内按顺序排列的内容。

召回分工：

| 接口 | 调用方 | 目标 | 返回形态 |
| --- | --- | --- | --- |
| `Shallow_recall` | 系统调用方（后续版本接入 Runtime） | 从当前刺激发散联想，再严格限制输出 | 少量带溯源的线索，或空结果 |
| `Deep_recall` | 模型（工具召回） | 高准确地重建具体场景 | 带证据溯源的重建文本 |

## Memory_build

调用方只提交原始 Scene。记忆平台在构建过程中自行判断应当新增、合并、修正、废止已有记忆，或者不形成记忆；调用方不通过 `build_mode` 指定这些处理结果。

### 构建请求

| 信息名称 | 含义 | 用处 |
| --- | --- | --- |
| `schema_version` | 构建请求的接口格式版本 | 支持接口后续演进与兼容解析 |
| `thread_id` | 智能体的唯一标识 | 确定记忆归属，并作为不同用户之间的隔离边界 |
| `conv_id` | 该智能体下的一次具体对话标识 | 确定本次构建对应哪段对话；同一 `thread_id` 下可以有多个 `conv_id` |
| `scene_id` | 当前 Scene 在该对话中的唯一标识 | 幂等构建、去重，以及从记忆追溯到原始 Scene |
| `items` | 当前 Scene 中按实际顺序排列的内容 | 作为记忆构建的主要输入 |
| `memory_proposals` | 预留的记忆建议 | 当前先不在方法内实现，仅保留接口位置，后续再确定处理语义 |

`build_id` 和输入内容摘要由记忆平台内部生成，不要求调用方提供。

历史回填、全量重建等管理操作后续单独定义，不放入普通 `Memory_build` 请求。

### Scene 中的 `item`

一条 `item` 表示 Scene 中的一条内容，而不是一个完整 Scene。

| 信息名称 | 必填 | 含义 | 用处 |
| --- | --- | --- | --- |
| `item_id` | 是 | 该内容在当前 Scene 中的唯一标识 | 去重，并从记忆追溯到具体原始内容 |
| `item_seq` | 是 | 该内容在 Scene 中的顺序 | 按真实交互和执行顺序还原 Scene |
| `item_type` | 是 | 该条内容的类型 | 标识内容性质，并决定最终记录文本的渲染标签 |
| `content` | 是 | 该条内容的原始文本 | 作为记忆提取的实际材料 |
| `occurred_at` | 否 | 该条内容实际产生的时间 | 需要精确时间线时使用；未提供时依赖 `item_seq` |

`item_type` 使用以下命名形式：

| `item_type` | 含义 |
| --- | --- |
| `observation_[Stimulus_type]` | 一条外部刺激或观察内容；`[Stimulus_type]` 标明刺激类型 |
| `thinking` | Thinking、reasoning 等内部思考内容 |
| `tool_use_[Tool_name]` | 一条工具调用内容；`[Tool_name]` 标明被调用的工具 |

示例：

```json
{
  "item_id": "item-003",
  "item_seq": 3,
  "item_type": "tool_use_search_memory",
  "content": "调用 search_memory，查询用户之前确定的数据库方案",
  "occurred_at": "2026-08-09T14:31:12+08:00"
}
```

记忆平台按 `item_seq` 组合各条内容，形成完整的 Scene 记录文本。`transaction_id`、`actor`、`related_item_ids` 当前不进入 `item` 接口；内容类型和执行关系分别由 `item_type` 与 `item_seq` 表达。

### 构建返回

| 信息名称 | 必填 | 含义 | 用处 |
| --- | --- | --- | --- |
| `build_id` | 是 | 记忆平台生成的本次构建唯一标识 | 查询内部构建记录和排查错误 |
| `status` | 是 | 构建状态，当前为 `success` 或 `failed` | 判断平台是否完成了对当前 Scene 的处理 |
| `memory_refs` | 是 | 本次构建涉及的记忆标识列表 | 后续定位或维护相关记忆；平台判断无需形成记忆时可以为空 |
| `error_code` | 否 | 构建失败时的错误码 | 供调用方判断错误类型 |
| `error_message` | 否 | 构建失败时的错误说明 | 供日志记录和问题排查 |

```json
{
  "build_id": "build-20260809-001",
  "status": "success",
  "memory_refs": [
    "memory-00017",
    "memory-00023"
  ]
}
```

构建结果不返回单一 `effect`。一个 Scene 可能同时引起多条记忆的新增、合并或修正，具体变化记录在 `build_id` 对应的平台内部构建日志中。

## Shallow_recall

`Shallow_recall` 是只读的联想接口，不接收明确查询问题，也不负责重建某个指定事务。平台从当前刺激中提取多个联想入口，宽覆盖发现候选，再通过严格限制选择极少量有当前价值的历史线索；没有合适线索时正常返回空结果。

```text
当前刺激 -> 多入口联想激活 -> 宽覆盖候选 -> 严格选择 -> 少量线索或空结果
```

### 召回请求

| 信息名称 | 必填 | 含义 | 用处 |
| --- | --- | --- | --- |
| `schema_version` | 是 | 接口格式版本 | 支持接口后续演进与兼容解析 |
| `thread_id` | 是 | 智能体的唯一标识 | 确定记忆归属和允许检索的隔离范围 |
| `conv_id` | 是 | 当前对话标识 | 标明本次浅召回发生在哪次对话中，不作为记忆搜索边界 |
| `scene_id` | 是 | 当前 Scene 标识 | 标明本次浅召回所在的 Scene |
| `scene_items` | 是 | 当前 Scene 截至本次刺激为止的全部内容 | 同时提供当前刺激及其发生时的情景上下文 |

`scene_items` 复用 `Memory_build` 已定义的 item 格式，并按 `item_seq` 排列。最后一条 item 是本次新出现的当前刺激，之前的 items 是刺激发生时已有的情景上下文：

```text
stimulus = scene_items[-1]
context  = scene_items[0:-1]
```

当前刺激负责激活联想；之前的情景内容用于判断候选是否适合当前场景、是否与已知内容重复，以及是否具有额外价值。平台不会把全部 `scene_items` 拼接成一个精准查询。

```json
{
  "schema_version": "0.4.1",
  "thread_id": "thread-001",
  "conv_id": "conv-012",
  "scene_id": "scene-003",
  "scene_items": [
    {
      "item_id": "item-001",
      "item_seq": 1,
      "item_type": "observation_user",
      "content": "帮我查询上海的天气"
    },
    {
      "item_id": "item-002",
      "item_seq": 2,
      "item_type": "thinking",
      "content": "需要先确定查询日期"
    },
    {
      "item_id": "item-003",
      "item_seq": 3,
      "item_type": "tool_use_weather",
      "content": "查询上海未来七天天气"
    },
    {
      "item_id": "item-004",
      "item_seq": 4,
      "item_type": "observation_tool_feedback",
      "content": "上海下周预计持续降雨"
    }
  ]
}
```

在该示例中，`item-004` 是当前刺激，`item-001` 至 `item-003` 是当前情景上下文。

### 召回返回

| 信息名称 | 必填 | 含义 | 用处 |
| --- | --- | --- | --- |
| `recall_id` | 是 | 本次浅召回的唯一标识 | 查询召回记录和排查问题 |
| `status` | 是 | `hit`、`empty` 或 `failed` | 表示是否选出了值得提供的联想线索 |
| `hints` | 是 | 通过严格限制后留下的历史线索 | 作为辅助上下文提供给调用方 |
| `error_code` | 否 | 技术失败时的错误码 | 供调用方判断错误类型 |
| `error_message` | 否 | 技术失败时的错误说明 | 供日志记录和问题排查 |

`status` 的含义：

| 状态 | 含义 |
| --- | --- |
| `hit` | 找到并选出了至少一条有当前价值的联想线索 |
| `empty` | 没有线索通过选择条件，属于正常结果 |
| `failed` | 召回过程发生技术错误 |

每条 `hint` 包含：

| 信息名称 | 含义 |
| --- | --- |
| `hint_id` | 本次返回中的线索编号 |
| `text` | 紧凑、带归属表述的历史线索 |
| `source_refs` | 支撑该线索的原始 Scene 内容位置 |

每条 `source_ref` 包含：

| 信息名称 | 含义 |
| --- | --- |
| `source_ref_id` | 该来源在本次返回中的引用编号 |
| `conv_id` | 原始内容所属对话 |
| `scene_id` | 原始内容所属 Scene |
| `item_id` | 对应的具体 Scene 内容 |
| `item_type` | 原始内容类型 |
| `occurred_at` | 原始内容产生时间 |

```json
{
  "recall_id": "recall-20260810-001",
  "status": "hit",
  "hints": [
    {
      "hint_id": "H1",
      "text": "用户此前在旅行规划中表示不喜欢早班交通。[S1]",
      "source_refs": [
        {
          "source_ref_id": "S1",
          "conv_id": "conv-008",
          "scene_id": "scene-021",
          "item_id": "item-004",
          "item_type": "observation_user",
          "occurred_at": "2026-08-08T16:20:00+08:00"
        }
      ]
    }
  ]
}
```

没有值得输出的联想线索时，接口仍返回响应，以区分正常弃权和技术失败：

```json
{
  "recall_id": "recall-20260810-002",
  "status": "empty",
  "hints": []
}
```

请求不提供显式 `query`、定向来源过滤、`top_k` 或排序阈值。这些参数会把浅召回推向定向检索，联想激活和严格选择策略由平台内部控制。

## Deep_recall

`Deep_recall` 是只读接口。调用方提交一个明确、可独立理解的召回问题，记忆平台返回带原始 Scene 溯源的重建结果，不在召回过程中修改记忆。

### 召回请求

| 信息名称 | 必填 | 含义 | 用处 |
| --- | --- | --- | --- |
| `schema_version` | 是 | 接口格式版本 | 支持接口后续演进与兼容解析 |
| `thread_id` | 是 | 智能体的唯一标识 | 确定记忆归属和允许检索的隔离范围 |
| `query` | 是 | 完整、可独立理解的召回问题 | 描述需要重建的信息 |
| `filters` | 否 | 对原始材料来源范围的明确限制 | 限定对话或发生时间；未提供时检索整个 `thread_id` 下的记忆 |

`filters` 当前包含：

| 信息名称 | 含义 |
| --- | --- |
| `conv_ids` | 只召回指定对话中的记忆 |
| `occurred_from` | 只使用该时间之后发生的原始材料 |
| `occurred_to` | 只使用该时间之前发生的原始材料 |

`conv_id` 不作为必填字段。`Deep_recall` 默认跨当前 `thread_id` 下的全部对话召回；只有调用方明确提供 `filters.conv_ids` 时才限制对话范围。

```json
{
  "schema_version": "0.4.1",
  "thread_id": "thread-001",
  "query": "我们之前为什么决定不在 v0.4.1 接入 Runtime 主链？",
  "filters": {
    "occurred_from": "2026-07-01T00:00:00+08:00"
  }
}
```

### 召回返回

| 信息名称 | 必填 | 含义 | 用处 |
| --- | --- | --- | --- |
| `recall_id` | 是 | 本次深召回的唯一标识 | 查询召回记录和排查问题 |
| `status` | 是 | `success`、`partial`、`insufficient` 或 `failed` | 表示证据是否足以完成重建，或召回是否发生技术错误 |
| `reconstruction` | 是 | 基于证据形成的重建文本；证据不足或技术失败时为 `null` | 直接提供给调用方使用 |
| `evidence` | 是 | 本次重建实际引用的原始材料 | 支撑重建内容并提供溯源 |
| `conflicts` | 是 | 召回中发现的互相冲突的信息 | 防止平台擅自选择其中一个版本 |
| `gaps` | 是 | 尚未找到足够证据的信息缺口 | 明确哪些部分无法可靠回答 |
| `error_code` | 否 | 技术失败时的错误码 | 供调用方判断错误类型 |
| `error_message` | 否 | 技术失败时的错误说明 | 供日志记录和问题排查 |

`status` 的含义：

| 状态 | 含义 |
| --- | --- |
| `success` | 已找到足够证据并形成重建 |
| `partial` | 只能重建部分内容，仍存在明确缺口 |
| `insufficient` | 没有足够证据形成可靠重建 |
| `failed` | 召回过程发生技术错误 |

每条 `evidence` 包含：

| 信息名称 | 含义 |
| --- | --- |
| `evidence_id` | 本次返回中的引用编号，例如 `E1` |
| `conv_id` | 原始材料所属对话 |
| `scene_id` | 原始材料所属 Scene |
| `item_id` | 对应的具体 Scene 内容 |
| `item_type` | 原始内容类型 |
| `occurred_at` | 原始内容产生时间 |
| `excerpt` | 实际用于支撑重建的原文片段 |

```json
{
  "recall_id": "recall-20260809-001",
  "status": "success",
  "reconstruction": "此前决定 v0.4.1 只建设独立记忆平台，不接入 Runtime 主链；Runtime 接入留到后续版本。[E1]",
  "evidence": [
    {
      "evidence_id": "E1",
      "conv_id": "conv-008",
      "scene_id": "scene-021",
      "item_id": "item-004",
      "item_type": "observation_user",
      "occurred_at": "2026-08-08T16:20:00+08:00",
      "excerpt": "现在不要录入到 Runtime 链路，先搭系统。"
    }
  ],
  "conflicts": [],
  "gaps": []
}
```

请求暂不暴露 `top_k`、扩展跳数、证据需求分解或排序分数，这些属于 `Deep_recall` 的内部实现策略。返回也暂不提供没有校准依据的 `confidence`。

## 实体图在召回中的定位

实体图不单独定义 `Entity_recall` 召回链路，而是作为记忆平台内部的关联索引：由 `Memory_build` 派生并维护实体及关系，在 `Shallow_recall` 中根据当前刺激识别实体、沿有限关系激活相关实体与历史情景，再将结果放入普通候选池并接受统一的严格筛选，最终仍只返回少量 `hints` 或空结果。实体节点和关系不能直接作为独立证据，也不能把完整实体档案直接注入上下文；每条候选都必须能够追溯到原始 `scene_id/item_id`。针对实体的显式问题仍由 `Deep_recall` 处理，后续可在其内部复用实体图进行证据定位和扩展，而无需增加新的公开召回协议。
