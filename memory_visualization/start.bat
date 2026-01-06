@echo off
echo 启动Memory可视化后端服务...
echo.

REM 检查Python环境
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到Python，请确保Python已安装并添加到PATH
    pause
    exit /b 1
)

REM 检查依赖是否已安装
echo 检查依赖...
pip list | findstr fastapi >nul
if errorlevel 1 (
    echo 安装依赖...
    pip install -r requirements.txt
) else (
    echo 依赖已安装
)

REM 启动后端服务
echo.
echo 启动后端服务...
echo 访问地址: http://localhost:8001
echo 前端界面: http://localhost:8001/frontend/index.html
echo 按 Ctrl+C 停止服务
echo.

cd backend
python main.py

pause