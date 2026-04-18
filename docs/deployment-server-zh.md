# M-Agent 服务器部署说明

本文档整理当前项目在阿里云 Ubuntu 24.04 服务器上的一套已验证部署方案，目标是先稳定跑通：

- 前端：`tools/M-Agent-UI`
- 后端：`python -m m_agent.api.chat_api`
- 图数据库：Neo4j Community
- 进程管理：`systemd`
- 反向代理：`nginx`

本文档偏向“单机可用、先稳定上线”的路线，不追求一开始就做多实例、高可用或容器编排。

---

## 1. 当前推荐架构

当前建议采用单机部署：

1. `nginx` 对外提供 `80/443`
2. 前端静态资源由 `nginx` 托管
3. Python Chat API 监听 `127.0.0.1:8777`
4. Neo4j 监听本机 `127.0.0.1:7687`
5. 仅对外开放 `22/80/443`

不建议直接暴露：

- `8777`
- `7687`

说明：

- 目前聊天运行态、SSE 事件、线程状态存在进程内状态，不适合一开始就做多实例横向扩容。
- Neo4j 使用 Community 版时，动态 `CREATE DATABASE` 不可用，代码会自动 fallback 到默认库。

---

## 2. 部署前提

### 2.1 推荐分支

当前部署流程基于：

- `build-memory` 分支

服务器拉取分支：

```bash
cd ~/apps/M-Agent
git fetch origin
git checkout -b build-memory origin/build-memory || git checkout build-memory
git pull --recurse-submodules
git submodule update --init --recursive
```

### 2.2 服务器环境

推荐环境：

- Ubuntu 24.04
- Python 3.11
- Neo4j 5 Community
- Node.js 20

说明：

- 不建议直接使用 Ubuntu 24.04 系统自带的 Python 3.12 来复现当前本地环境。
- 当前本地可运行环境更接近 Python 3.10/3.11。

---

## 3. 代码获取

首次克隆：

```bash
mkdir -p ~/apps
cd ~/apps
git clone --recurse-submodules https://github.com/changshengEVA/M-Agent.git M-Agent
cd M-Agent
git submodule update --init --recursive
```

如果仓库已经拉下来，只需更新：

```bash
cd ~/apps/M-Agent
git pull --recurse-submodules
git submodule update --init --recursive
```

---

## 4. Python 环境

### 4.1 安装 Python 3.11

Ubuntu 24.04 上推荐通过 `deadsnakes` 安装：

```bash
sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev
```

### 4.2 创建 venv

```bash
cd ~/apps/M-Agent
python3.11 -m venv .venv
source .venv/bin/activate
python --version
```

期望输出为 `Python 3.11.x`。

### 4.3 安装依赖

本项目增加了一个更适合服务器的锁定依赖文件：

- `requirements-server-lock.txt`

安装方式：

```bash
cd ~/apps/M-Agent
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements-server-lock.txt
```

### 4.4 已知依赖坑

当前最容易踩的问题是 `langgraph` 相关传递依赖漂移，已知可用组合是：

```text
langchain==1.2.10
langchain-core==1.2.16
langgraph==1.0.10
langgraph-checkpoint==4.0.1
langgraph-prebuilt==1.0.8
langgraph-sdk==0.3.9
fastapi==0.104.1
anyio==3.7.1
httpx==0.27.0
```

如果遇到导入错误或 `pip` 漂移，可强制修复：

```bash
pip install --force-reinstall \
  "langchain==1.2.10" \
  "langchain-core==1.2.16" \
  "langgraph==1.0.10" \
  "langgraph-checkpoint==4.0.1" \
  "langgraph-prebuilt==1.0.8" \
  "langgraph-sdk==0.3.9" \
  "fastapi==0.104.1" \
  "anyio==3.7.1" \
  "httpx==0.27.0" \
  "requests==2.32.5" \
  "PyYAML==6.0.2"
```

校验：

```bash
pip check
```

---

## 5. Neo4j 部署

### 5.1 安装

```bash
sudo apt update
sudo apt install -y openjdk-17-jre-headless wget gnupg
wget -O - https://debian.neo4j.com/neotechnology.gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/neo4j.gpg
echo "deb [signed-by=/usr/share/keyrings/neo4j.gpg] https://debian.neo4j.com stable 5" | sudo tee /etc/apt/sources.list.d/neo4j.list
sudo apt update
sudo apt install -y neo4j
sudo systemctl enable --now neo4j
```

### 5.2 验证

```bash
sudo systemctl status neo4j --no-pager
```

### 5.3 修改密码

验证密码可用：

```bash
cypher-shell -u neo4j -p '你的密码' "RETURN 1;"
```

