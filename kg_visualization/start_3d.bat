@echo off
echo 启动三维知识图谱可视化后端...
echo.

REM 设置环境变量
set KG_MEMORY_ID=test2

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到Python，请先安装Python 3.7+
    pause
    exit /b 1
)

REM 检查依赖
echo 检查Python依赖...
pip list | findstr "fastapi uvicorn" >nul
if errorlevel 1 (
    echo 安装依赖...
    pip install -r requirements.txt
)

REM 启动后端服务
echo 启动三维可视化后端服务...
echo 访问地址: http://localhost:8001
echo 三维前端: http://localhost:8001/3d_frontend/index.html
echo 二维前端: http://localhost:8001/frontend/index.html
echo.

cd /d "%~dp0"
python backend/main_3d.py

pause