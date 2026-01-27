# 局域网快速部署指南

## 场景
两台电脑在同一局域网内，想在另一台电脑上查看三维知识图谱可视化。

## 解决方案
使用**局域网共享方案** - 最简单、最直接的方法。

## 三步部署法

### 第一步：在主机上启动服务

**方法A：使用批处理文件（推荐）**
```bash
cd kg_visualization
deploy_lan.bat
```

**方法B：手动启动**
```bash
cd kg_visualization
python backend/main_3d.py --host 0.0.0.0 --port 8001
```

**方法C：使用现有脚本**
```bash
cd kg_visualization
start_3d.bat
```

### 第二步：获取主机IP地址

服务启动后，会显示类似以下信息：
```
本机IP地址: 192.168.1.100
访问地址:
本地访问: http://localhost:8001/3d_frontend/index.html
局域网访问: http://192.168.1.100:8001/3d_frontend/index.html
```

如果未显示IP，可以手动获取：
- **Windows**: 打开命令提示符，输入 `ipconfig`
- **Mac/Linux**: 打开终端，输入 `ifconfig` 或 `ip addr`

### 第三步：在另一台电脑访问

在另一台电脑的浏览器中打开：
```
http://[主机IP]:8001/3d_frontend/index.html
```

例如：
```
http://192.168.1.100:8001/3d_frontend/index.html
```

## 故障排除

### 问题1：无法连接
**症状**：浏览器显示"无法访问此网站"

**解决方案**：
1. 检查两台电脑是否在同一网络
2. 检查防火墙设置（可能需要允许端口8001）
3. 确保服务绑定到 `0.0.0.0` 而不是 `127.0.0.1`

**防火墙设置（Windows）**：
```cmd
# 允许端口8001
netsh advfirewall firewall add rule name="KG Visualization" dir=in action=allow protocol=TCP localport=8001
```

### 问题2：服务启动失败
**症状**：端口被占用或Python依赖问题

**解决方案**：
1. 检查端口是否被占用：
   ```cmd
   netstat -ano | findstr :8001
   ```

2. 安装依赖：
   ```cmd
   pip install -r requirements.txt
   ```

3. 使用不同端口：
   ```cmd
   python backend/main_3d.py --host 0.0.0.0 --port 8002
   ```

### 问题3：数据加载失败
**症状**：页面显示但无数据

**解决方案**：
1. 检查数据目录是否存在：
   ```
   data/memory/test2/kg_data/
   ```

2. 查看服务日志确认错误

## 测试连接

运行测试脚本验证连接：
```bash
cd kg_visualization
python test_lan_connection.py
```

## 高级配置

### 1. 自动启动服务
创建计划任务或开机启动项，让服务自动启动。

### 2. 使用固定IP
在路由器中为主机分配固定IP，避免IP变化。

### 3. 域名访问（可选）
在路由器设置DDNS，使用域名访问：
```
http://your-domain.ddns.net:8001/3d_frontend/index.html
```

## 安全注意事项

### 生产环境建议
1. **限制访问IP**（如果只在特定电脑查看）：
   ```python
   # 在main_3d.py中修改
   allow_origins=["http://192.168.1.101", "http://192.168.1.102"]
   ```

2. **添加简单认证**（可选）：
   ```python
   # 在index.html中添加基本认证
   ```

3. **使用HTTPS**（如果通过公网访问）：
   ```bash
   # 使用nginx反向代理 + Let's Encrypt
   ```

## 性能优化

### 大量数据时
如果数据量很大（超过1000个节点）：
1. 启用分批加载
2. 使用LOD（细节层次）
3. 减少初始显示节点数

## 实时更新

系统支持WebSocket实时更新：
- 数据变化时自动刷新
- 多用户同时查看
- 实时交互

## 其他访问方式

### 手机/平板访问
在同一WiFi下，使用手机浏览器访问：
```
http://[主机IP]:8001/3d_frontend/index.html
```

### 远程桌面分享
使用TeamViewer、AnyDesk等远程桌面软件，直接分享主机屏幕。

## 联系方式

遇到问题：
1. 查看详细文档：`DEPLOYMENT_GUIDE.md`
2. 运行测试脚本：`test_lan_connection.py`
3. 检查服务日志

---

**最后更新**：2026-01-26  
**适用版本**：三维知识图谱可视化 v1.0.0