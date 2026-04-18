# Project Structure

本次结构优化遵循 4 个原则：

1. 源码统一收敛到 `src/m_agent/`，避免业务模块散落在仓库根目录。
2. 所有可执行入口集中到 `scripts/`，让“源码”和“运行入口”职责分离。
3. 测试、示例、实验目录独立，避免和正式源码混放。
4. 项目根路径通过 `m_agent.paths` 统一管理，减少各模块自己计算 `parents[...]`。

当前推荐目录如下：

```text
M-Agent/
├─ src/m_agent/
│  ├─ agents/
│  ├─ load_data/
│  ├─ load_model/
│  ├─ memory/
│  ├─ pipeline/
│  └─ utils/
├─ scripts/
├─ tests/
├─ examples/
├─ experiments/
├─ config/
├─ data/
├─ docs/
└─ tools/
```

几个约定：

- `src/m_agent/paths.py` 是统一路径入口，项目根目录、`config/`、`data/`、`log/`、`model/` 都从这里取。
- `scripts/run_locomo/_bootstrap.py` 负责在直接运行 LoCoMo 脚本时补齐 `src/` 到 `sys.path`。
- `tests/conftest.py` 统一处理测试运行时的导入路径。
- `docs/analysis/` 存放分析型文档，避免 `.md` 文件继续堆在仓库根目录。

推荐的常用命令：

```bash
python scripts/run_locomo/import_locomo.py --env-config config/eval/memory_agent/locomo/test_env.yaml
python scripts/run_locomo/eval_locomo.py --env-config config/eval/memory_agent/locomo/test_env.yaml
python scripts/run_locomo/plot_locomo_scores.py --test-id quickstart
```
