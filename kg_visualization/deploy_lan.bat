@echo off
echo ========================================
echo 三维知识图谱可视化 - 局域网部署脚本
echo ========================================
echo.

REM 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python 3.7+
    pause
    exit /b 1
)

REM 检查依赖
echo [1/4] 检查Python依赖...
pip list | findstr "fastapi uvicorn" >nul
if errorlevel 1 (
    echo 安装依赖...
    pip install -r requirements.txt
) else (
    echo 依赖已安装
)

REM 获取本机IP
echo [2/4] 获取本机IP地址...
for /f "tokens=2 delims=:" %%i in ('ipconfig ^| findstr "IPv4"') do (
    set "IP=%%i"
    goto :got_ip
)
:got_ip
set "IP=%IP:~1%"
echo 本机IP地址: %IP%

REM 设置环境变量
echo [3/4] 设置环境变量...
set KG_MEMORY_ID=test2

REM 启动服务
echo [4/4] 启动三维可视化服务...
echo.
echo ========================================
echo 服务启动中...
echo 访问地址:
echo 本地访问: http://localhost:8001/3d_frontend/index.html
echo 局域网访问: http://%IP%:8001/3d_frontend/index.html
echo ========================================
echo.
echo 按Ctrl+C停止服务
echo.

cd /d "%~dp0"
python backend/main_3d.py --host 0.0.0.0 --port 8001

pause