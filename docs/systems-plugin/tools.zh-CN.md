# Tools 子系统——可插拔说明

> English: [tools.md](./tools.md) · 总索引：[README.zh-CN.md](./README.zh-CN.md)

## 职责

Tools 子系统向执行层暴露 LangChain capability。当前工具集为每个 capability 使用一份
YAML 清单，使注册、prompt 描述、参数路由、结果投影、限流和依赖元数据共享唯一事实源。

## 目录与工具集配置

```text
config/systems/tools/
├── default.yaml
├── external_writes_enabled.yaml
└── capabilities/
    ├── web_search.yaml
    ├── schedule_create.yaml
    └── ...
```

```yaml
# config/agents/chat/chat_controller.yaml
systems:
  tools: ../../systems/tools/default.yaml
```

```yaml
# config/systems/tools/default.yaml
system: tools
capabilities_dir: ./capabilities
enabled:
  - shallow_recall
  - reply_to_user
  - get_current_time
  - web_search
  - schedule_query
  - email_ask
  - email_read
defaults:
  __controller__:
    max_calls_per_turn: 12
```

省略 `enabled` 时，加载器使用与 `default.yaml` 相同的安全 read/emit allow-list；
仅仅发现 capability 清单不会把全部工具自动启用。

> **当前安全边界：**`default.yaml` 只启用读取类工具和 `reply_to_user`。创建/删除
> 日程及发送邮件必须显式切换到 `external_writes_enabled.yaml`。缺失、`unspecified`
> 或未知的 `policy.side_effect` 会在加载或构建阶段 fail closed；合法分类只有
> `read`、`emit`、`write`、`send`。该分类和显式 profile 用于防止误启用，交互式
> 审批策略仍属于后续路线图能力。兼容用 `deep_recall` API 也不默认启用，因为本地
> backend 尚未实现真正的 deep 语义。

确实需要外部写入的部署，应让 Chat 使用
`config/agents/chat/chat_controller_external_writes.yaml`。该 profile 会同时选择
`config/systems/tools/external_writes_enabled.yaml` 与
`config/agents/email/gmail_email_agent_external_writes.yaml`。只切换 tools suite 不足以
发送 Gmail：默认 Gmail 配置只有只读 OAuth scope，写入 profile 使用独立的发送 token。

## Capability 清单

```yaml
name: web_search
version: 1
category: information
builder: m_agent.systems.tools.default.capabilities.web_search_ops:_build_web_search_tool

descriptions:
  en: Search the public web or inspect a known URL.
  zh: 搜索公开网络或读取指定 URL。

input:
  mode: param_llm
  schema: inferred_from_tool

output:
  schema: builtins:dict
  feedback_projector: m_agent.runtime.turn_support.execution_feedback:feedback_summary_from_tool_history
  memory_projector: m_agent.chat.working_memory:project_tool_call_to_entry

policy:
  side_effect: read
  max_calls_per_turn: 3

dependencies: [web_search_client, api_credentials]

defaults:
  provider: auto
  max_results: 5
```

| 字段 | 含义 |
|------|------|
| `name` | 规划、调用、日志和记忆共同使用的稳定 capability 名 |
| `version` | 大于零的契约版本 |
| `category` | Descriptor 分类 |
| `builder` | 返回 LangChain 工具的可导入 callable |
| `descriptions` | 暴露给思考层的多语言描述 |
| `input.mode` | `param_llm`、`instruction_arg`、`no_args` 或 `reply` |
| `input.instruction_arg` | 直接映射 instruction 的目标键 |
| `input.schema` | Schema 引用；`inferred_from_tool` 读取 LangChain args schema |
| `output.*` | 结果 schema 与经过校验的 feedback/WM projector 路径 |
| `policy.max_calls_per_turn` | 单 capability 调用上限 |
| `policy.side_effect` | 必填的可执行副作用分类，只接受 `read`、`emit`、`write`、`send`；缺失/未知值 fail closed，write/send 仍须显式 profile opt-in |
| `dependencies` | 由 capability context 提供的所需服务 |
| `defaults` | 单 capability runtime 默认参数 |

加载工具集时会解析所有可执行 dotted path，因此无效清单会在启动阶段失败。

## 参数模式

| 模式 | 行为 |
|------|------|
| `param_llm` | 按工具 schema 填充结构化参数 |
| `instruction_arg` | 把思考层 instruction 映射到声明的键 |
| `no_args` | 使用 `{}` 调用 |
| `reply` | 不经参数填充，直接构建 `reply_to_user` payload |

文件型 capability 的参数路由完全由清单控制。

## Runtime 与 `runtime_hooks` 契约

LangGraph turn port 通过
`ExecutionAgent.invoke_tool_direct(..., runtime_hooks=...)` 调用且只调用一个
capability。执行层把该映射复制到
`ControllerCapabilityContext.controller_state["runtime"]`。

当前 hook 可包括：

| Hook | 用途 |
|------|------|
| `transaction_id`, `conversation_id`, `delegate_id`, `effect_id` | 持久化归属 |
| `idempotency_key`, `delivery_guarantee` | 副作用标识与投递策略 |
| `on_reply` | 投递用户可见回复 |
| `on_schedule_created` | 把新 schedule 关联到 runtime state |
| `scene_writer` | 追加受 transaction fence 保护的 Scene entry |

Hook 属于单次调用；除非 capability 契约明确要求，否则均为可选。Capability 从
`controller_state["runtime"]` 读取 hook，通过 `ControllerCapabilityContext` 获取服务，
且不导入 graph 内部实现。

Builder 应在外部操作前后调用 `start_tool_call`、`check_tool_call_limits`、
`record_tool_use` 和 `finish_tool_call`。Episodic 访问通过
`context.get_episodic_backend()` 完成。

## 新增 capability

1. 在 `src/m_agent/systems/tools/default/capabilities/` 或其他可导入包中实现 builder。
2. 在 `config/systems/tools/capabilities/` 下新增且只新增一份清单。
3. 工具集使用显式列表时，把名称加入 `enabled`。
4. 按需接入 context 依赖和 feedback/WM 投影。
5. 测试加载、参数路由、调用、限流、投影与 hook。

```bash
pytest tests/systems/
pytest tests/runtime/test_turn_support_tool_args.py tests/test_chat_controller_tool_limits.py
pytest tests/chat/test_working_memory.py tests/runtime/test_langgraph_turn_loop.py
```
