# 知识图谱实时可视化系统

## 概述
基于 `/data/memory/kg_candidates/strong` 目录下的知识图谱候选数据，构建一个实时可视化系统，支持在UI界面中实时观测节点和关系的变化，无需手动刷新。

## 功能特性

### 🎯 核心功能
- **实时可视化**: 使用vis.js展示知识图谱的节点和关系
- **实时更新**: 监控数据目录变化，自动更新可视化界面
- **交互式操作**: 支持节点点击、缩放、拖拽、搜索等交互
- **统计分析**: 显示实体类型分布、置信度统计等信息
- **过滤搜索**: 支持按类型、置信度过滤，按ID搜索节点

### 🔄 实时更新机制
- **文件系统监控**: 使用watchdog监控数据目录变化
- **WebSocket推送**: 检测到变化时通过WebSocket实时推送更新
- **增量更新**: 前端动态更新变化的节点和关系

### 📊 数据展示
- **节点可视化**: 不同类型节点使用不同颜色标识
- **关系可视化**: 带标签的有向边表示关系
- **详细信息**: 点击节点或边显示详细信息
- **统计图表**: 实体类型分布饼图

## 系统架构

### 组件说明
```
kg_visualization/
├── backend/                    # 后端服务
│   ├── main.py                # FastAPI主应用
│   ├── data_loader.py         # 数据加载和解析
│   └── file_watcher.py        # 文件系统监控
├── frontend/                  # 前端界面
│   ├── index.html            # 主页面
│   ├── style.css             # 样式表
│   └── app.js                # 前端逻辑
├── requirements.txt           # Python依赖
├── start.bat                 # Windows启动脚本
├── start.sh                  # Linux/Mac启动脚本
├── test_backend.py           # 测试脚本
└── README.md                 # 本文档
```

### 技术栈
- **后端**: Python 3.9+, FastAPI, WebSocket, watchdog
- **前端**: HTML5, CSS3, JavaScript, vis.js, Chart.js
- **数据格式**: JSON
- **实时通信**: WebSocket

## 快速开始

### 环境要求
- Python 3.9+
- pip 包管理器
- 现代浏览器（Chrome/Firefox/Edge）

### 安装步骤

1. **克隆或复制项目**
   ```bash
   # 确保在项目根目录 f:/AI/M-Agent
   ```

2. **安装依赖**
   ```bash
   cd kg_visualization
   pip install -r requirements.txt
   ```

3. **启动系统**
   - **Windows**: 双击 `start.bat` 或运行:
     ```bash
     cd kg_visualization
     start.bat
     ```
   
   - **Linux/Mac**:
     ```bash
     cd kg_visualization
     chmod +x start.sh
     ./start.sh
     ```

4. **手动启动（可选）**
   ```bash
   cd kg_visualization/backend
   python main.py
   ```

### 访问系统
1. 系统启动后，打开浏览器访问:
   ```
   http://localhost:8000/frontend/index.html
   ```

2. 或者访问API文档:
   ```
   http://localhost:8000
   ```

## 使用指南

### 界面说明
1. **左侧面板**
   - **统计信息**: 显示实体、关系、场景数量
   - **控制面板**: 过滤、搜索、刷新控制
   - **更新日志**: 实时显示系统更新事件

2. **可视化区域**
   - **知识图谱**: 交互式图可视化
   - **图控制**: 物理引擎、标签显示开关
   - **选中信息**: 显示点击的节点或边详情
   - **图例**: 节点类型颜色说明

### 交互操作
- **点击节点**: 显示节点详细信息
- **双击节点**: 聚焦并放大该节点
- **拖拽画布**: 平移视图
- **鼠标滚轮**: 缩放视图
- **搜索节点**: 在搜索框输入节点ID

### 过滤功能
1. **置信度过滤**: 使用滑块过滤低置信度节点和关系
2. **类型过滤**: 在多选框中选择要显示的实体类型
3. **实时更新**: 系统自动检测数据变化并更新

## API接口

### REST API
- `GET /` - 欢迎页面和API文档
- `GET /api/nodes` - 获取所有实体节点
- `GET /api/edges` - 获取所有关系边
- `GET /api/scenes` - 获取所有scene信息
- `GET /api/stats` - 获取统计信息
- `GET /api/graph` - 获取图数据（用于可视化）
- `GET /api/entity/{id}` - 获取特定实体信息

### WebSocket
- `WS /ws` - 实时更新连接
  - 连接后接收初始数据
  - 数据变化时接收更新通知

