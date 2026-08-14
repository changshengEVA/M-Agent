# Email 与 arXiv Observation Monitors

这两个 Monitor 只负责被 Heartbeat 唤醒、读取外部增量并提交
`ObservationTriggerDelivery`。它们不定义 Runtime 如何归因、消费、回复或记忆。

## 启用

默认配置位于 `config/integrations/observation_monitors.yaml`，两个 Monitor
均默认关闭。修改订阅后，将对应的 `enabled` 设为 `true`，再通过正常的
`m-agent-chat` CLI 启动；CLI 会把已启用 Monitor 显式注入现有 Heartbeat。

也可指定其他配置：

```powershell
m-agent-chat --observation-monitors-config config/integrations/observation_monitors.yaml
```

嵌入式 `create_app()` 不会自动创建或联网。可用
`--disable-observation-monitors` 临时关闭所有外部 Monitor。

使用生产 CLI 且启用认证时，`advanced` 用户也可以在 M-Agent-UI 的
“Observation Monitors”页修改公开配置。页面通过
`GET/PUT /v1/users/me/settings/observation-monitors` 读取并应用设置；PUT 使用
revision/`If-Match` 防止覆盖其他会话的修改。服务端会先严格校验并构造整组候选
Monitor，等待当前 Heartbeat tick 结束后原子切换 Gmail 与 arXiv，再原子写入 YAML；
写入失败会恢复旧 Monitor。使用 `--disable-observation-monitors` 启动时只持久化，
响应会明确标记 `restart_required`。owner、状态路径和 provider 运维字段不由 UI
提交，owner 会绑定到执行应用操作的已认证用户。

游标和 staged delivery 默认写在
`M_AGENT_DATA_DIR/observation_monitors/`（未配置该环境变量时为
`data/observation_monitors/`）。配置目录不保存可变状态。

两个 Monitor 都采用“仅本次进程在线变化”语义。每个新 Monitor 实例第一次成功
访问外部源时，只持久化当时的当前基线，不产生 delivery；同一实例之后才从该基线
读取增量。进程停止期间发生的变化不会在重启后补偿。上一进程未确认的 Gmail
staged batch 或 arXiv pending outbox 也不会跨会话重放：新基线成功时会和旧待投递
状态一起原子替换；如果新基线访问失败，旧状态暂时保留用于诊断，但不会被新实例
投递，直到后续成功建立基线时清除。

通过设置页热应用 Monitor 配置同样会构造新实例，因此也会切一条新的当前基线；
不会把替换前的未确认队列当作补偿任务继续投递。

## Gmail

Gmail Monitor 使用现有 EmailAgent 配置中的只读 Gmail token，但会强制关闭
Heartbeat 内的浏览器和控制台 OAuth。请先通过交互式 Email 工具完成一次授权，
再启用 Monitor。

- `owner_ids` 必须明确填写且只能有一个 owner，防止同一邮箱被广播到其他用户。
- 每个新 Monitor 实例首次成功访问只记录当前 `historyId`，不会回放旧邮箱、停机
  窗口或上一进程未确认的 staged delivery。
- 后续读取 `messageAdded`，默认只取 metadata，不读取正文、HTML 或附件。
- `subject_keywords`、`sender_allowlist`、`sender_denylist` 是确定性本地过滤。
- Gmail message ID 构成稳定事件身份；有待投递事件的 cursor 只在对应
  `on_ingested` 后推进。纯过滤记录或无事件 high-water 可直接提交。
- history cursor 过期（HTTP 404）时从当前高水位重建基线，不做全邮箱回放。

若使用 wheel 安装，可安装 Gmail 可选依赖：

```powershell
pip install "m-agent[gmail]"
```

## arXiv

arXiv Monitor 使用官方 RSS/Atom feed，不新增第三方解析依赖。订阅必须明确指定：

- `owner_id`
- 一个或多个分类，如 `cs.AI`、`cs.CL`
- 可选 `keywords`、`exclude_keywords`、`announce_types`

过滤会对标题和摘要做 Unicode NFKC 与大小写归一化。论文身份按
`arXiv base ID + version` 去重；跨分类出现的同一 revision 只投递一次。
客户端使用 ETag/Last-Modified、响应大小上限和请求超时。收到完整 feed 后，
validators、seen frontier 与各 owner 的待投递队列会原子持久化；在同一进程会话
内，每个 delivery 的 `on_ingested` 只确认并移除自己的待投递项。某个 owner 暂时
不在线不会阻塞源轮询，也不会阻塞其他 owner 的投递，但进程重启会以当前完整 feed
重建 seen frontier，并清除上一会话尚未确认的 outbox。

默认每 6 小时检查一次，远低于 arXiv API/RSS 的频率限制。每个新实例首次成功
检查当前完整 feed 时只建立基线、不提交论文；后续 Heartbeat 每次最多提交
`max_deliveries_per_beat` 条在线增量。

每个 owner 的 durable outbox 由 `max_pending_per_owner` 设置硬上限，默认
2000 条且不得小于 `max_deliveries_per_beat`。达到上限后的新匹配不会静默消失：
checkpoint 会累计记录 overflow owner、数量、时间及最近 revision ID，同时输出
error 日志并把当次 Heartbeat 标记为 failed/degraded。该错误会在已到期的其他
owner delivery 处理之后报告，因此坏 owner 不会冻结源轮询或其他 owner。
运行中调低上限不会追溯删除已经持久化的 delivery；队列可暂时高于新上限，但在
排空到上限以下之前不会再接纳该 owner 的新匹配。

移除某个 owner 的最后一条启用订阅会清理其 pending outbox；若该 owner 仍有其他
启用订阅，已经入队的旧匹配会继续投递。

## 当前限制

- 两个 JSON checkpoint store 面向单进程 Heartbeat；多实例不得共享同一状态文件。
- Gmail Monitor 的 API 请求和 token refresh 默认使用 10 秒请求级硬超时。
- 交互式 Gmail OAuth 默认最多等待 300 秒；Heartbeat Monitor 永远禁用交互授权。
- Gmail cursor 过期重建基线时，失效窗口内的邮件可能漏检。
- arXiv 单个组合 feed 超过官方 2000 条上限时会 fail closed，不会截断后静默推进。
