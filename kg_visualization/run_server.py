#!/usr/bin/env python3
"""
启动知识图谱可视化服务器
"""

import sys
import os
import subprocess
import time

def start_server():
    """启动后端服务器"""
    print("启动知识图谱可视化服务器...")
    
    # 切换到backend目录
    backend_dir = os.path.join(os.path.dirname(__file__), "backend")
    
    # 启动服务器
    cmd = [sys.executable, "main.py"]
    
    print(f"执行命令: {' '.join(cmd)}")
    print(f"工作目录: {backend_dir}")
    
    try:
        # 启动进程
        process = subprocess.Popen(
            cmd,
            cwd=backend_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8'
        )
        
        print(f"服务器进程已启动 (PID: {process.pid})")
        
        # 等待几秒让服务器启动
        print("等待服务器启动...")
        time.sleep(3)
        
        # 检查服务器是否运行
        import requests
        try:
            response = requests.get("http://localhost:8000", timeout=5)
            if response.status_code == 200:
                print("✅ 服务器启动成功!")
                print(f"API地址: http://localhost:8000")
                print(f"前端地址: http://localhost:8000/frontend/index.html")
                print(f"WebSocket地址: ws://localhost:8000/ws")
            else:
                print(f"⚠️ 服务器响应状态码: {response.status_code}")
        except requests.exceptions.ConnectionError:
            print("❌ 服务器连接失败")
        
        # 输出服务器日志
        print("\n服务器输出:")
        try:
            stdout, stderr = process.communicate(timeout=2)
            if stdout:
                print(f"标准输出: {stdout[:500]}")
            if stderr:
                print(f"标准错误: {stderr[:500]}")
        except subprocess.TimeoutExpired:
            print("服务器仍在运行...")
        
        # 保持运行
        print("\n按Ctrl+C停止服务器...")
        try:
            process.wait()
        except KeyboardInterrupt:
            print("\n停止服务器...")
            process.terminate()
            process.wait()
            print("服务器已停止")
            
    except Exception as e:
        print(f"启动服务器失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    start_server()