## 数据格式

### 输入数据
系统从以下目录读取数据:
```
data/memory/kg_candidates/strong/*.kg_candidate.json
```

### 数据示例
```json
{
  "scene_id": "scene_000004",
  "user_id": "ZQR",
  "facts": {
    "entities": [
      {"id": "ZQR", "type": "person", "confidence": 1.0}
    ],
    "relations": [
      {"subject": "ZQR", "relation": "member_of", "object": "Peking_University", "confidence": 0.9}
    ]
  }
}
```

### 处理逻辑
1. **数据合并**: 合并所有scene中的实体和关系
2. **去重处理**: 相同ID的实体合并，保留最高置信度
3. **图构建**: 构建节点和边的图数据结构

## 实时更新机制

### 监控流程
1. **文件监控**: 监控数据目录的创建、修改、删除事件
2. **防抖处理**: 1秒内多次事件只处理一次
3. **数据重载**: 检测到变化时重新加载所有数据
4. **客户端通知**: 通过WebSocket推送更新消息

### 更新类型
- **文件创建**: 新增scene数据
- **文件修改**: scene数据更新
- **文件删除**: scene数据移除

## 测试验证

### 运行测试
```bash
cd kg_visualization
python test_backend.py
```

### 测试内容
1. **数据加载测试**: 验证能否正确加载KG候选数据
2. **文件监控测试**: 验证文件系统监控功能
3. **API端点测试**: 验证REST API是否正常工作

### 手动测试
1. 启动系统后，访问前端界面
2. 观察是否正常显示知识图谱
3. 测试搜索、过滤、交互功能
4. 修改数据目录中的JSON文件，观察实时更新

## 故障排除

### 常见问题

1. **服务无法启动**
   - 检查Python版本是否为3.9+
   - 检查依赖是否安装成功
   - 检查端口8000是否被占用

2. **数据加载失败**
   - 检查数据目录路径是否正确
   - 确认 `data/memory/kg_candidates/strong` 目录存在
   - 检查JSON文件格式是否正确

3. **前端无法访问**
   - 检查后端服务是否运行
   - 检查浏览器控制台错误信息
   - 确认网络连接正常

4. **实时更新不工作**
   - 检查文件监控权限
   - 查看后端日志输出
   - 检查WebSocket连接状态

### 日志查看
- 后端日志直接输出在控制台
- 前端日志在浏览器开发者工具中查看

## 扩展开发

### 添加新功能
1. **新可视化类型**: 修改 `frontend/app.js` 中的可视化逻辑
2. **新API端点**: 在 `backend/main.py` 中添加新的路由
3. **新数据处理**: 修改 `backend/data_loader.py` 中的数据处理逻辑

### 配置调整
- **数据目录**: 修改 `backend/main.py` 中的 `data_dir` 参数
- **服务端口**: 修改 `backend/main.py` 中的 `uvicorn.run` 端口
- **节点颜色**: 修改 `frontend/app.js` 中的 `NODE_COLORS` 映射

### 性能优化
- **大规模数据**: 实现分页加载和增量渲染
- **内存优化**: 优化数据结构和缓存策略
- **网络优化**: 压缩WebSocket消息和数据传输

## 部署说明

### 生产环境部署
1. **使用生产服务器**
   ```bash
   # 使用gunicorn或uvicorn生产配置
   cd kg_visualization/backend
   uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
   ```

2. **配置反向代理** (Nginx示例)
   ```nginx
   server {
       listen 80;
       server_name kg.example.com;
       
       location / {
           proxy_pass http://localhost:8000;
           proxy_http_version 1.1;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade";
       }
   }
   ```

3. **系统服务** (Systemd示例)
   ```ini
   [Unit]
   Description=KG Visualization Service
   After=network.target
   
   [Service]
   User=www-data
   WorkingDirectory=/path/to/kg_visualization/backend
   ExecStart=/usr/bin/python3 main.py
   Restart=always
   
   [Install]
   WantedBy=multi-user.target
   ```

### 安全考虑
1. **访问控制**: 生产环境应添加身份验证
2. **CORS配置**: 限制允许的源地址
3. **输入验证**: 验证API输入参数
4. **错误处理**: 避免泄露敏感信息

## 许可证
本项目基于MIT许可证开源。

## 贡献指南
欢迎提交Issue和Pull Request来改进本项目。

## 联系方式
如有问题或建议，请通过项目Issue跟踪系统反馈。