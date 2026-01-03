#!/usr/bin/env python3
"""
知识图谱可视化后端主应用
提供REST API和WebSocket实时更新
"""

import json
import logging
import asyncio
from typing import Dict, List, Optional
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from data_loader import KGDataLoader
from file_watcher import KGFileWatcher

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 创建FastAPI应用
app = FastAPI(title="知识图谱可视化API", version="1.0.0")

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制来源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局变量
data_loader: Optional[KGDataLoader] = None
file_watcher: Optional[KGFileWatcher] = None
connected_clients: List[WebSocket] = []
main_event_loop: Optional[asyncio.AbstractEventLoop] = None

# Pydantic模型
class EntityResponse(BaseModel):
    id: str
    type: str
    confidence: float
    scenes: List[str]

class RelationResponse(BaseModel):
    subject: str
    relation: str
    object: str
    confidence: float
    scene_id: str

class SceneResponse(BaseModel):
    scene_id: str
    user_id: str
    generated_at: str
    prompt_version: str

class StatsResponse(BaseModel):
    total_scenes: int
    total_entities: int
    total_relations: int
    entity_types: Dict[str, int]
    relation_types: Dict[str, int]
    loaded_at: str

class GraphDataResponse(BaseModel):
    nodes: List[Dict]
    edges: List[Dict]

# 事件回调函数
def on_file_change(change_type: str, file_path: str):
    """文件变化回调函数"""
    logger.info(f"文件变化: {change_type} {file_path}")
    
    # 重新加载数据
    if data_loader:
        stats = data_loader.load_all_data()
        logger.info(f"数据重新加载完成: {stats}")
        
        # 通知所有连接的客户端（使用线程安全的方式）
        # 使用保存的主事件循环引用
        if main_event_loop:
            try:
                # 使用线程安全的方式在主事件循环中运行协程
                future = asyncio.run_coroutine_threadsafe(
                    notify_clients_about_update(change_type, file_path),
                    main_event_loop
                )
                # 可选：等待结果或处理异常
                try:
                    future.result(timeout=5)
                    logger.debug("客户端通知发送成功")
                except asyncio.TimeoutError:
                    logger.warning("客户端通知超时")
                except Exception as e:
                    logger.warning(f"客户端通知失败: {e}")
            except Exception as e:
                logger.error(f"安排客户端通知失败: {e}")
        else:
            logger.warning("主事件循环未初始化，无法通知客户端")

async def notify_clients_about_update(change_type: str, file_path: str):
    """通知所有连接的客户端关于数据更新"""
    if not connected_clients:
        return
    
    message = {
        "type": "data_updated",
        "change_type": change_type,
        "file_path": file_path,
        "timestamp": datetime.now().isoformat(),
        "stats": data_loader.load_all_data() if data_loader else {}
    }
    
    disconnected_clients = []
    for client in connected_clients:
        try:
            await client.send_json(message)
        except Exception as e:
            logger.error(f"发送更新消息失败: {e}")
            disconnected_clients.append(client)
    
    # 移除断开连接的客户端
    for client in disconnected_clients:
        if client in connected_clients:
            connected_clients.remove(client)

@app.on_event("startup")
async def startup_event():
    """应用启动时初始化"""
    global data_loader, file_watcher, main_event_loop
    
    logger.info("启动知识图谱可视化后端...")
    
    # 保存主事件循环引用
    main_event_loop = asyncio.get_running_loop()
    
    # 初始化数据加载器 - 使用默认路径（会自动计算正确路径）
    data_loader = KGDataLoader()
    
    logger.info(f"数据目录: {data_loader.data_dir}")
    logger.info(f"目录是否存在: {data_loader.data_dir.exists()}")
    
    stats = data_loader.load_all_data()
    logger.info(f"初始数据加载完成: {stats}")
    
    # 启动文件监控
    file_watcher = KGFileWatcher(
        data_dir=str(data_loader.data_dir),
        on_change_callback=on_file_change
    )
    if file_watcher.start():
        logger.info("✅ 文件监控已成功启动")
        logger.info(f"监控目录: {data_loader.data_dir}")
        logger.info(f"监控器运行状态: {file_watcher.is_running()}")
    else:
        logger.error("❌ 文件监控启动失败")
        logger.warning("实时更新功能可能不可用")