### 5.4 项目连接配置

编辑：

- `config/integrations/neo4j.yaml`

示例：

```yaml
url: "bolt://127.0.0.1:7687"
user_name: "neo4j"
password: "你的Neo4j密码"
database_template: "wf-{workflow_id}"
```

### 5.5 Community 版警告说明

日志中如果出现：

```text
CREATE DATABASE is unsupported on this Neo4j server; fallback to default database.
```

说明：

- 当前 Neo4j Community 不支持动态建数据库
- 程序会退回默认数据库继续运行
- 单机测试或小规模使用可接受
- 如果未来需要严格多 workflow / 多用户隔离，需要再设计数据库隔离策略

---

## 6. 项目环境变量

在项目根目录创建：

- `.env`

示例：

```dotenv
API_SECRET_KEY=你的兼容OpenAI接口Key
OPENAI_API_KEY=你的兼容OpenAI接口Key
BASE_URL=https://你的兼容接口/v1
DEEPSEEK_API_KEY=你的DeepSeek Key
ALIBABA_API_KEY=你的阿里云Embedding Key
ALIBABA_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ALIBABA_EMBED_MODEL=text-embedding-v4
LANGUAGE=zh
EMBED_PROVIDER=aliyun
LLM_PROVIDER=deepseek
```

注意：

- `.env` 不要提交到 Git 仓库
- 密钥不要复用仓库历史里曾暴露过的值

---

## 7. Gmail Secrets

如果需要启用邮件工具，服务器上需要存在：

```text
~/apps/M-Agent/.secrets/gmail/client_secret.json
~/apps/M-Agent/.secrets/gmail/token.json
```

权限建议：

```bash
chmod 700 ~/apps/M-Agent/.secrets
chmod 700 ~/apps/M-Agent/.secrets/gmail
chmod 600 ~/apps/M-Agent/.secrets/gmail/client_secret.json
chmod 600 ~/apps/M-Agent/.secrets/gmail/token.json
```

邮件配置文件：

- `config/agents/email/gmail_email_agent.yaml`

服务端更推荐：

```yaml
oauth:
  allow_local_webserver_flow: false
  allow_console_flow: true
```

说明：

- 如果 `token.json` 已经可用，通常直接迁移到服务器即可。
- 如果 token 失效，服务端更适合使用 console flow，而不是本地浏览器回调。

---

## 8. 启动后端

手动启动：

```bash
cd ~/apps/M-Agent
source .venv/bin/activate
export PYTHONPATH=$PWD/src
python -m m_agent.api.chat_api \
  --host 127.0.0.1 \
  --port 8777 \
  --config config/agents/chat/chat_controller.yaml \
  --idle-flush-seconds 6000 \
  --history-max-rounds 12
```

健康检查：

```bash
curl http://127.0.0.1:8777/healthz
```

---

## 9. systemd 常驻

服务文件：

- `/etc/systemd/system/m-agent.service`

示例：

```ini
[Unit]
Description=M-Agent Chat API
After=network.target neo4j.service
Wants=neo4j.service

[Service]
Type=simple
User=admin
WorkingDirectory=/home/admin/apps/M-Agent
Environment=PYTHONPATH=/home/admin/apps/M-Agent/src
ExecStart=/home/admin/apps/M-Agent/.venv/bin/python -m m_agent.api.chat_api --host 127.0.0.1 --port 8777 --config /home/admin/apps/M-Agent/config/agents/chat/chat_controller.yaml --idle-flush-seconds 6000 --history-max-rounds 12
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable m-agent
sudo systemctl start m-agent
```

检查：

```bash
sudo systemctl status m-agent --no-pager
journalctl -u m-agent -n 100 --no-pager
```

---

## 10. 前端构建

安装 Node.js 20 后：

```bash
cd ~/apps/M-Agent/tools/M-Agent-UI
npm install
VITE_AGENT_API_URL=http://你的公网IP npm run build
```

注意：

- 当前前端构建时会把 `VITE_AGENT_API_URL` 写入产物。
- 如果将来切域名，需要重新 build。

---

## 11. Nginx 配置

### 11.1 拷贝静态文件

```bash
sudo mkdir -p /var/www/m-agent
sudo rm -rf /var/www/m-agent/*
sudo cp -r ~/apps/M-Agent/tools/M-Agent-UI/dist/* /var/www/m-agent/
```

### 11.2 配置站点

文件：

- `/etc/nginx/sites-available/m-agent`

示例：

