# 三维知识图谱可视化系统部署指南

## 概述

本系统是一个基于FastAPI后端和Three.js前端的三维知识图谱可视化系统，支持实体-特征-场景三层结构可视化。本文档提供多种部署方案，以便在其他电脑上显示可视化结果。

## 系统架构

```
├── 后端 (FastAPI + Python)
│   ├── main_3d.py - 主应用
│   ├── enhanced_data_loader.py - 数据加载器
│   └── file_watcher.py - 文件监控
├── 前端 (Three.js + HTML/CSS/JS)
│   ├── 3d_frontend/ - 三维前端
│   └── frontend/ - 二维前端
└── 数据目录
    └── data/memory/{memory_id}/kg_data/
```

## 部署方案

### 方案1：局域网共享（最简单）

**适用场景**：在同一局域网内的其他电脑上查看

**步骤**：
1. 在主机上启动服务：
   ```bash
   cd kg_visualization
   python backend/main_3d.py
   ```
   或使用批处理文件：
   ```bash
   start_3d.bat
   ```

2. 获取主机IP地址：
   ```bash
   ipconfig  # Windows
   ifconfig  # Linux/Mac
   ```

3. 在其他电脑浏览器访问：
   ```
   http://[主机IP]:8001/3d_frontend/index.html
   ```

**配置要求**：
- 关闭防火墙或开放8001端口
- 确保所有电脑在同一网络

### 方案2：Docker容器化部署

**适用场景**：跨平台部署，环境隔离

**Dockerfile**：
```dockerfile
FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 复制数据目录
COPY data/memory/test2 /app/data/memory/test2

EXPOSE 8001

CMD ["python", "backend/main_3d.py"]
```

**部署步骤**：
1. 构建Docker镜像：
   ```bash
   docker build -t kg-visualization:latest .
   ```

2. 运行容器：
   ```bash
   docker run -d -p 8001:8001 --name kg-viz kg-visualization:latest
   ```

3. 访问服务：
   ```
   http://localhost:8001/3d_frontend/index.html
   ```

### 方案3：云服务器部署

**适用场景**：公网访问，远程协作

**推荐平台**：
- AWS EC2 / Lightsail
- Google Cloud Run
- Azure App Service
- 阿里云/腾讯云ECS

**部署步骤**：
1. 准备服务器（Ubuntu 20.04+）
2. 安装依赖：
   ```bash
   sudo apt update
   sudo apt install python3-pip python3-venv
   ```

3. 部署代码：
   ```bash
   git clone [你的仓库]
   cd M-Agent/kg_visualization
   pip3 install -r requirements.txt
   ```

4. 使用生产级服务器：
   ```bash
   # 使用gunicorn + uvicorn
   pip3 install gunicorn
   gunicorn -w 4 -k uvicorn.workers.UvicornWorker backend.main_3d:app
   ```

5. 配置Nginx反向代理：
   ```nginx
   server {
       listen 80;
       server_name your-domain.com;
       
       location / {
           proxy_pass http://127.0.0.1:8001;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
       }
   }
   ```

### 方案4：静态数据导出 + 离线查看

**适用场景**：无需实时更新，分享静态可视化

**步骤**：
1. 导出数据为静态JSON：
   ```python
   # 创建导出脚本 export_static.py
   from enhanced_data_loader import EnhancedKGDataLoader
   import json
   
   loader = EnhancedKGDataLoader(memory_id="test2")
   loader.load_all_data()
   
   data = loader.get_3d_graph_data()
   with open('static_kg_data.json', 'w', encoding='utf-8') as f:
       json.dump(data, f, ensure_ascii=False, indent=2)
   ```

2. 创建离线HTML文件：
   ```html
   <!-- offline_visualization.html -->
   <!DOCTYPE html>
   <html>
   <head>
       <title>离线知识图谱可视化</title>
       <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
       <script>
           // 加载静态数据
           fetch('static_kg_data.json')
               .then(response => response.json())
               .then(data => {
                   // 初始化Three.js场景
                   initVisualization(data);
               });
       </script>
   </head>
   <body>
       <div id="container"></div>
   </body>
   </html>
   ```

3. 打包分发：
   - 包含：HTML文件 + JSON数据 + Three.js库（或CDN）
   - 可在任何电脑上双击打开

### 方案5：WebSocket实时协作

**适用场景**：多用户同时查看，实时更新

