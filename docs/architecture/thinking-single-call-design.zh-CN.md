# Thinking 单次 LLM 调用设计

> 状态：在途设计，目标进入 v0.2.1；当前尚未作为已发布能力验收  
> 日期：2026-08-03  
> 目标：把任务状态生成与当前动作决策合并为一次 LLM 调用。

本文用于指导 v0.2.1 实现与验收；若代码、测试与本文不一致，以当前可执行测试和 [`../runtime/README.md`](../runtime/README.md) 记录的事实为准。

## 1. 设计结论

当前 Thinking 在一个 `think` node 内顺序执行两次模型调用：

1. `thinking.pre_gen_task_state` 生成任务状态更新；
2. `thinking.plan` 读取更新后的任务状态，再生成当前动作。

调整后只保留一次联合生成：

```text
旧 TaskState + 当前刺激 + 上下文
                 │
                 ▼
          一次结构化 LLM 调用
                 │
                 ▼
      reason → 新 TaskState → Decision
                 │
                 ▼
             commit_thought
                 │
                 ▼
       plan_delegate / settle_turn
```

模型先给出简短 `reason`，随后生成保持连续性的新 `TaskState`，最后依据这个新状态给出与现有 `ThinkingDecision` 等价的详细决策。

第一版不改变现有 LangGraph 拓扑，也不新增修复子图。LangGraph 继续负责 turn 编排、checkpoint、提交以及 delegate/settle 分流；结构化输出由 LangChain `with_structured_output` 和 Pydantic 负责。

## 2. 调整范围

### 2.1 本次合并

只合并 `ThinkingAgent.handle()` 内的：

- `_pre_gen_task_state()`；
- `_plan()`。

### 2.2 保持不变

以下调用具有不同的输入依赖，不纳入本次调整：

- `resolve_transaction`：需要先确定当前刺激属于哪个 Transaction；
- `fill_tool_args`：需要先由 Decision 选定 capability，之后才能按对应工具 schema 填参；
- 工具执行、execution feedback、reply delivery 和 completion gate；
- Transaction、Scene、delegate 与 effect 的既有外部契约。

因此这里的“单次调用”特指一次事务内 Thinking，不能理解为整个用户请求全链路永远只有一次模型调用。

## 3. 联合输出结构

### 3.1 顶层结构

使用 Pydantic 定义联合输出，并按照希望模型生成的先后顺序声明字段：

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskStateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(max_length=1000)
    completion_status: Literal[
        "processing",
        "awaiting_user",
        "completed",
    ]
    completed: list[str] = Field(max_length=32)
    remaining: list[str] = Field(max_length=32)


class DecisionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["execute", "answer_directly", "silent"]
    tool_name: str | None
    instruction: str | None
    answer: str | None
    episode_note: str | None

    @model_validator(mode="after")
    def validate_mode_fields(self):
        ...


class ThinkingTurnOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 放在第一个字段，引导模型先形成规划依据。
    reason: str = Field(max_length=1000)

    # 基于旧状态生成的完整新快照。
    task_state: TaskStateOutput

    # 必须依据上面的新 task_state 生成。
    decision: DecisionOutput
