# Memory-Agent 可视化 API 文档

更新时间: 2026-04-12

## 1. 目标
该 API 用于给前端提供 Memory-Agent 的可视化能力，重点支持:

- 实时查看工具调用过程（开始、完成、失败、参数、结果）
- 实时查看 workspace 状态变化（每轮开始、每轮判定、最终状态）
- 查询一次运行（run）的最终结果、工具调用汇总、workspace 汇总

## 2. 启动方式

首次在本地环境运行前，先在仓库根目录执行：

```bash
python -m pip install -e .
```

然后启动服务：

```bash
python -m m_agent.agents.memory_agent.visual_api --host 0.0.0.0 --port 8092 --config config/agents/memory/locomo_eval_memory_agent.yaml
```

说明:

- `--config` 是默认的 Memory-Agent 配置路径
- 请求里也可以按 run 覆盖 `config_path`
- 绑定 `0.0.0.0` 时，浏览器请访问 **`http://127.0.0.1:8092`**（或 `localhost`），不要使用 `http://0.0.0.0:8092`（常见报错 `ERR_ADDRESS_INVALID`）。启动时终端会打印可用的浏览器地址。

## 3. 返回数据约定

所有事件流（SSE）里的单条事件统一结构:

```json
{
  "run_id": "memory_run_xxx",
  "seq": 12,
  "timestamp": "2026-04-12T09:30:21.123Z",
  "type": "tool_call",
  "payload": {}
}
```

`type` 常见值:

- `run_started`
- `plan_update`
- `sub_question_started`
- `sub_question_completed`
- `tool_call`
- `tool_result`
- `workspace_state`
- `direct_answer_payload`
- `direct_answer_fallback`
- `final_answer_payload`
- `run_completed`
- `run_failed`

## 4. 接口列表

## 4.1 健康检查
### `GET /healthz` 或 `GET /`
作用: 检查服务是否可用，并返回默认配置路径。

返回示例:

```json
{
  "status": "ok",
  "service": "memory-agent-visual-api",
  "default_config_path": "F:\\AI\\M-Agent\\config\\agents\\memory\\locomo_eval_memory_agent.yaml"
}
```

## 4.2 查询默认配置
### `GET /v1/memory-agent/config`
作用: 给前端展示当前后端默认使用的配置文件路径。

返回示例:

```json
{
  "default_config_path": "F:\\AI\\M-Agent\\config\\agents\\memory\\locomo_eval_memory_agent.yaml"
}
```

## 4.3 创建一次可视化运行
### `POST /v1/memory-agent/runs`
作用: 提交一个问题，异步启动 Memory-Agent 执行，返回 `run_id` 和相关查询/流地址。

请求体:

```json
{
  "question": "What are Emi's hobbies?",
  "recall_mode": "deep",
  "config_path": "config/agents/memory/locomo_eval_memory_agent.yaml",
  "thread_id": "ui-thread-001"
}
```

字段说明:

- `question` 必填，用户问题
- `recall_mode` 可选，`deep` 或 `shallow`，默认 `deep`
- `config_path` 可选，本次 run 覆盖默认配置
- `thread_id` 可选，透传给 agent

返回示例:

```json
{
  "run_id": "memory_run_4f1d8d0b0f3b4c8f9b4db3b7f7d6f3c1",
  "status": "queued",
  "question": "What are Emi's hobbies?",
  "recall_mode": "deep",
  "config_path": "F:\\AI\\M-Agent\\config\\agents\\memory\\locomo_eval_memory_agent.yaml",
  "thread_id": "ui-thread-001",
  "created_at": "2026-04-12T09:31:10.421Z",
  "result_url": "/v1/memory-agent/runs/memory_run_4f1d8d0b0f3b4c8f9b4db3b7f7d6f3c1",
  "events_url": "/v1/memory-agent/runs/memory_run_4f1d8d0b0f3b4c8f9b4db3b7f7d6f3c1/events",
  "tool_calls_url": "/v1/memory-agent/runs/memory_run_4f1d8d0b0f3b4c8f9b4db3b7f7d6f3c1/tool-calls",
  "workspace_url": "/v1/memory-agent/runs/memory_run_4f1d8d0b0f3b4c8f9b4db3b7f7d6f3c1/workspace"
}
```

