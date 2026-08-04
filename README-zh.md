# M-Agent

**Stimulus-Native Cognitive Runtime（刺激原生认知运行时）**

> 让 Agent 的认知不再以用户消息为边界，而由世界变化、行动结果、时间条件和内部预期持续驱动。

M-Agent 研究并实现一套独立于单次 LLM 调用而持续存在的运行环境：它接纳异步刺激，维护事务与状态，在需要认知时编译上下文 `c`，约束行动，并把行动结果重新送回认知循环。

面向个人助手，它最终希望提供一种简单体验：

> **说一次，持续接得上；需要你时才出现。**

[愿景与目标](docs/vision-and-goals.zh-CN.md) · [v0.2.0—v1.0.0 路线图](docs/roadmap-v0.2.0-v1.0.0.zh-CN.md) · [目标架构](docs/architecture/cognitive-runtime-architecture.zh-CN.md) · [路线图 PDF](docs/pdf/M-Agent-Roadmap-v0.2.0-v1.0.0.zh-CN.pdf)

> **当前阶段：v0.2.0 开发基线。** 本页会严格区分“已经实现”与“目标能力”；认知运行时愿景不代表所有目标模块已经完成。

## 为什么是“刺激”，而不只是“消息”

用户消息只是刺激的一种。一个持续存在的 Agent 还可能因为以下变化重新认知：

- 用户消息、邮件、日程、网页变化或其他外部观察；
- 工具成功、执行失败、权限拒绝或其他行动结果；
- 截止时间临近、预期回复未出现或任务长期停滞；
- 新证据与已有判断发生冲突。

M-Agent 的核心技术主线称为 **Stimulus-to-Cognition Compilation（刺激到认知的编译）**：

```text
Signal
→ Observation
→ Stimulus
→ Admission / Attention
→ Goal / Transaction Attribution
→ Cognitive State Transition
→ Context Snapshot c
→ Action / Silence
→ Effect Feedback
```

项目真正关心的不是“连接多少事件源”，而是：世界发生变化以后，Agent 是否应该醒来、应该唤醒哪项持续事务、应该带着什么认知上下文醒来，以及为什么行动或保持沉默。

## v0.2.0 已经实现什么

当前仓库提供认知运行时的可靠性骨架：

- 单个 LangGraph-backed `RuntimeHost`（`langgraph_v1`）；
- 持续存在的 Transaction、TaskState、Activation 与 Scene 时间线；
- Stimulus Inbox、优先级、接纳顺序、事务归因和明确来源校验；
- 用户消息、到期计划与执行反馈共用的感知和运行路径；
- Thinking、Delegate、Effect、Feedback 与用户回复闭环；
- SQLite checkpoint、effect ledger、flush journal 及重启恢复基础；
- Transaction 级工作记忆，以及由模型主动调用的简单 RAG 工具记忆；
- 覆盖 Transaction、Stimulus Pool、Attribution 和恢复语义的可执行验收平台。

当前仍未完成：

- 通用 Observation Source SDK 和公开的 `runtime.ingest(observation)`；
- 完整的 Attention，以及 `ignored / merged / deferred / activated` 处置模型；
- Goal、Expectation、Evidence、Belief 和可回放 `Context Snapshot`；
- 由 Runtime 自动维护并注入上下文的系统记忆；
- “预期未满足”等内生刺激；
- 生产可用的 Strategy System；
- 权限、预算、审批和 Kill Switch 组成的受控自主性。

这些边界分别安排在 v0.3.0—v0.8.0，而不是包装成 v0.2.0 的现有能力。

## 路线图摘要

| 版本 | 主题 | 核心目标 |
| --- | --- | --- |
| v0.2.0 | 当前运行时基线 | 固化 Transaction、Scene、Stimulus、Schedule、Effect/Feedback 与工具记忆 |
| v0.2.1 | 可信基线 | 修复恢复、上下文、记忆正确性、安全默认值和开源交付 |
| v0.3.0 | Stimulus Kernel | 公开 Observation/Stimulus 协议、`runtime.ingest()`、处置 Trace 与 Adapter |
| v0.4.0 | Attention & Attribution | 过滤噪声，判断是否应该醒以及应该唤醒哪项事务 |
| v0.5.0 | Cognitive State & Context Compiler | 建立认知状态与可追溯 `Context Snapshot`，系统记忆进入 Shadow |
| v0.6.0 | System Memory & Temporal Runtime | 系统记忆进入主链，时间与 Expectation 可计算，Strategy 进入 Shadow |
| v0.7.0 | Endogenous Cognition & Strategy | 内生刺激与受限、可评测的 Strategy 指导 |
| v0.8.0 | Bounded Autonomy & Cognitive SDK | 权限、预算、审批、Kill Switch 与认知插件 SDK |
| v0.9.0 | Release Candidate | 冻结契约，完成迁移、安全和长期运行验证 |
| v1.0.0 | Stable Cognitive Runtime | 稳定的刺激原生认知运行时公共契约 |

完整目标、交付项与放行条件见[版本规划](docs/roadmap-v0.2.0-v1.0.0.zh-CN.md)或[可分发 PDF](docs/pdf/M-Agent-Roadmap-v0.2.0-v1.0.0.zh-CN.pdf)。