**配置**：
1. 启用WebSocket支持（已内置）
2. 配置CORS允许跨域：
   ```python
   # 在main_3d.py中修改
   app.add_middleware(
       CORSMiddleware,
       allow_origins=["http://client1:3000", "http://client2:3000"],
       allow_credentials=True,
       allow_methods=["*"],
       allow_headers=["*"],
   )
   ```

3. 客户端连接：
   ```javascript
   const ws = new WebSocket('ws://server-ip:8001/ws');
   ws.onmessage = (event) => {
       const data = JSON.parse(event.data);
       // 处理实时更新
   };
   ```

## 数据同步方案

### 方案A：共享网络存储
- 使用SMB/NFS共享数据目录
- 所有实例指向同一数据源
- 适合局域网环境

### 方案B：数据库后端
- 将JSON数据迁移到数据库（PostgreSQL + JSONB）
- 支持多用户并发访问
- 推荐使用Neo4j图数据库

### 方案C：Git版本控制
- 数据目录纳入Git管理
- 定期提交更新
- 其他电脑git pull同步

## 安全配置

### 生产环境建议
1. **修改CORS设置**：
   ```python
   allow_origins=["https://your-domain.com"]  # 替换为实际域名
   ```

2. **启用HTTPS**：
   ```bash
   # 使用Let's Encrypt
   certbot --nginx -d your-domain.com
   ```

3. **API认证**：
   ```python
   # 添加API密钥验证
   API_KEYS = {"client1": "key1", "client2": "key2"}
   
   @app.middleware("http")
   async def verify_api_key(request: Request, call_next):
       api_key = request.headers.get("X-API-Key")
       if api_key not in API_KEYS.values():
           return JSONResponse({"error": "Invalid API key"}, status_code=401)
       return await call_next(request)
   ```

## 性能优化

### 前端优化
1. 启用Three.js缓存：
   ```javascript
   THREE.Cache.enabled = true;
   ```

2. 使用LOD（Level of Detail）：
   ```javascript
   // 根据距离调整细节
   const lod = new THREE.LOD();
   ```

3. 分批渲染大量节点

### 后端优化
1. 启用Gzip压缩：
   ```python
   from fastapi.middleware.gzip import GZipMiddleware
   app.add_middleware(GZipMiddleware, minimum_size=1000)
   ```

2. 添加缓存头：
   ```python
   @app.middleware("http")
   async def add_cache_headers(request: Request, call_next):
       response = await call_next(request)
       response.headers["Cache-Control"] = "public, max-age=3600"
       return response
   ```

## 故障排除

### 常见问题

1. **端口被占用**：
   ```bash
   # 查找占用进程
   netstat -ano | findstr :8001
   # 终止进程
   taskkill /PID [PID] /F
   ```

2. **数据加载失败**：
   - 检查data/memory/test2/kg_data目录是否存在
   - 确认文件权限
   - 查看日志：`python backend/main_3d.py`

3. **跨域问题**：
   - 确保CORS配置正确
   - 检查浏览器控制台错误

4. **Three.js加载慢**：
   - 使用CDN版本
   - 预加载资源
   - 减少初始节点数量

### 日志查看
```bash
# 查看实时日志
tail -f kg_visualization.log

# Windows查看日志
type kg_visualization.log
```

## 快速开始脚本

### Windows一键部署
```batch
@echo off
echo 部署三维知识图谱可视化系统...
echo.

REM 1. 安装依赖
pip install -r requirements.txt

REM 2. 启动服务
set KG_MEMORY_ID=test2
python backend/main_3d.py --host 0.0.0.0 --port 8001

echo.
echo 访问地址: http://localhost:8001/3d_frontend/index.html
echo 局域网访问: http://%COMPUTERNAME%:8001/3d_frontend/index.html
pause
```

### Linux/Mac部署脚本
```bash
#!/bin/bash
echo "部署三维知识图谱可视化系统..."

# 安装依赖
pip3 install -r requirements.txt

# 设置环境变量
export KG_MEMORY_ID=test2

# 启动服务
python3 backend/main_3d.py --host 0.0.0.0 --port 8001 &

echo "服务已启动"
echo "访问地址: http://localhost:8001/3d_frontend/index.html"
echo "局域网访问: http://$(hostname -I | awk '{print $1}'):8001/3d_frontend/index.html"
```

## 联系方式与支持

- 项目仓库：[你的GitHub仓库]
- 问题反馈：[Issues页面]
- 文档更新：[Wiki页面]

---

**最后更新**：2026-01-26  
**版本**：1.0.0  
**作者**：三维知识图谱可视化团队