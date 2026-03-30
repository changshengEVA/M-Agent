# Chat API

## 概述

当前 Chat API 采用“服务启动时固定 config、运行时维护 thread session”的模型。

服务层当前基于 `FastAPI + Uvicorn`，并提供：

- 标准 HTTP JSON 接口
- `SSE` 事件流
- 内置跨域支持
- 自动 OpenAPI 文档：`/docs`

- 同一个 `thread_id` 会复用同一份服务端线程态上下文
- 主控聊天会读取该线程最近的历史轮次作为当前对话 `history`
- 记忆写回不会默认每轮立即入库，而是先进入线程 buffer
- 线程 memory mode 目前支持：
  - `manual`
  - `off`
- `manual` 模式下，buffer 可通过显式 `flush` 或空闲超时自动 flush
- `off` 模式下，新轮次仍会保留为聊天上下文，但不会进入待写回队列

## 一个重要约定

从当前版本开始，`tool_calls` 不再出现在 Chat API 的最终结果里。

原因：

- `tool_calls` 是回放汇总，不是实时过程流
- 它很容易和 SSE 的 `tool_call` / `tool_result` 实时事件混淆
- 前端如果需要展示“过程”，应该消费 SSE 事件流，而不是读取最终结果里的工具列表

现在的推荐做法是：

- 过程面板：消费 `SSE`
- 最终回答：消费 `assistant_message` 或 `GET /v1/chat/runs/{run_id}`
- 线程记忆状态：消费 `GET /v1/chat/threads/{thread_id}/memory/state`

## 服务启动

```powershell
$env:PYTHONPATH='src'
python -m m_agent.api.chat_api `
  --host 127.0.0.1 `
  --port 8777 `
  --config config/agents/chat/test_agent_chat.yaml `
  --idle-flush-seconds 600 `
  --history-max-rounds 12
```

说明：

- `--config`
  服务启动后固定，不支持请求级覆盖
- `--idle-flush-seconds`
  `manual` 模式下 pending buffer 的空闲自动 flush 阈值
- `--history-max-rounds`
  每个线程在服务内保留的最近历史轮次数

启动后可访问：

- Swagger UI：`http://127.0.0.1:8777/docs`
- OpenAPI：`http://127.0.0.1:8777/openapi.json`

## 线程模型

每个 `thread_id` 对应一份服务端 session，核心状态包括：

- `mode`
  `manual | off`
- `history_rounds`
  当前仍保留在服务内、可作为 chat history 的轮次数
- `pending_rounds`
  当前尚未 flush 的待写回轮次数
- `last_activity_at`
  最近一次该线程完成 assistant 回复的时间
- `last_flush_at`
  最近一次成功 flush 的时间

## API

### 1. 健康检查

`GET /healthz`

返回示例：

```json
{
  "ok": true,
  "service": "m-agent-chat-api",
  "runtime": {
    "config_path": "F:\\AI\\M-Agent\\config\\prompt\\test_agent_chat.yaml",
    "default_thread_id": "test-agent-1",
    "persist_memory": true,
    "idle_flush_seconds": 600,
    "history_max_rounds": 12
  }
}
```

### 2. 创建一次聊天 run

`POST /v1/chat/runs`

请求体：

```json
{
  "thread_id": "demo-thread-1",
  "message": "我上次提过的旅行计划是什么？"
}
```

注意：

- 不接受请求体里的 `config`
- 若传入 `config`，服务端会返回 `400`
- 该接口返回的是异步 run 元信息，不是最终回答结果

返回示例：

```json
{
  "run_id": "run_xxx",
  "status": "queued",
  "thread_id": "demo-thread-1",
  "events_url": "/v1/chat/runs/run_xxx/events",
  "result_url": "/v1/chat/runs/run_xxx"
}
```

### 3. 查询 run 结果

`GET /v1/chat/runs/{run_id}`

返回中的 `result` 为完整聊天结果对象，但不包含 `tool_calls`。

常见字段包括：

- `answer`
- `agent_result.answer`
- `agent_result.recall_mode`
- `agent_result.question_plan`
- `agent_result.sub_question_results`
- `agent_result.plan_summary`
- `agent_result.tool_call_count`
- `memory_capture`
- `thread_state`

返回示例：

```json
{
  "run_id": "run_xxx",
  "status": "completed",
  "thread_id": "demo-thread-1",
  "message": "我上次提过的旅行计划是什么？",
  "result": {
    "success": true,
    "thread_id": "demo-thread-1",
    "question": "我上次提过的旅行计划是什么？",
    "answer": "你之前提到想在五一去杭州旅行。",
    "agent_result": {
      "answer": "你之前提到想在五一去杭州旅行。",
      "plan_summary": "Shallow recall over stored details.",
      "tool_call_count": 1,
      "recall_mode": "shallow_recall",
      "question_plan": {
        "goal": "",
        "question_type": "",
        "decomposition_reason": "Single-step detail recall over search_details.",
        "sub_questions": [],
        "suggested_tool_order": [],
        "completion_criteria": ""
      },
      "sub_question_results": []
    },
    "memory_capture": {
      "mode": "manual",
      "status": "buffered"
    },
    "thread_state": {
      "thread_id": "demo-thread-1",
      "pending_rounds": 1
    }
  },
  "error": null
}
```