```

选择完整 `TaskState` 快照而不是 partial patch，原因是联合调用需要明确表达“模型最终认为当前任务处于什么状态”。旧状态会完整放入 Prompt，模型负责继承未变化的内容。

### 3.2 TaskState 连续性

沿用现有字段，不再新增 current/future 两套数据结构：

- `goal`：整个 Transaction 的稳定目标；
- `completed`：已有对话或可信 execution evidence 确认完成的步骤；
- `remaining[0]`：当前最应该处理的步骤；
- `remaining[1:]`：当前步骤之后的后续步骤，按预期顺序排列；
- `completion_status`：宏观任务状态。

连续性规则：

- 当前刺激没有改变目标时，原样保留 `goal`；
- 不得无依据删除、改写或重复已有 `completed`；
- 只有出现可信完成证据时，才能把步骤从 `remaining` 移入 `completed`；
- 新的用户请求进入当前 Transaction 时保持 `processing`；
- 仍需向用户发送结果时不能提前标记 `completed`；
- 只有完整请求和必要回复都已完成时才使用 `completed`。

### 3.3 Decision 兼容

`DecisionOutput` 与当前 `ThinkingDecision` 保持同样的动作语义：

| mode | 必须包含 | 必须为空 |
| --- | --- | --- |
| `execute` | `tool_name`、`instruction` | `answer` |
| `answer_directly` | `answer` | `tool_name`、`instruction` |
| `silent` | 无 | `tool_name`、`instruction`、`answer` |

服务端继续校验 `tool_name` 是否属于当前 enabled capabilities。每个 turn 仍然最多委托一个工具。

模型不再输出 `request_complete`。兼容层按下面的规则构造现有字段：

```python
decision.request_complete = (
    output.task_state.completion_status == "completed"
)
```

`completed`、`awaiting_user` 或 Transaction 已处于 pause 时，服务端最终统一使用 `silent`，不继续 reply 或 delegate。

## 4. reason（显式 CoT）字段

`reason` 放在联合结构的第一个字段，Prompt 也明确要求按 `reason → task_state → decision` 的顺序生成。它作为本轮显式 CoT/规划字段，让模型在写任务状态和动作之前，先形成一段可读的任务分析。

需要注意：JSON 对象在规范上没有语义顺序，字段声明顺序本身不是强保证，因此 Prompt 仍必须明确生成步骤。

`reason` 建议限制为一至三句简短说明，不要求输出冗长的完整思维链。它只服务于本轮决策，不应写入长期记忆、RAG 或用户可见回复：

- `reason`：本轮临时规划依据；
- `episode_note`：真正值得在后续记住的简短内容；
- `answer`：用户可见内容。

当前 `commit_thought` 会把原 `decision.reasoning` 写入 Scene。接入新结构时应停止持久化 `reason`，或者只记录固定长度的内部诊断摘要，避免规划文本污染 Scene 和后续记忆。

## 5. 合并 Prompt

公共上下文只渲染一次：

```text
[Persona / Role]
[Delegable Capabilities]
[Current Stimulus]
[Activation Event]
[Current Objective]
[Observed Evidence]
[Dialogue History]
[Scene Context]
[Previous Task State]
[Working Memory]
[Thinking Turn Instructions]
```

建议核心指令：

```text
请生成一次完整的 Thinking Turn，并严格执行以下顺序：

1. reason
   用一至三句话分析当前刺激、旧 TaskState、Observed Evidence 和当前能力。
   Scene Context 只能帮助理解，不能单独证明任务已经完成。

2. task_state
   在 Previous Task State 基础上生成完整的新状态：
   - 没有变化的字段必须保持连续；
   - remaining[0] 是当前步骤；
   - remaining[1:] 是之后的步骤；
   - 只有可信证据可以推进 completed；
   - 不要把原始工具结果复制进任务状态。

3. decision
   必须基于刚生成的新 task_state 决定本轮动作：
   - execute：选择一个已启用工具并给出详细 instruction；
   - answer_directly：直接形成用户回复；
   - silent：本轮不执行工具也不回复。

只能输出 ThinkingTurnOutput 对应的结构化内容。
```

原有 `pre_gen_task_state` 与 `make_decision` 中有效的领域约束合并到这个 Prompt；重复的输入块、输出示例和 `request_complete` 说明删除。

## 6. Runtime 接入

### 6.1 ThinkingAgent

在 `ThinkingAgent` 中增加单一入口：

```python
def _think_once(
    self,
    perception: PerceptionInput,
    state: ConversationState,
) -> ThinkingTurnOutput:
    messages = self._build_thinking_turn_messages(perception, state)
    model = self.model_provider.model.with_structured_output(
        ThinkingTurnOutput,
        include_raw=False,
    )
    return self._invoke_structured(
        model,
        messages=messages,
        call_name="thinking.turn",
    )
```

`handle()` 的主要流程调整为：

```text
准备 state
  → user_message 预先标记 processing
  → _think_once()                       唯一常规思考调用
  → 校验并应用 output.task_state
  → 保留 param-gap / awaiting-user 修正规则
  → 根据最终状态规范化 output.decision
  → 投影为现有 ThinkingDecision
  → 发出兼容 SSE 事件
  → 返回
