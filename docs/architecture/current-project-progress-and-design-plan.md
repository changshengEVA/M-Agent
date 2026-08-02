# 当前项目进度与设计

> 状态快照：2026-08-02
> 产品 Runtime 已收敛为单个 LangGraph 宿主，运行时标识固定为 `langgraph_v1`。

## 当前结构

```text
Chat API / Schedule heartbeat
            │
            ▼
      RuntimeHost
            │
            ▼
   LangGraphRuntime
      ├─ shared domain / transaction / perception / dispatch
      ├─ turn graph + transaction graph
      ├─ SQLite checkpoint
      └─ flush journal → Scene / Dialogue / RAG
```

RuntimeHost 是产品唯一入口。共享领域模块不依赖图框架，LangGraph 层负责节点编排、checkpoint 和恢复。Transaction store 保持任务状态、revision、WM、delegate、effect 与刺激处置的权威记录。

## 已完成

- 配置收敛到 `runtime.common` 与 `runtime.langgraph`。
- 能力上下文使用 `runtime_hooks`，controller state 使用中性 `runtime` 键。
- Chat API、Schedule heartbeat 与健康检查只依赖 RuntimeHost。
- Flush 对外字段为 `runtime_flush`，事务查询入口为 `get_transactions()`。
- Scene、Dialogue/RAG materialization 和 flush journal 支持幂等重试及重启恢复。
- Acceptance 仅提供 `langgraph_v1`，38/38 variant 可执行。
- CLI、Acceptance Web UI、测试 fixture 与 smoke gate 均使用单 Runtime 路径。
- 未支持的 Runtime 配置在启动时明确报错，不进行隐式转换。

## 当前 Gate

```powershell
python -m pytest
python -m m_agent.acceptance contract run --runtime langgraph_v1 --all-layers
python scripts/run_runtime_migration_gate.py --rounds 3
```

持久化部署必须使用 SQLite checkpointer，并通过 checkpoint schema、flush journal、Scene append identity、effect ledger 与 schedule delivery 的重启测试。生产数据操作仍应先执行只读审计和可恢复备份，不能用代码升级替代数据确认。

## 文档边界

- [总体设计架构](overall-design-architecture.md)定义稳定领域职责。
- [当前 Runtime 文档](../runtime/README.md)说明运行与验收入口。
- [Chat API 参考](../chat_api/README.md)定义当前 HTTP/SSE 协议。
- [历史归档](archive/README.md)保存已经完成的规格、差距矩阵和迁移快照；归档不定义当前行为。
