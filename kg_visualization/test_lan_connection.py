#!/usr/bin/env python3
"""
测试局域网连接脚本
用于验证三维知识图谱可视化服务是否能在局域网内访问
"""

import socket
import requests
import subprocess
import time
import sys

def get_local_ip():
    """获取本机IP地址"""
    try:
        # 创建一个UDP套接字连接到外部服务器（不实际发送数据）
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception as e:
        print(f"获取IP地址失败: {e}")
        return "127.0.0.1"

def check_port_open(ip, port, timeout=2):
    """检查端口是否开放"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except Exception as e:
        print(f"检查端口 {port} 失败: {e}")
        return False

def test_local_service():
    """测试本地服务"""
    print("测试本地服务...")
    
    # 测试本地API
    try:
        response = requests.get("http://localhost:8001/api/3d/stats", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ 本地服务正常")
            print(f"   实体数量: {data.get('total_entities', 0)}")
            print(f"   特征数量: {data.get('total_features', 0)}")
            print(f"   场景数量: {data.get('total_scenes', 0)}")
            return True
        else:
            print(f"❌ 本地服务响应异常: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ 本地服务未运行")
        return False
    except Exception as e:
        print(f"❌ 测试本地服务失败: {e}")
        return False

def test_lan_service(ip):
    """测试局域网服务"""
    print(f"测试局域网服务 (IP: {ip})...")
    
    try:
        response = requests.get(f"http://{ip}:8001/api/3d/stats", timeout=5)
        if response.status_code == 200:
            print(f"✅ 局域网服务正常")
            print(f"   访问地址: http://{ip}:8001/3d_frontend/index.html")
            return True
        else:
            print(f"❌ 局域网服务响应异常: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ 局域网服务无法连接")
        return False
    except Exception as e:
        print(f"❌ 测试局域网服务失败: {e}")
        return False

def check_firewall():
    """检查防火墙设置"""
    print("检查防火墙设置...")
    
    try:
        # Windows防火墙检查
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", "name=all"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if "8001" in result.stdout:
            print("✅ 防火墙规则已包含端口8001")
        else:
            print("⚠️  防火墙可能阻止端口8001，建议添加规则:")
            print("   netsh advfirewall firewall add rule name=\"KG Visualization\" dir=in action=allow protocol=TCP localport=8001")
            
    except Exception as e:
        print(f"⚠️  无法检查防火墙: {e}")

def start_service_if_needed():
    """如果需要，启动服务"""
    if not check_port_open("localhost", 8001):
        print("服务未运行，尝试启动...")
        try:
            # 使用现有的start_3d.bat
            subprocess.Popen(
                ["start_3d.bat"],
                cwd=".",
                shell=True
            )
            print("✅ 服务启动命令已执行")
            print("等待10秒让服务启动...")
            time.sleep(10)
            return True
        except Exception as e:
            print(f"❌ 启动服务失败: {e}")
            return False
    return True

def main():
    print("=" * 60)
    print("三维知识图谱可视化 - 局域网连接测试")
    print("=" * 60)
    
    # 获取本机IP
    local_ip = get_local_ip()
    print(f"本机IP地址: {local_ip}")
    
    # 检查服务是否运行
    if not start_service_if_needed():
        print("请手动启动服务: python backend/main_3d.py")
        return
    
    # 测试本地服务
    if not test_local_service():
        print("本地服务测试失败，请检查服务是否正常运行")
        return
    
    # 检查端口开放
    print(f"检查端口8001是否开放...")
    if check_port_open(local_ip, 8001):
        print(f"✅ 端口8001已开放")
    else:
        print(f"❌ 端口8001未开放")
        print("可能的原因:")
        print("1. 服务未绑定到0.0.0.0（请使用--host 0.0.0.0参数）")
        print("2. 防火墙阻止了端口")
        print("3. 服务未正确启动")
    
    # 测试局域网服务
    test_lan_service(local_ip)
    
    # 检查防火墙
    check_firewall()
    
    print("\n" + "=" * 60)
    print("部署指南:")
    print("=" * 60)
    print("1. 在主机上运行服务:")
    print("   cd kg_visualization")
    print("   python backend/main_3d.py --host 0.0.0.0 --port 8001")
    print()
    print("2. 在其他电脑浏览器访问:")
    print(f"   http://{local_ip}:8001/3d_frontend/index.html")
    print()
    print("3. 如果无法访问，检查:")
    print("   - 两台电脑是否在同一网络")
    print("   - 防火墙是否允许端口8001")
    print("   - 服务是否绑定到0.0.0.0")
    print()
    print("4. 快速启动脚本:")
    print("   deploy_lan.bat")
    print("=" * 60)

if __name__ == "__main__":
    main()