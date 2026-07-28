# `m_agent.systems`

Pluggable chat subsystems. **Index:** [docs/systems-plugin/README.md](../../../docs/systems-plugin/README.md)

**Subsystem guides (6 files):** [docs/systems-plugin/](../../../docs/systems-plugin/)

| Subsystem | 中文 | English |
|-----------|------|---------|
| WM | [wm.zh-CN.md](../../../docs/systems-plugin/wm.zh-CN.md) | [wm.md](../../../docs/systems-plugin/wm.md) |
| Episodic | [episodic.zh-CN.md](../../../docs/systems-plugin/episodic.zh-CN.md) | [episodic.md](../../../docs/systems-plugin/episodic.md) |
| Tools | [tools.zh-CN.md](../../../docs/systems-plugin/tools.zh-CN.md) | [tools.md](../../../docs/systems-plugin/tools.md) |

**Config:** [config/systems/README.md](../../../config/systems/README.md)

```python
from m_agent.systems import (
    SystemsBundle,
    load_systems_bundle_from_config,
    WMSystem,
    EpisodicMemorySystem,
    ToolSuiteSystem,
)
```
