# Chat Controller Capabilities

## 中文说明

这个目录负责顶层 `chat` 主控 Agent 的工具适配层。

这里的模块应该只做三件事：

1. 定义 capability 的名字与注册方式。
2. 把顶层工具调用适配到项目里已有的真实实现。
3. 统一顶层工具的 trace、history 记录和结果格式。

不要在这里重复实现业务逻辑。
真实能力应该继续留在原有模块里，例如：

- 时间相关能力放在共享工具模块里。
- 记忆召回能力继续调用 `MemoryAgent`。
- 其他业务能力也应该调用它们各自原本所属的模块。

### 新增一个顶层工具时，推荐按下面步骤做

1. 先确认真实实现已经存在。
2. 在这个目录下新增一个 capability 适配文件，或者放进合适的现有文件。
3. 在适配层里只做“参数整理 + 调已有实现 + trace/hisory 记录”。
4. 在 `registry.py` 里注册新的 `ControllerCapabilitySpec`。
5. 在 `config/agents/chat/runtime/chat_controller_runtime.yaml` 里补这个工具的中英文描述。
6. 如果需要默认参数，在 `config/agents/chat/chat_controller.yaml` 的 `tool_defaults` 里配置。
7. 把工具名加入 `enabled_tools`，或者让它通过配置按需启用。

### capability 适配层应保持的约定

- tool name 要稳定，改名会影响配置和 prompt。
- description 不要硬编码在业务实现里，优先从 runtime prompt 读取。
- 输入参数要尽量薄，只负责把顶层 Agent 的调用转发给真实实现。
- 顶层 trace 要保持统一，方便 SSE / Thinking 面板复用。
- 如果工具调用结果要回写到 `agent_result`，要通过 `controller_state["history"]` 统一记录。

### 一个好的 capability 适配层应该像什么

- 薄：不重复实现下游业务。
- 稳：参数、日志、history 结构统一。
- 可配置：描述和默认参数来自配置。
- 可插拔：新增工具只需要注册，不需要重写主控 Agent。

## English Guide

This directory is the capability adapter layer for the top-level `chat` controller agent.

Modules here should only do three things:

1. Define capability names and registration.
2. Adapt top-level tool calls to the real implementations that already exist elsewhere in the project.
3. Standardize trace logging, history recording, and result shape for top-level tools.

Do not re-implement business logic here.
Real capabilities should remain in their original modules, for example:

- Time-related logic should stay in shared utility modules.
- Memory recall should continue to call `MemoryAgent`.
- Other business capabilities should call the modules that already own them.

### Recommended steps for adding a new top-level tool

1. First confirm that the real implementation already exists.
2. Add a new capability adapter file in this directory, or place it in an appropriate existing file.
3. Keep the adapter thin: normalize params, call the existing implementation, and record trace/history.
4. Register the new `ControllerCapabilitySpec` in `registry.py`.
5. Add bilingual tool descriptions in `config/agents/chat/runtime/chat_controller_runtime.yaml`.
6. If default parameters are needed, configure them in `tool_defaults` inside `config/agents/chat/chat_controller.yaml`.
7. Add the tool name to `enabled_tools`, or let it be enabled through config when needed.

### Conventions that capability adapters should keep

- Tool names should stay stable; renaming them affects config and prompts.
- Descriptions should not be hard-coded in business implementations; prefer runtime prompt config.
- Input parameters should stay thin and only forward the call to the real implementation.
- Top-level trace format should remain consistent so SSE and the Thinking panel can reuse it.
- If tool usage should be reflected in `agent_result`, record it through `controller_state["history"]`.

### What a good capability adapter should look like

- Thin: it does not duplicate downstream business logic.
- Stable: params, logs, and history shapes are consistent.
- Configurable: descriptions and defaults come from config.
- Pluggable: adding a tool should mainly be a registration task, not a controller rewrite.
