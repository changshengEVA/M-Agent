@echo off
chcp 65001 >nul
echo =========================================
echo   知识图谱实时可视化系统启动
echo =========================================

REM 检查Python版本
echo 检查Python版本...
python --version
if errorlevel 1 (
    echo Python未安装或未添加到PATH
    pause
    exit /b 1
)

REM 检查依赖
echo 检查依赖...
if not exist "requirements.txt" (
    echo requirements.txt 不存在
    pause
    exit /b 1
)

REM 安装依赖（如果未安装）
echo 安装Python依赖...
pip install -r requirements.txt

REM 创建必要的目录
echo 创建目录结构...
if not exist "logs" mkdir logs
if not exist "data" mkdir data

REM 启动后端服务
echo 启动后端服务...
cd backend
start "KG Visualization Backend" python main.py

REM 等待服务启动
echo 等待服务启动...
timeout /t 5 /nobreak >nul

REM 检查服务是否运行
echo 检查服务状态...
curl -s http://localhost:8000 >nul
if errorlevel 1 (
    echo ❌ 后端服务启动失败
    pause
    exit /b 1
)

echo ✅ 后端服务启动成功
echo API地址: http://localhost:8000
echo 前端地址: http://localhost:8000/frontend/index.html
echo WebSocket地址: ws://localhost:8000/ws
echo.
echo =========================================
echo   系统已启动！
echo =========================================
echo.
echo 按任意键停止系统...
pause >nul

REM 查找并终止Python进程
taskkill /F /IM python.exe >nul 2>&1
echo 系统已停止
pause