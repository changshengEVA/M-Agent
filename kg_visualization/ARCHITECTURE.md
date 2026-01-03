# 知识图谱实时可视化系统架构设计

## 概述
本系统用于实时可视化 `/data/memory/kg_candidates/strong` 目录下的知识图谱数据，支持节点和关系的实时更新观测。

## 系统架构

### 1. 数据层
- **数据源**：`data/memory/kg_candidates/strong/*.kg_candidate.json` 文件
- **数据结构**：
  ```json
  {
    "scene_id": "scene_000001",
    "user_id": "ZQR",
    "facts": {
      "entities": [
        {"id": "ZQR", "type": "person", "confidence": 1.0}
      ],
      "relations": [
        {"subject": "ZQR", "relation": "member_of", "object": "Peking_University", "confidence": 0.9}
      ],
      "attributes": []
    }
  }
  ```

### 2. 后端服务 (Python FastAPI)
- **API端点**：
  - `GET /api/nodes` - 获取所有节点
  - `GET /api/edges` - 获取所有关系
  - `GET /api/scenes` - 获取所有scene信息
  - `GET /api/stats` - 获取统计信息
  - `WS /ws` - WebSocket连接，用于实时更新

- **数据管理**：
  - 启动时加载所有JSON文件
  - 合并重复节点（基于ID）
  - 构建图数据结构
  - 文件系统监控（watchdog）检测新文件或修改

### 3. 前端界面 (HTML + JavaScript)
- **可视化库**：vis.js 或 D3.js
- **功能**：
  - 图可视化（节点和关系）
  - 节点点击显示详细信息
  - 实时更新动画
  - 过滤和搜索功能
  - 统计面板

### 4. 实时更新机制
- **轮询方式**：定期检查文件修改时间
- **推送方式**：WebSocket连接，后端检测到变化时推送更新
- **增量更新**：只发送变化的节点和关系

## 文件结构
```
kg_visualization/
├── backend/
│   ├── main.py              # FastAPI应用
│   ├── data_loader.py       # 数据加载和解析
│   ├── graph_manager.py     # 图数据管理
│   └── file_watcher.py      # 文件系统监控
├── frontend/
│   ├── index.html           # 主页面
│   ├── style.css            # 样式
│   ├── app.js               # 主应用逻辑
│   └── visualization.js     # 可视化组件
├── requirements.txt         # Python依赖
└── ARCHITECTURE.md          # 本文档
```

## 技术栈
- **后端**：Python 3.9+, FastAPI, WebSocket, watchdog
- **前端**：HTML5, CSS3, JavaScript, vis.js
- **数据格式**：JSON
- **实时通信**：WebSocket

## 实时更新流程
1. 用户打开前端页面，建立WebSocket连接
2. 后端监控 `data/memory/kg_candidates/strong` 目录
3. 当有新文件或文件修改时：
   - 解析新数据
   - 更新内存中的图数据结构
   - 通过WebSocket发送增量更新消息
4. 前端接收更新消息，动态更新可视化

## 扩展性考虑
- 支持大规模图数据的分页加载
- 节点和关系的类型过滤
- 置信度阈值过滤
- 时间线视图（按scene时间顺序）

## 部署
- 本地运行：`python backend/main.py`
- 前端通过浏览器访问 `http://localhost:8000`
- 可配置数据源路径