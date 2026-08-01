# 当前 Runtime 文档

本目录记录已经存在的 `think_life_v1` 行为和验收工具。

当前为 **post-P2 Transaction Foundation**：共享合同仍有 35 个 `Executable`
variant，当前验收结果为 9 `Passed` / 26 `Registered Known Gaps`。

- [Think-life 当前实现规格](think-life-runtime-spec.zh-CN.md)：post-P2 已实现的
  transaction、activation/delegate、SQLite 持久化与仍待接线的生产边界。
- [Runtime 语义验收平台](semantic-acceptance-platform.zh-CN.md)：当前合同口径、
  CLI、Web UI、结构化证据和 P2 Gate。
- [ThinkLife P1 Known Gap 矩阵](think-life-p1-gap-matrix.zh-CN.md)：冻结的
  **2026-07-28 pre-P2 历史基线**；表内 35 个 gap 不代表当前状态。

当前 Gate：

```powershell
python -m pytest -q -m p2_foundation
python -m m_agent.acceptance contract run --runtime think_life_v1 --scenario TX-01 --scenario TX-04 --scenario TX-09 --scenario TX-10 --scenario AT-05 --scenario AT-06 --scenario AT-07 --all-layers
```

未来目标语义和迁移计划见 [`../architecture/`](../architecture/README.md)。当前实现与目标设计
不一致时，应通过验收场景标记差异，不能静默修改任一侧的含义。
