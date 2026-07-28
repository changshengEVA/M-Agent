# `m_agent.systems`

对话栈可插拔子系统。**总索引：** [docs/systems-plugin/README.zh-CN.md](../../../docs/systems-plugin/README.zh-CN.md)

**子系统专题（6 篇）：** [docs/systems-plugin/](../../../docs/systems-plugin/)

| 子系统 | 中文 | English |
|--------|------|---------|
| WM | [wm.zh-CN.md](../../../docs/systems-plugin/wm.zh-CN.md) | [wm.md](../../../docs/systems-plugin/wm.md) |
| 情景记忆 | [episodic.zh-CN.md](../../../docs/systems-plugin/episodic.zh-CN.md) | [episodic.md](../../../docs/systems-plugin/episodic.md) |
| 工具 | [tools.zh-CN.md](../../../docs/systems-plugin/tools.zh-CN.md) | [tools.md](../../../docs/systems-plugin/tools.md) |

**配置目录：** [config/systems/README.md](../../../config/systems/README.md)

```python
from m_agent.systems import (
    SystemsBundle,
    load_systems_bundle_from_config,
    WMSystem,
    EpisodicMemorySystem,
    ToolSuiteSystem,
)
```