```

原先的确定性顺序继续保留：

```text
user_message 强制 processing
  → 接受新的 TaskState
  → param-gap 强制 processing
  → 未发送澄清回复时禁止 awaiting_user
  → completed / awaiting_user / pause 决定最终 silent
```

### 6.2 LangGraph

现有拓扑保持不变：

```text
load_turn
  → think
  → commit_thought
  → plan_delegate
  → delegate | settle_turn
```

`think` node 内从两次 LLM 调用变为一次 `_think_once()`。联合输出通过校验后，同时形成：

- 下一版 TaskState；
- 当前 ThinkingDecision。

`commit_thought` 继续作为持久化边界。最好让 `think` 把候选 TaskState 和 Decision 一起放入 graph state，再由 `commit_thought` 一次提交，避免只更新了其中一部分。

不需要为了这次合并增加额外 graph nodes。结构解析失败按普通 Thinking 调用失败处理；保留旧双调用实现作为短期配置回退即可。

### 6.3 SSE 与下游

为了不修改客户端协议，一次联合结果仍派生原来的事件顺序：

```text
thinking_started
thinking_task_state
thinking_plan
thinking_completed
```

下游继续接收现有 `ThinkingDecision`，因此 `plan_delegate`、ExecutionAgent、工具与 execution feedback 不需要改变接口。

## 7. 最小校验

实现完成后保留下面这些结构和回归检查：

- Pydantic 能拒绝非法 mode、缺字段和额外字段；
- `execute`、`answer_directly`、`silent` 的字段组合正确；
- `tool_name` 属于 enabled capabilities；
- 新 TaskState 能继承旧 `goal/completed/remaining`；
- `remaining[0]` 与当前 Decision 一致；
- `request_complete` 由最终状态派生；
- 一个正常 Thinking turn 只发生一次 `thinking.turn` 调用；
- completed、awaiting_user、param gap、execution feedback 和 completion gate 的现有行为不变；
- 当前 Runtime 单元测试与语义验收继续通过。

真实模型接入后只需挑选一小组典型 Prompt 人工观察任务状态连续性和 instruction 质量；如果实际出现明显不稳定，再针对具体问题调整 Prompt。

## 8. 配置与回退

迁移期间可以保留一个简单开关：

```yaml
runtime:
  langgraph:
    thinking_mode: single_call  # single_call | legacy_two_call
```

- 默认开发方向为 `single_call`；
- `legacy_two_call` 只用于快速回退；
- 两套路径对外都返回同一个 `ThinkingDecision`；
- 确认新路径稳定后，再删除旧 Prompt 和旧私有方法。

## 9. 预计修改点

| 文件 | 修改 |
| --- | --- |
| `src/m_agent/layers/thinking/contracts.py` | 增加 Pydantic 联合输出契约 |
| `src/m_agent/layers/thinking/core.py` | 增加 `_think_once()`，替换两次调用 |
| `src/m_agent/runtime/langgraph/graph_state.py` | 如需原子提交，增加候选 TaskState 字段 |
| `src/m_agent/runtime/langgraph/turn_graph.py` | 让 `commit_thought` 同时提交 TaskState 与 Decision |
| `config/agents/chat/runtime/chat_controller_runtime.yaml` | 增加统一 Thinking Prompt |
| `config/agents/chat/chat_controller.yaml` | 迁移期调用模式开关 |
| `tests/agents/thinking/` | 单次调用、状态连续性和 Decision 结构测试 |
| `tests/runtime/` | commit、completion 与 delegate 回归测试 |

## 10. 完成标准

满足下面条件即可认为合并完成：

1. 正常 `ThinkingAgent.handle()` 只调用一次 LLM；
2. 一次输出同时包含 `reason`、完整连续的 `task_state` 和详细 `decision`；
3. `remaining` 能表达当前步骤及后续步骤；
4. 输出能无损投影为当前 `ThinkingDecision`；
5. `request_complete` 不再由模型自由决定；
6. LangGraph、SSE、delegate 和 execution feedback 对外语义不变；
7. `reason` 不进入长期记忆；
8. 现有测试与语义验收通过。
