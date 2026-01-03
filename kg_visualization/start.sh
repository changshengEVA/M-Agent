#!/bin/bash
# 知识图谱可视化系统启动脚本

echo "========================================="
echo "  知识图谱实时可视化系统启动"
echo "========================================="

# 检查Python版本
echo "检查Python版本..."
python3 --version || { echo "Python3未安装"; exit 1; }

# 检查依赖
echo "检查依赖..."
if [ ! -f "requirements.txt" ]; then
    echo "requirements.txt 不存在"
    exit 1
fi

# 安装依赖（如果未安装）
echo "安装Python依赖..."
pip3 install -r requirements.txt

# 创建必要的目录
echo "创建目录结构..."
mkdir -p logs
mkdir -p data

# 启动后端服务
echo "启动后端服务..."
cd backend
python3 main.py &

# 获取进程ID
BACKEND_PID=$!
echo "后端服务PID: $BACKEND_PID"

# 等待服务启动
echo "等待服务启动..."
sleep 3

# 检查服务是否运行
if curl -s http://localhost:8000 > /dev/null; then
    echo "✅ 后端服务启动成功"
    echo "API地址: http://localhost:8000"
    echo "前端地址: http://localhost:8000/frontend/index.html"
    echo "WebSocket地址: ws://localhost:8000/ws"
else
    echo "❌ 后端服务启动失败"
    kill $BACKEND_PID 2>/dev/null
    exit 1
fi

echo ""
echo "========================================="
echo "  系统已启动！"
echo "========================================="
echo ""
echo "使用以下命令停止系统:"
echo "  kill $BACKEND_PID"
echo ""
echo "或者使用Ctrl+C停止当前进程"
echo "========================================="

# 保持脚本运行
wait $BACKEND_PID