### 4. 订阅 run 事件流

`GET /v1/chat/runs/{run_id}/events`

SSE 事件类型包括：

- `run_started`
- `recall_started`
- `question_strategy`
- `plan_update`
- `sub_question_started`
- `tool_call`
- `tool_result`
- `sub_question_completed`
- `direct_answer_payload`
- `direct_answer_fallback`
- `final_answer_payload`
- `recall_completed`
- `assistant_message`
- `memory_capture_updated`
- `thread_state_updated`
- `chat_result`
- `run_completed`
- `run_failed`

说明：

- `tool_call` 和 `tool_result` 才是实时工具过程事件
- `chat_result.payload.agent_result` 是本轮最终主控结果，不是实时过程流
- `run_completed.payload.result` 是完整最终结果对象，便于前端在不额外请求 `result_url` 的情况下完成收尾

#### 事件语义

- `recall_started`
  主控决定开始调用 recall 工具，payload 里有 `mode`
- `question_strategy`
  `deep_recall` 判断是否先走 direct answer，还是直接分解
- `plan_update`
  `deep_recall` 输出问题分解后的 plan
- `sub_question_started`
  某个子问题开始执行
- `tool_call`
  某个工具开始调用
- `tool_result`
  某个工具返回结果
- `sub_question_completed`
  某个子问题结束
- `final_answer_payload`
  recall 层得出的最终结构化回答
- `recall_completed`
  某次 recall 工具结束
- `assistant_message`
  主控最终对用户说的话
- `chat_result`
  主控最终结果对象
- `run_completed`
  整个 run 结束

#### 两条典型事件序列

`shallow_recall` 常见序列：

1. `run_started`
2. `recall_started`
3. `tool_call`
4. `tool_result`
5. `final_answer_payload`
6. `recall_completed`
7. `assistant_message`
8. `chat_result`
9. `run_completed`

`deep_recall` 常见序列：

1. `run_started`
2. `recall_started`
3. `question_strategy`
4. `plan_update`
5. `sub_question_started`
6. `tool_call`
7. `tool_result`
8. `sub_question_completed`
9. 重复 5 到 8，直到所有子问题结束
10. `final_answer_payload`
11. `recall_completed`
12. `assistant_message`
13. `chat_result`
14. `run_completed`

`run_completed` 事件示例：

```json
{
  "run_id": "run_xxx",
  "seq": 18,
  "type": "run_completed",
  "payload": {
    "thread_id": "demo-thread-1",
    "answer": "你之前提到想在五一去杭州旅行。",
    "result": {
      "success": true,
      "thread_id": "demo-thread-1",
      "answer": "你之前提到想在五一去杭州旅行。"
    }
  }
}
```

### 5. 查询线程 memory 状态

`GET /v1/chat/threads/{thread_id}/memory/state`

该接口返回线程态摘要，并附带当前保留历史轮次的完整文本数据。

返回示例：

```json
{
  "thread_id": "demo-thread-1",
  "mode": "manual",
  "history_rounds": 3,
  "history_messages": 6,
  "pending_rounds": 2,
  "pending_turns": 4,
  "has_pending_data": true,
  "last_activity_at": "2026-03-29T10:00:00Z",
  "last_flush_at": null,
  "idle_flush_seconds": 600,
  "idle_flush_deadline": "2026-03-29T10:10:00Z",
  "history_rounds_data": [
    {
      "round_id": "round_xxx",
      "capture_state": "pending",
      "flush_id": null,
      "user_message": "我最近在准备五一去杭州旅行",
      "assistant_message": "你之前提到想在五一去杭州旅行。",
      "user_at": "2026-03-29T09:58:00Z",
      "assistant_at": "2026-03-29T09:58:01Z"
    }
  ],
  "history_preview": [
    {
      "round_id": "round_xxx",
      "capture_state": "pending",
      "flush_id": null,
      "user_message": "我最近在准备五一去杭州旅行",
      "assistant_message": "你之前提到想在五一去杭州旅行。",
      "user_at": "2026-03-29T09:58:00Z",
      "assistant_at": "2026-03-29T09:58:01Z"
    }
  ]
}
```

字段说明：

- `history_rounds_data`
  当前线程保留在服务内的完整历史轮次
- `history_preview`
  `history_rounds_data` 的最近 3 轮快捷预览

### 6. 设置线程 memory mode

