# 故障排除指南

如果网页无法正常运作，请按照以下步骤诊断问题。

## 1. 检查后端服务是否运行

### 方法1: 检查API端点
打开浏览器或使用命令行访问:
```
http://localhost:8000/api/stats
```

应该返回类似以下的JSON数据:
```json
{"total_scenes":7,"total_entities":7,"total_relations":2,...}
```

### 方法2: 检查服务进程
在终端中运行:
```bash
# PowerShell
Get-Process -Name python | Where-Object {$_.CommandLine -like "*main.py*"}

# 或检查端口
netstat -ano | findstr :8000
```

## 2. 检查前端页面

### 方法1: 直接访问页面
打开浏览器访问:
```
http://localhost:8000/frontend/index.html
```

### 方法2: 检查浏览器控制台
1. 按 `F12` 打开开发者工具
2. 切换到 `Console` 标签页
3. 查看是否有红色错误信息

常见错误:
- `Failed to load resource: net::ERR_CONNECTION_REFUSED` - 后端服务未运行
- `vis is not defined` - vis.js库加载失败
- `Chart is not defined` - Chart.js库加载失败

## 3. 检查WebSocket连接

### 方法1: 浏览器开发者工具
1. 打开开发者工具 (`F12`)
2. 切换到 `Network` 标签页
3. 过滤 `WS` (WebSocket) 连接
4. 查看WebSocket连接状态

### 方法2: 测试WebSocket
在浏览器控制台中运行:
```javascript
// 测试WebSocket连接
const ws = new WebSocket('ws://localhost:8000/ws');
ws.onopen = () => console.log('WebSocket连接成功');
ws.onerror = (e) => console.error('WebSocket错误:', e);
ws.onclose = (e) => console.log('WebSocket关闭:', e.code, e.reason);
```

## 4. 常见问题及解决方案

### 问题1: 后端服务无法启动
**症状**: 访问 `http://localhost:8000` 显示连接失败

**解决方案**:
1. 检查端口8000是否被占用:
   ```bash
   netstat -ano | findstr :8000
   ```
2. 终止占用端口的进程
3. 重新启动服务:
   ```bash
   cd kg_visualization/backend
   python main.py
   ```

### 问题2: 数据加载失败
**症状**: 页面显示0个实体和关系

**解决方案**:
1. 检查数据目录是否存在:
   ```bash
   dir data\memory\kg_candidates\strong
   ```
2. 检查JSON文件格式是否正确
3. 查看后端日志中的错误信息

### 问题3: 可视化图表不显示
**症状**: 页面布局正常但知识图谱不显示

**解决方案**:
1. 检查vis.js库是否加载:
   - 在浏览器中打开 `https://unpkg.com/vis-network/standalone/umd/vis-network.min.js`
   - 如果无法访问，可能需要使用本地版本或更换CDN
2. 检查浏览器控制台是否有JavaScript错误
3. 尝试使用其他浏览器 (Chrome/Firefox)

### 问题4: 实时更新不工作
**症状**: 修改数据文件后页面不更新

**解决方案**:
1. 检查文件监控是否启用
2. 查看后端日志中是否有文件变化事件
3. 检查WebSocket连接是否正常
4. 手动刷新页面查看数据是否更新

## 5. 手动测试步骤

### 步骤1: 启动服务
```bash
cd kg_visualization/backend
python main.py
```

### 步骤2: 测试API
```bash
# PowerShell
Invoke-RestMethod -Uri "http://localhost:8000/api/stats" -Method Get -UseBasicParsing

# 或使用curl
curl http://localhost:8000/api/stats
```

### 步骤3: 测试WebSocket
使用在线WebSocket测试工具或浏览器控制台测试连接。

### 步骤4: 测试文件监控
1. 复制一个数据文件:
   ```bash
   copy data\memory\kg_candidates\strong\scene_000001.kg_candidate.json data\memory\kg_candidates\strong\scene_test.kg_candidate.json
   ```
2. 观察后端日志和前端更新日志

## 6. 备用方案

如果CDN库无法加载，可以使用本地版本:

### 下载本地库文件
1. 下载 vis-network:
   ```
   https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js
   ```
2. 下载 Chart.js:
   ```
   https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js
   ```
3. 将文件放入 `kg_visualization/frontend/libs/` 目录
4. 更新HTML中的script标签:
   ```html
   <script src="libs/vis-network.min.js"></script>
   <script src="libs/chart.umd.min.js"></script>
   ```

## 7. 获取帮助

如果以上步骤都无法解决问题:

1. 查看后端服务的完整日志输出
2. 检查浏览器控制台的完整错误信息
3. 确保所有依赖已安装:
   ```bash
   pip install -r requirements.txt
   ```
4. 检查Python版本是否为3.9+

## 系统状态检查清单

- [ ] 后端服务正在运行 (`python main.py`)
- [ ] API端点可访问 (`http://localhost:8000/api/stats`)
- [ ] 前端页面可访问 (`http://localhost:8000/frontend/index.html`)
- [ ] 浏览器控制台无JavaScript错误
- [ ] WebSocket连接成功
- [ ] 数据文件存在且格式正确
- [ ] 依赖库(vis.js, Chart.js)加载成功