```nginx
server {
    listen 80;
    server_name _;

    root /var/www/m-agent;
    index index.html;

    location / {
        try_files $uri /index.html;
    }

    location /v1/ {
        proxy_pass http://127.0.0.1:8777;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_buffering off;
        proxy_cache off;
    }

    location /docs {
        proxy_pass http://127.0.0.1:8777;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }

    location /openapi.json {
        proxy_pass http://127.0.0.1:8777;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }

    location /healthz {
        proxy_pass http://127.0.0.1:8777;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }
}
```

启用：

```bash
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/m-agent /etc/nginx/sites-enabled/m-agent
sudo nginx -t
sudo systemctl restart nginx
sudo systemctl enable nginx
```

验证：

```bash
curl http://127.0.0.1/healthz
curl http://你的公网IP/healthz
```

---

## 12. 用户注册与实际生效配置

当前 Chat API 默认开启认证。

健康检查返回：

- `auth_required_for_chat: true`

因此：

1. 前端首次访问需要先注册用户
2. 注册后会生成用户态配置
3. 运行时往往优先使用用户态配置，而不是共享基线配置

相关目录：

- `config/users/<username>/chat.yaml`
- `config/users/<username>/runtime/chat_runtime.yaml`

重要说明：

- 如果你修改了 `config/agents/chat/chat_controller.yaml`
- 但前端登录的是某个已存在用户
- 实际生效的可能仍然是 `config/users/<username>/...`

这也是排查“为什么我改了共享配置但没生效”的第一优先项。

---

## 13. 工具乱调用问题与保守模式

### 13.1 问题现象

当前顶层控制器在一些闲聊场景里可能主动调用：

- `schedule_query`
- `email_ask`

甚至出现连续尝试多个工具的情况。

### 13.2 原因

主要原因有三类：

1. 用户态配置里仍然开启了邮箱/日程工具
2. prompt 中对“何时禁止用工具”的限制不够硬
3. 顶层 controller 代码没有全局每轮工具调用总量限制

### 13.3 推荐保守模式

在用户态配置里：

- `config/users/<username>/chat.yaml`

先缩成：

```yaml
enabled_tools:
  - shallow_recall
  - deep_recall
  - get_current_time
```

如果确认日程稳定，再加：

```yaml
  - schedule_manage
  - schedule_query
```

先不要开：

- `email_ask`
- `email_read`
- `email_send`

### 13.4 用户态 prompt 建议

在：

- `config/users/<username>/runtime/chat_runtime.yaml`

建议增加硬约束：

```yaml
global_tool_policy:
  zh: |
    [工具使用原则]
    - 非必要不使用工具。
    - 对于寒暄、闲聊、情绪回应、陪伴式对话，禁止调用任何工具，直接自然回复。
    - 只有当用户明确提到“邮件/邮箱/inbox/未读邮件/发邮件”时，才允许使用邮件工具。
    - 只有当用户明确提到“日程/提醒/安排/待办/几点/哪天”且是在查询或修改安排时，才允许使用日程工具。
    - 不要为了“主动帮助用户”而自行检查邮箱、日程或记忆。
    - 每轮最多调用 1 个顶层工具；只有当第一个工具结果明确不足以回答时，才允许调用第 2 个。
    - 如果工具返回空结果、无结果或不相关，不要继续尝试别的工具；直接说明没有查到，或向用户澄清。
```

---

## 14. 常用运维命令

### 14.1 查看后端状态

```bash
sudo systemctl status m-agent --no-pager
journalctl -u m-agent -n 100 --no-pager
```

### 14.2 重启

```bash
sudo systemctl restart m-agent
```

### 14.3 停止

```bash
sudo systemctl stop m-agent
```

### 14.4 如果 `systemctl stop` 卡住

先查 PID：

```bash
ps -ef | grep "m_agent.api.chat_api"
```

强杀：

```bash
sudo pkill -9 -f "m_agent.api.chat_api"
```

然后清理 systemd 状态：

```bash
sudo systemctl disable m-agent
sudo systemctl reset-failed m-agent
```

---

## 15. 当前已验证结果

这套流程已经验证通过的关键点：

1. `Python 3.11 + venv` 可正常运行后端
2. `requirements-server-lock.txt` 可安装出可用运行环境
3. Neo4j 本机连接可用
4. `systemd` 可托管 Chat API
5. `nginx` 可反向代理 `/v1`、`/docs`、`/openapi.json`、`/healthz`
6. 公网可访问：
   - `/`
   - `/healthz`
   - `/docs`

---

## 16. 后续建议

建议下一阶段继续做：

1. 绑定域名
2. 配置 HTTPS
3. 把前端 API 地址改成相对路径，避免每次换 IP 重新 build
4. 给顶层 controller 增加代码级工具调用上限
5. 明确 Neo4j Community 下的多用户/多 workflow 隔离策略

