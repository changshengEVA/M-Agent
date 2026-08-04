# Runtime 语义验收平台

状态：LangGraph-only。Catalog 固定提供 38 个 `langgraph_v1` variant，所有 variant 都有可执行 binding；CLI、Web API 和本地 UI 不提供其他 Runtime 选项。

## 覆盖范围

| 层级 | Variant 数 | 目的 |
| --- | ---: | --- |
| Core | 27 | TX、SP、AT 的稳定领域语义 |
| Robustness | 7 | 重启、租约、幂等与并发边界 |
| Matcher evaluation | 1 | 离线归因数据集与指标 |
| PoC | 3 | 图执行、顺序 effect 与恢复证据 |
| 合计 | 38 | LangGraph 全量合同 |

验收证据比较稳定领域结果，不比较随机 UUID、临时路径或内部图节点名称。场景实现复用中立的 AT/SP/TX harness，LangGraph adapter 只负责接入图引擎。

## CLI

```powershell
# 查看 catalog 与覆盖
python -m m_agent.acceptance contract list
python -m m_agent.acceptance contract coverage

# 运行全部层或指定场景
python -m m_agent.acceptance contract run --runtime langgraph_v1 --all-layers
python -m m_agent.acceptance contract run --runtime langgraph_v1 --scenario TX-01 --scenario SP-07

# 结构化结果
python -m m_agent.acceptance contract run --runtime langgraph_v1 --all-layers --format json
```

`--runtime` 仅接受 `langgraph_v1`，省略时也使用该值。未知值会在执行前被拒绝。

## 本地 Web UI

```powershell
python -m m_agent.acceptance ui --host 127.0.0.1 --port 8765
```

Web 运行请求采用结构化 scenario allowlist；结果按 `runtime_id=langgraph_v1` 持久化和查询。Artifact 路由只接受已登记的结果类型，不把任意路径暴露为下载端点。

## Gate

```powershell
python -m pytest tests/acceptance -q
python scripts/run_runtime_migration_gate.py --rounds 3
```

完成条件是 38/38 场景可执行且没有 mandatory xfail。早期平台说明和 gap 证据位于 [`../archive/architecture/`](../archive/architecture/README.md)，不属于当前 catalog。