## 4.4 列出最近运行
### `GET /v1/memory-agent/runs?limit=20`
作用: 给前端列表页展示最近 run 的状态和摘要。

返回示例:

```json
{
  "count": 2,
  "runs": [
    {
      "run_id": "memory_run_xxx",
      "status": "completed",
      "question": "What are Emi's hobbies?",
      "recall_mode": "deep",
      "config_path": "F:\\AI\\M-Agent\\config\\agents\\memory\\locomo_eval_memory_agent.yaml",
      "thread_id": "ui-thread-001",
      "created_at": "2026-04-12T09:31:10.421Z",
      "started_at": "2026-04-12T09:31:10.530Z",
      "finished_at": "2026-04-12T09:31:13.022Z",
      "event_count": 48,
      "tool_call_count": 7,
      "workspace_event_count": 5,
      "latest_workspace": {},
      "result": {},
      "error": null
    }
  ]
}
```

## 4.5 查询单次运行详情
### `GET /v1/memory-agent/runs/{run_id}`
作用: 获取该 run 的整体状态、最终结果、工具数、workspace 最新快照。

返回示例:

```json
{
  "run_id": "memory_run_xxx",
  "status": "completed",
  "question": "What are Emi's hobbies?",
  "recall_mode": "deep",
  "config_path": "F:\\AI\\M-Agent\\config\\agents\\memory\\locomo_eval_memory_agent.yaml",
  "thread_id": "ui-thread-001",
  "created_at": "2026-04-12T09:31:10.421Z",
  "started_at": "2026-04-12T09:31:10.530Z",
  "finished_at": "2026-04-12T09:31:13.022Z",
  "event_count": 48,
  "tool_call_count": 7,
  "workspace_event_count": 5,
  "latest_workspace": {
    "phase": "finalized",
    "status": "SUFFICIENT",
    "gap_type": null,
    "workspace": {
      "round_id": 2,
      "evidences": [],
      "kept_evidence_ids": [],
      "status": "SUFFICIENT",
      "gap_type": null
    }
  },
  "result": {
    "answer": "...",
    "gold_answer": "...",
    "evidence": "...",
    "tool_call_count": 7
  },
  "error": null
}
```

## 4.6 实时事件流
### `GET /v1/memory-agent/runs/{run_id}/events?after_seq=0`
作用: SSE 实时订阅该 run 的执行事件，用于可视化时间线与实时面板。

说明:

- Content-Type: `text/event-stream`
- 支持 `after_seq` 断点续传
- 当 run 已结束且没有新事件时，接口会返回 `204`，用于提示前端停止重连
- 若暂时无事件会返回 keep-alive 注释帧

前端示例:

```javascript
const es = new EventSource(`/v1/memory-agent/runs/${runId}/events?after_seq=0`);
es.onmessage = (evt) => {
  const event = JSON.parse(evt.data);
  console.log(event.type, event.payload);
};
```

`tool_call` 事件示例:

```json
{
  "run_id": "memory_run_xxx",
  "seq": 6,
  "timestamp": "2026-04-12T09:31:11.012Z",
  "type": "tool_call",
  "payload": {
    "call_id": 1,
    "ts": "2026-04-12 17:31:11",
    "tool_name": "search_details",
    "params": {
      "detail": "Emi hobbies",
      "topk": 5
    },
    "status": "started"
  }
}
```

`tool_result` 事件示例:

```json
{
  "run_id": "memory_run_xxx",
  "seq": 7,
  "timestamp": "2026-04-12T09:31:11.104Z",
  "type": "tool_result",
  "payload": {
    "call_id": 1,
    "tool_name": "search_details",
    "status": "completed",
    "result": {
      "hit": true,
      "matched_count": 3,
      "results": []
    }
  }
}
```

`workspace_state` 事件示例:

```json
{
  "run_id": "memory_run_xxx",
  "seq": 12,
  "timestamp": "2026-04-12T09:31:11.509Z",
  "type": "workspace_state",
  "payload": {
    "phase": "round_judged",
    "round_id": 1,
    "status": "INSUFFICIENT",
    "gap_type": "insufficient_episode_evidence",
    "reason": "Not enough episode evidence with concrete turn content.",
    "action_types": [
      "EVENT_DETAIL_RECALL"
    ],
    "episode_ref_count": 2,
    "kept_evidence_count": 1,
    "workspace": {
      "round_id": 1,
      "evidences": [],
      "kept_evidence_ids": [],
      "status": "INSUFFICIENT",
      "gap_type": "insufficient_episode_evidence"
    }
  }
}
```

