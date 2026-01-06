# Memory数据可视化界面

这是一个用于显示 `@/data/memory/` 目录中dialogues、episodes和scenes信息的实时监控可视化界面。

## 功能特性

### 核心功能
1. **实时监控统计**：
   - 显示当前有多少个原始信息dialogue
   - 显示当前有多少个episode
   - 显示当前有多少个scene
   - 实时更新数据变化

2. **数据查看**：
   - 查看dialogues的具体内容（完整对话）
   - 查看episodes的原内容及评分信息
   - 查看scenes的详细信息

3. **可视化展示**：
   - 统计卡片显示各类数据数量
   - 用户分布图表
   - 评分分布分析
   - 实时更新日志

### 技术特性
- **实时更新**：通过WebSocket实现数据实时更新
- **文件监控**：监控data/memory目录变化，自动刷新数据
- **响应式设计**：适配不同屏幕尺寸
- **搜索功能**：支持按ID、用户、内容搜索
- **标签页导航**：Dialogues、Episodes、Scenes分页查看

## 系统架构

### 目录结构
```
memory_visualization/
├── backend/              # 后端服务
│   ├── data_loader.py   # 数据加载器
│   ├── file_watcher.py  # 文件监控器
│   └── main.py          # FastAPI主应用
├── frontend/            # 前端界面
│   ├── index.html       # 主页面
│   ├── style.css        # 样式文件
│   └── app.js           # 前端逻辑
├── requirements.txt     # Python依赖
└── start.bat           # 启动脚本
```

### 数据流
1. 后端加载 `data/memory/` 目录下的JSON文件
2. 通过REST API提供数据访问
3. 前端通过WebSocket接收实时更新
4. 文件监控器检测到文件变化时通知前端刷新

## 快速开始

### 1. 安装依赖
```bash
cd memory_visualization
pip install -r requirements.txt
```

### 2. 启动后端服务
```bash
# Windows
start.bat

# 或手动启动
cd backend
python main.py
```

### 3. 访问界面
打开浏览器访问：http://localhost:8001/frontend/index.html

## API接口

### REST API
- `GET /api/stats` - 获取统计信息
- `GET /api/dialogues` - 获取所有dialogues
- `GET /api/episodes` - 获取所有episodes
- `GET /api/scenes` - 获取所有scenes
- `GET /api/dialogue/{id}` - 获取特定dialogue详情
- `GET /api/scene/{id}` - 获取特定scene详情

### WebSocket
- `WS /ws` - 实时数据更新

## 数据格式

### Dialogue
```json
{
  "dialogue_id": "dlg_2025-11-17_19-04-53",
  "user_id": "ZQR",
  "start_time": "2025-11-17T19:04:53",
  "end_time": "2025-11-17T19:05:58",
  "turn_count": 14,
  "turns": [...]
}
```

### Episode
```json
{
  "episode_id": "ep_001",
  "dialogue_id": "dlg_2025-11-17_19-04-53",
  "turn_span": [0, 7],
  "segmentation_reason": []
}
```

### Qualification (评分)
```json
{
  "episode_id": "ep_001",
  "scene_potential_score": {
    "information_density": 2,
    "novelty": 1,
    "total": 3
  },
  "decision": "scene_candidate",
  "rationale": {...}
}
```

### Scene
```json
{
  "scene_id": "scene_000001",
  "user_id": "ZQR",
  "scene_type": "other",
  "diary": "I talked with ZQR about...",
  "intent": "respond_to_ZQR_about_exam_preparation",
  "confidence": 0.9
}
```

## 使用说明

### 1. 查看统计概览
- 首页显示dialogues、episodes、scenes的数量统计
- 实时显示连接状态和最后更新时间

### 2. 浏览数据
- 使用标签页切换Dialogues、Episodes、Scenes
- 点击左侧列表项查看详情
- 右侧面板显示选中项的详细信息

### 3. 搜索功能
- 在搜索框输入ID或关键词
- 按Enter或点击搜索按钮
- 结果实时筛选显示

### 4. 实时监控
- 系统自动监控data/memory目录变化
- 文件变化时自动刷新数据
- 更新日志显示实时变化记录

## 配置说明

### 数据目录
默认数据目录：`项目根目录/data/memory/`

如需修改数据目录，可编辑 `backend/data_loader.py` 中的 `data_dir` 参数。

### 端口配置
默认端口：8001

如需修改端口，可编辑 `backend/main.py` 中的 `uvicorn.run` 参数。

## 故障排除

### 常见问题

1. **后端启动失败**
   - 检查Python版本（需要Python 3.7+）
   - 检查依赖是否安装：`pip install -r requirements.txt`
   - 检查端口8001是否被占用

2. **前端无法连接**
   - 检查后端服务是否运行
   - 检查浏览器控制台错误信息
   - 检查CORS配置

3. **数据加载失败**
   - 检查data/memory目录是否存在
   - 检查JSON文件格式是否正确
   - 查看后端日志获取详细错误信息

### 日志查看
后端日志输出到控制台，包含：
- 数据加载状态
- 文件监控事件
- WebSocket连接状态
- API请求记录

## 开发说明

### 扩展功能
1. **添加新图表**：在 `app.js` 中扩展Chart.js配置
2. **添加新过滤**：在控制面板添加过滤条件
3. **导出功能**：实现数据导出为CSV/JSON
4. **批量操作**：添加批量查看/删除功能

### 性能优化
- 大数据集分页加载
- 图表数据聚合显示
- 前端缓存优化
- WebSocket连接管理

## 许可证

本项目基于MIT许可证开源。

## 贡献指南

欢迎提交Issue和Pull Request来改进本项目。