## 系统记忆与工具记忆

M-Agent 明确区分两种机制：

- **工具记忆**：模型意识到需要回忆后，主动调用 RAG、搜索或档案工具。当前 `SimpleRagEpisodicBackend` 属于这一类。
- **系统记忆**：Runtime 自动投影、整合和检索已提交的 Scene、TaskState、Evidence 与 Effect，并由 Context Compiler 在决策前评估是否进入 `c`。

系统记忆计划在 v0.5.0 以 Shadow 方式接入，在 v0.6.0 进入认知主链。Strategy 属于程序性知识：v0.6.0 Shadow、v0.7.0 Opt-in，只有经过消融评测和安全门槛后才会考虑成为默认候选。

## 安装

要求 Python `>=3.10`。当前开发环境建议从仓库根目录安装完整依赖并进行可编辑安装：

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

Linux / macOS 使用 `source .venv/bin/activate` 激活环境。

## 最小配置

在仓库根目录创建 `.env`。当前 Chat 模型通过 OpenAI-compatible 接口访问，以下两个密钥变量任选其一：

```dotenv
API_SECRET_KEY=你的兼容接口密钥
# OPENAI_API_KEY=你的兼容接口密钥
BASE_URL=https://你的服务地址/v1
```

默认简单 RAG 使用本地 `hash` 表示，不要求嵌入服务密钥。只有在相应 YAML 中启用可选集成时，才需要配置它们实际读取的凭据，例如：

```dotenv
# Alibaba embedding / rerank（可选）
ALIBABA_API_KEY=
ALIBABA_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ALIBABA_EMBED_MODEL=text-embedding-v4

# Web search（可选，按所选 provider 填写）
YDC_API_KEY=
TAVILY_API_KEY=
```

## 启动 Chat API

Windows PowerShell：

```powershell
$env:PYTHONPATH = "src"
python -m m_agent.api.chat_api `
  --host 127.0.0.1 `
  --port 8777 `
  --config config/agents/chat/chat_controller.yaml
```

Linux / macOS：

```bash
export PYTHONPATH=src
python -m m_agent.api.chat_api \
  --host 127.0.0.1 \
  --port 8777 \
  --config config/agents/chat/chat_controller.yaml
```

启动后访问：

- Swagger UI：`http://127.0.0.1:8777/docs`
- OpenAPI JSON：`http://127.0.0.1:8777/openapi.json`
- 健康检查：`http://127.0.0.1:8777/healthz`

完整的认证、HTTP、SSE、Thread、Transaction、Scene、Memory 与 Schedule 接口见 [Chat API 参考](docs/chat_api/README.md)。当前 `/threads/{thread_id}/stimuli` 仍是面向用户式输入的入口；通用 Observation 接纳协议属于 v0.3.0 目标。

## 当前存储边界

- Transaction WM：当前事务的热上下文；
- Scene：`data/memory/chat-api/<用户>/scene/<thread_id>.jsonl`；
- Dialogue：Flush 形成的对话归档；
- Episodic index：`data/memory/chat-api/<用户>/episodic/`；
- Runtime / checkpoint / journal：负责事务、刺激、Effect 和 Flush 的恢复基础。

详细说明见[可插拔子系统指南](docs/systems-plugin/README.zh-CN.md)和[当前 Runtime 文档](docs/runtime/README.md)。

## 测试

```bash
pytest
```

Runtime 语义验收：

```powershell
python -m m_agent.acceptance contract run --runtime langgraph_v1 --all-layers
python scripts/run_runtime_migration_gate.py --rounds 3
```

详见 [Runtime 语义验收平台](docs/runtime/semantic-acceptance-platform.zh-CN.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [愿景与目标](docs/vision-and-goals.zh-CN.md) | 项目定位、用户价值、系统记忆和成功判据 |
| [版本规划](docs/roadmap-v0.2.0-v1.0.0.zh-CN.md) | v0.2.0—v1.0.0 的目标、交付与 Gate |
| [路线图 PDF](docs/pdf/M-Agent-Roadmap-v0.2.0-v1.0.0.zh-CN.pdf) | 可直接分发的正式版本规划 |
| [认知运行时目标架构](docs/architecture/cognitive-runtime-architecture.zh-CN.md) | Signal 到 Effect Feedback 的目标链路 |
| [当前 Runtime](docs/runtime/README.md) | v0.2.0 当前实现事实 |
| [Chat API](docs/chat_api/README.md) | HTTP / SSE 接口参考 |
| [子系统插件](docs/systems-plugin/README.zh-CN.md) | WM、Episodic 与 Tools 当前插件接口 |
| [文档总索引](docs/README.md) | 活动文档、目标设计与历史归档入口 |

## 仓库结构

源码位于 `src/m_agent/`，配置位于 `config/`，测试位于 `tests/`，脚本位于 `scripts/`，参考客户端位于 `tools/`。详见[项目结构](docs/development/project-structure.md)。

## 许可证

M-Agent 使用 [MIT License](LICENSE)。

**English:** [README.md](README.md)