## 4.7 工具调用汇总
### `GET /v1/memory-agent/runs/{run_id}/tool-calls`
作用: 给前端工具面板使用，展示当前 run 的全部工具调用汇总状态。

返回示例:

```json
{
  "run_id": "memory_run_xxx",
  "status": "running",
  "tool_call_count": 2,
  "calls": [
    {
      "call_id": 1,
      "tool_name": "search_details",
      "params": {
        "detail": "Emi hobbies",
        "topk": 5
      },
      "status": "completed",
      "result": {
        "hit": true
      }
    },
    {
      "call_id": 2,
      "tool_name": "search_content",
      "params": {
        "dialogue_id": "dlg_1",
        "episode_id": "ep_3"
      },
      "status": "started"
    }
  ]
}
```

## 4.8 Workspace 汇总
### `GET /v1/memory-agent/runs/{run_id}/workspace?limit=20`
作用: 给前端 workspace 面板使用，查看最新状态和最近 N 条 workspace 事件历史。

返回示例:

```json
{
  "run_id": "memory_run_xxx",
  "status": "running",
  "workspace_event_count": 3,
  "latest_workspace": {
    "phase": "round_judged",
    "round_id": 1,
    "status": "INSUFFICIENT",
    "gap_type": "insufficient_episode_evidence",
    "workspace": {
      "round_id": 1,
      "evidences": [],
      "kept_evidence_ids": [],
      "status": "INSUFFICIENT",
      "gap_type": "insufficient_episode_evidence"
    }
  },
  "history": [
    {
      "phase": "round_started",
      "round_id": 1,
      "force_remedy": false,
      "last_status": "INIT",
      "workspace": {}
    },
    {
      "phase": "round_judged",
      "round_id": 1,
      "status": "INSUFFICIENT",
      "gap_type": "insufficient_episode_evidence",
      "workspace": {}
    }
  ]
}
```

## 5. 数据结构说明

### 5.1 WorkspaceDocument（workspace evidence 单条记录）

workspace snapshot 中 `evidences` 数组的每一项为 `WorkspaceDocument`，结构如下：

| 字段 | 类型 | 说明 |
|------|------|------|
| `evidence_id` | string | 唯一标识。episode 类型如 `"dlg_01:ep_03"`，时间搜索类型如 `"time:00005"` |
| `content` | string | 预渲染的结构化文本，可直接展示给用户。包含时间、主题、参与者、事实、对话等信息 |
| `source_action_id` | string | 产生该 evidence 的 action 标识，如 `"r1_a1"` |
| `recall_score` | float \| null | 召回阶段的相似度分数 |
| `rerank_score` | float \| null | rerank 阶段的相关性分数 |

`content` 示例（episode 类型）：

```
【对话发生时间 Dialogue time】2023-01-15 ~ 2023-01-20
【场景主题 Scene theme】周末出行讨论
【参与者 Participants】Alice, Bob
【相关事实 Related facts】
  - Alice 提议去海边
  - Bob 更想去爬山
【对话内容 Dialogue】
  Alice: 这周末去海边怎么样？
  Bob: 我更想去爬山。
```

`content` 示例（时间搜索类型）：

```
【时间范围 Time range】2023-01-15 ~ 2023-01-20
【场景主题 Scene theme】周末出行讨论
```

前端展示建议：
- 直接以 `<pre>` 或等宽字体展示 `content` 文本
- 用 `recall_score` / `rerank_score` 展示分数标签
- 用 `evidence_id` 前缀区分来源类型（`time:` 为时间搜索，其余为 episode 语义搜索）

## 6. 常见错误

- 400: `question is required`
- 400: `config file not found: ...`
- 404: `run not found: ...`

## 7. 前端接入建议

- 列表页轮询 `GET /v1/memory-agent/runs`
- 详情页先拉 `GET /runs/{run_id}`、`/tool-calls`、`/workspace`
- 同时开启 SSE `GET /runs/{run_id}/events` 实时增量更新
- 断线后使用 `after_seq` 从上次序号继续订阅