@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时清理"""
    global file_watcher
    
    logger.info("关闭知识图谱可视化后端...")
    
    if file_watcher:
        file_watcher.stop()
        logger.info("文件监控已停止")
    
    # 关闭所有WebSocket连接
    for client in connected_clients:
        try:
            await client.close()
        except Exception:
            pass
    connected_clients.clear()

# REST API端点
@app.get("/", response_class=HTMLResponse)
async def root():
    """根路径，返回简单欢迎页面"""
    return """
    <html>
        <head>
            <title>知识图谱可视化API</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; }
                h1 { color: #333; }
                .endpoint { background: #f5f5f5; padding: 10px; margin: 10px 0; border-radius: 5px; }
                code { background: #eee; padding: 2px 5px; border-radius: 3px; }
            </style>
        </head>
        <body>
            <h1>知识图谱可视化API</h1>
            <p>后端服务正在运行。可用端点：</p>
            <div class="endpoint"><code>GET /api/nodes</code> - 获取所有实体节点</div>
            <div class="endpoint"><code>GET /api/edges</code> - 获取所有关系边</div>
            <div class="endpoint"><code>GET /api/scenes</code> - 获取所有scene信息</div>
            <div class="endpoint"><code>GET /api/stats</code> - 获取统计信息</div>
            <div class="endpoint"><code>GET /api/graph</code> - 获取图数据（用于可视化）</div>
            <div class="endpoint"><code>GET /api/entity/{id}</code> - 获取特定实体信息</div>
            <div class="endpoint"><code>WS /ws</code> - WebSocket连接（实时更新）</div>
            <p>前端界面: <a href="/frontend/index.html">/frontend/index.html</a></p>
        </body>
    </html>
    """

@app.get("/api/nodes", response_model=List[EntityResponse])
async def get_nodes():
    """获取所有实体节点"""
    if not data_loader:
        return []
    return data_loader.get_all_entities()

@app.get("/api/edges", response_model=List[RelationResponse])
async def get_edges():
    """获取所有关系边"""
    if not data_loader:
        return []
    return data_loader.get_all_relations()

@app.get("/api/scenes", response_model=List[SceneResponse])
async def get_scenes():
    """获取所有scene信息"""
    if not data_loader:
        return []
    return data_loader.get_all_scenes()

@app.get("/api/stats", response_model=StatsResponse)
async def get_stats():
    """获取统计信息"""
    if not data_loader:
        return StatsResponse(
            total_scenes=0,
            total_entities=0,
            total_relations=0,
            entity_types={},
            relation_types={},
            loaded_at=datetime.now().isoformat()
        )
    
    # 重新加载数据以获取最新统计
    stats = data_loader.load_all_data()
    return StatsResponse(**stats)

@app.get("/api/graph", response_model=GraphDataResponse)
async def get_graph_data():
    """获取图数据（用于前端可视化）"""
    if not data_loader:
        return GraphDataResponse(nodes=[], edges=[])
    return GraphDataResponse(**data_loader.get_graph_data())

@app.get("/api/entity/{entity_id}")
async def get_entity(entity_id: str):
    """获取特定实体信息及其相关关系"""
    if not data_loader:
        return {"error": "数据加载器未初始化"}
    
    entity = data_loader.get_entity_by_id(entity_id)
    if not entity:
        return {"error": f"实体 '{entity_id}' 不存在"}
    
    relations = data_loader.get_relations_for_entity(entity_id)
    
    return {
        "entity": entity,
        "relations": relations
    }

# WebSocket端点
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket连接，用于实时更新"""
    await websocket.accept()
    connected_clients.append(websocket)
    logger.info(f"新的WebSocket连接，当前连接数: {len(connected_clients)}")
    
    try:
        # 发送初始数据
        if data_loader:
            initial_data = {
                "type": "initial_data",
                "timestamp": datetime.now().isoformat(),
                "stats": data_loader.load_all_data(),
                "graph": data_loader.get_graph_data()
            }
            await websocket.send_json(initial_data)
        
        # 保持连接，等待客户端消息
        while True:
            data = await websocket.receive_text()
            # 处理客户端消息（如果需要）
            logger.debug(f"收到客户端消息: {data}")
            
    except WebSocketDisconnect:
        logger.info("WebSocket连接断开")
    except Exception as e:
        logger.error(f"WebSocket错误: {e}")
    finally:
        if websocket in connected_clients:
            connected_clients.remove(websocket)
        logger.info(f"WebSocket连接关闭，剩余连接数: {len(connected_clients)}")

# 挂载前端静态文件
import os
frontend_dir = os.path.join(os.path.dirname(__file__), "../frontend")
app.mount("/frontend", StaticFiles(directory=frontend_dir, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")