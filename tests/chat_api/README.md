# chat_api pytest 测试框架

## 分层
- `unit`：纯辅助函数与映射逻辑测试
- `integration`：基于 FastAPI 应用层的端点测试（使用 fake）
- `contract`：OpenAPI 与 schema 契约检查
- `e2e`：跨多个端点的完整请求流程测试

## 共享辅助模块
- `tests/fixtures/app_factory.py`：app/runtime/user-access 构建器
- `tests/fixtures/runtime_fakes.py`：fake runtime/schedule 实现
- `tests/fixtures/user_access_fakes.py`：fake 鉴权/用户 runtime 服务
- `tests/fixtures/payload_builders.py`：通用请求体构建器
- `tests/fixtures/sse_helpers.py`：SSE 解析辅助函数

## 测试功能索引（当前 13 条）
### unit
- `test_with_public_result_thread_id_rewrites_nested_thread_state`：验证公开线程 ID 映射会同步改写嵌套 `thread_state.thread_id`，且不修改原对象。
- `test_normalize_memory_mode_handles_case_and_fallback`：验证 memory mode 大小写归一化与非法值回退逻辑。
- `test_encode_sse_uses_id_event_data_lines`：验证 SSE 编码格式包含 `id/event/data` 且数据可反序列化。
- `test_trace_projector_maps_tool_call_payload`：验证日志投影器可把 TOOL CALL 日志映射为结构化事件。
- `test_trace_projector_ignores_unrecognized_messages`：验证未知日志不会被误识别为事件。

### integration
- `test_create_run_rejects_empty_message`：验证创建 run 时空消息返回 `400`。
- `test_run_lifecycle_snapshot_and_event_stream`：验证 run 创建、完成状态查询、SSE 事件流（started/message/completed）与结果一致。
- `test_thread_memory_mode_and_flush_flow`：验证线程 memory 状态查询、模式切换（含丢弃 pending）与 flush 行为。
- `test_schedule_crud_endpoints_with_fake_runtime`：验证 schedule 的创建、列表、更新、详情、取消、归档查询全流程。
- `test_chat_endpoints_require_token_when_auth_is_enabled`：验证开启鉴权后无 token 请求被拒绝（`401`）。
- `test_auth_register_login_and_run_visibility_isolated_by_user`：验证注册登录、用户 run 可见性隔离（不同用户不可互相访问）。

### contract
- `test_openapi_contains_core_chat_api_paths`：验证 OpenAPI 必含核心 chat 路径与 `ChatRunCreateRequest` 关键字段。

### e2e
- `test_anonymous_chat_run_memory_flush_and_schedule_flow`：验证匿名模式下 run、事件流、memory flush、schedule 创建/查询的一体化链路。

## 命令
```powershell
python -m pytest -q tests/chat_api
python -m pytest -q -m "unit or integration" tests/chat_api
python -m pytest -q -m contract tests/chat_api
python -m pytest -q -m e2e tests/chat_api
```

## 快速查看“测了什么”
```powershell
# 只收集用例，不执行（最直观的测试清单）
python -m pytest --collect-only -q tests/chat_api

# 执行时显示更详细的测试名
python -m pytest -vv tests/chat_api

# 只看某类功能（示例：schedule）
python -m pytest -q -k schedule tests/chat_api
```

## 可视化报告（可选）
`pytest` 默认没有内置图形界面；如需 HTML 报告可安装插件：

```powershell
pip install pytest-html
python -m pytest tests/chat_api --html=reports/chat_api_report.html --self-contained-html
```