`POST /v1/chat/threads/{thread_id}/memory/mode`

请求体：

```json
{
  "mode": "off",
  "discard_pending": false
}
```

字段说明：

- `mode`
  `manual | off`
- `discard_pending`
  是否把当前 pending rounds 从待 flush 队列中移除

行为说明：

- `manual`
  新轮次会进入 pending buffer
- `off`
  新轮次不会进入 pending buffer，但仍会保留在线程历史里供 chat history 使用
- `discard_pending=true`
  会把当前 pending rounds 改成 `skipped`，不再参与后续 flush

### 7. 手动 flush 线程 buffer

`POST /v1/chat/threads/{thread_id}/memory/flush`

请求体：

```json
{
  "reason": "manual_api"
}
```

返回示例：

```json
{
  "success": true,
  "thread_id": "demo-thread-1",
  "flush_reason": "manual_api",
  "status": "written",
  "rounds_flushed": 3,
  "turns_flushed": 6,
  "memory_write": {
    "success": true,
    "dialogue_id": "chat_demo-thread-1_20260329_100000_000001"
  },
  "thread_state": {
    "thread_id": "demo-thread-1",
    "mode": "manual",
    "pending_rounds": 0
  }
}
```

若当前没有 pending rounds，则返回：

```json
{
  "success": true,
  "status": "noop",
  "message": "no pending rounds to flush"
}
```

## 前端消费指南

### 最推荐的消费方式

1. 前端调用 `POST /v1/chat/runs`
2. 拿到 `run_id`、`events_url`、`result_url`
3. 立即连接 `GET /v1/chat/runs/{run_id}/events`
4. 用 SSE 事件驱动“思考过程”面板
5. 用 `assistant_message` 更新回答气泡
6. 收到 `run_completed` 后：
   - 标记本轮结束
   - 如有需要，再请求 `GET /v1/chat/runs/{run_id}` 做最终快照校验

### Thinking 面板推荐绑定

- `recall_started`
  显示本轮选择了 `shallow_recall` 还是 `deep_recall`
- `question_strategy`
  显示是否先尝试 direct answer
- `plan_update`
  显示问题分解结果
- `sub_question_started`
  显示子问题开始
- `tool_call`
  显示工具名和参数
- `tool_result`
  显示工具结果
- `sub_question_completed`
  显示子问题结果
- `final_answer_payload`
  显示 recall 层结构化答案

### Chat 面板推荐绑定

- `assistant_message`
  直接作为用户看到的最终回复

### Memory/Thread 状态面板推荐绑定

- `memory_capture_updated`
  显示本轮是被 buffered 还是 skipped
- `thread_state_updated`
  更新 pending rounds、history rounds、mode 等线程态指标

### 不推荐的消费方式

- 不要用 `agent_result` 来还原实时工具调用过程
- 不要期待最终结果里存在 `tool_calls`
- 不要把 `POST /v1/chat/runs` 的 201 响应当成最终结果

### 一个最小前端伪代码

```ts
const run = await fetch("/v1/chat/runs", {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({thread_id, message}),
}).then(r => r.json());

const source = new EventSource(run.events_url);

source.addEventListener("recall_started", (evt) => {
  const data = JSON.parse(evt.data);
  renderRecallMode(data.payload.mode);
});

source.addEventListener("tool_call", (evt) => {
  const data = JSON.parse(evt.data);
  renderToolCall(data.payload);
});

source.addEventListener("tool_result", (evt) => {
  const data = JSON.parse(evt.data);
  renderToolResult(data.payload);
});

source.addEventListener("assistant_message", (evt) => {
  const data = JSON.parse(evt.data);
  renderAnswer(data.payload.answer);
});

source.addEventListener("run_completed", async (evt) => {
  const data = JSON.parse(evt.data);
  finalizeRun(data.payload.result);
  source.close();
});
```

## 常见误区

### 1. 为什么 `POST /v1/chat/runs` 返回不完整

因为它只负责创建异步 run，不负责直接返回最终结果。

### 2. 为什么最终 `result` 里没有 `tool_calls`

因为 `tool_calls` 是回放汇总，不是实时过程流。  
前端应该消费 SSE 的 `tool_call` / `tool_result`。

### 3. 怎么知道主控选了 `deep_recall` 还是 `shallow_recall`

看 `recall_started.payload.mode`。

### 4. 怎么判断 `deep_recall` 是否做了问题分解

看是否出现 `plan_update`、`sub_question_started`、`sub_question_completed`。

## 设计说明

- thread history 是服务端热上下文，只用于当前 chat 连贯性
- flush 后的数据会进入正式 MemoryCore 流程，成为 durable memory
- durable memory 与热 history 是两层机制，并行存在
- 当前实现会保留最近若干轮次作为 chat history，较早的已 flush 或已 skipped 轮次会按 `history_max_rounds` 裁剪
