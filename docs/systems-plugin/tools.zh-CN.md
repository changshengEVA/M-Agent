# Tools 子系统——插件指南

> English: [tools.md](./tools.md) · 总索引：[README.zh-CN.md](./README.zh-CN.md)

## 职责

Tools 子系统向执行层暴露 LangChain capability。默认工具集采用**每个工具一个 YAML 清单**，使注册、提示词、默认参数、参数路由和集成元数据集中在一个位置。

## 目录结构

```text
config/systems/tools/
├── default.yaml                 # 工具集组合及跨工具策略
└── capabilities/
    ├── web_search.yaml          # 一个工具的一份完整描述
    ├── schedule_create.yaml
    └── ...
```

Chat controller 仍然只挂载一个工具集：

```yaml
systems:
  tools: ../../systems/tools/default.yaml
```

`default.yaml` 指向清单目录，并控制启用白名单：

```yaml
system: tools
capabilities_dir: ./capabilities
enabled: [reply_to_user, get_current_time, web_search]
defaults:
  __controller__:
    max_calls_per_turn: 12
```

如果省略 `enabled`，则启用 `capabilities_dir` 下的全部清单。旧版第三方工具集仍可继续使用 `registry` 和 `runtime_descriptions_path`。

## 工具清单

每份清单可包含以下字段：

| 字段 | 运行时含义 |
|---|---|
| `name` | 规划、调用、日志和记忆共同使用的稳定工具名 |
| `version` | 大于零的契约版本 |
| `category` | capability descriptor 对外暴露的分类 |
| `builder` | 构建 LangChain 工具的可导入 callable；路径错误会在启动时失败 |
| `descriptions` | 注入思考层 capability 列表的多语言描述 |
| `input.mode` | `param_llm`、`instruction_arg`、`no_args` 或 `reply` |
| `input.instruction_arg` | 直接映射思考层 instruction 时使用的参数名 |
| `input.schema` | 输入 schema 来源；`inferred_from_tool` 表示从 LangChain 工具推导 |
| `output.*` | 保存在 `ControllerCapabilitySpec` 上的输出契约和投影元数据 |
| `policy.max_calls_per_turn` | 通过现有默认参数和限流机制执行 |
| `policy.side_effect` | 审查与安全元数据，如 `read`、`write`、`send` |
| `dependencies` | 所需后端、服务或认证资源 |
| `defaults` | 该工具的默认运行参数 |

目前 `side_effect`、`dependencies` 和输出 projector 路径属于描述性元数据。工具仍需通过 `ControllerCapabilityContext` 获取依赖；复杂结果仍需在 `src/m_agent/chat/working_memory.py` 中接入专用投影。

## 参数模式

| 模式 | 行为 |
|---|---|
| `param_llm` | 从工具 schema 生成结构化参数 |
| `instruction_arg` | 将思考层 instruction 直接写入指定参数 |
| `no_args` | 使用 `{}` 调用工具 |
| `reply` | 直接构建 `reply_to_user` 参数，不运行参数 LLM |

通过 `capabilities_dir` 加载的工具集以清单为准。旧的跳过参数表只作为程序化 legacy registry 的兼容回退。

## 新增工具

1. 在 `src/m_agent/systems/tools/default/capabilities/` 或其他可导入包中实现 builder。
2. 在 `config/systems/tools/capabilities/` 下新增且只新增一份工具清单。
3. 如果工具集使用显式白名单，将工具名加入 `default.yaml` 的 `enabled`。
4. 按需增加依赖注入、工作记忆投影和专用执行反馈。
5. 测试清单加载、参数路由、调用、限流和记忆投影。

```bash
pytest tests/systems/ tests/runtime/test_think_life_tool_args.py tests/test_chat_controller_tool_limits.py
```
