#!/usr/bin/env python3
"""
三维知识图谱可视化后端主应用
提供REST API和WebSocket实时更新（支持三层结构）
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

from enhanced_data_loader import EnhancedKGDataLoader
from file_watcher import KGFileWatcher

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 创建FastAPI应用
app = FastAPI(title="三维知识图谱可视化API", version="1.0.0")

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制来源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 配置参数
import os
MEMORY_ID = os.environ.get("KG_MEMORY_ID", "test2")  # 默认使用test2目录

# 全局变量
data_loader: Optional[EnhancedKGDataLoader] = None
file_watcher: Optional[KGFileWatcher] = None
connected_clients: List[WebSocket] = []
main_event_loop: Optional[asyncio.AbstractEventLoop] = None
current_memory_id: str = MEMORY_ID

# Pydantic模型
class Entity3DResponse(BaseModel):
    id: str
    type: str
    confidence: float
    features: List[Dict]
    sources: List[Dict]

class FeatureResponse(BaseModel):
    id: str
    entity_id: str
    feature: str
    sources: List[Dict]
    confidence: float

class SceneResponse(BaseModel):
    id: str
    dialogue_id: str
    episode_id: str
    generated_at: str
    prompt_version: str
    file_number: Optional[int] = None

class EdgeResponse(BaseModel):
    id: str
    from_id: str
    to_id: str
    type: str
    confidence: float
    label: Optional[str] = None

class Graph3DResponse(BaseModel):
    entities: List[Dict]
    features: List[Dict]
    scenes: List[Dict]
    horizontal_edges: List[Dict]
    vertical_edges: List[Dict]
    stats: Dict

class Stats3DResponse(BaseModel):
    total_entities: int
    total_features: int
    total_scenes: int
    total_horizontal_edges: int
    total_vertical_edges: int
    entity_types: Dict[str, int]
    feature_distribution: Dict[str, int]
    scene_distribution: Dict[str, int]
    loaded_at: str
    memory_id: str

class MemoryInfoResponse(BaseModel):
    current_memory_id: str
    available_memory_ids: List[str]
    data_dir: str

class SwitchMemoryResponse(BaseModel):
    success: bool
    message: str
    new_memory_id: str
    stats: Optional[Dict] = None

# 事件回调函数
def on_file_change(change_type: str, file_path: str):
    """文件变化回调函数"""
    logger.info(f"文件变化: {change_type} {file_path}")
    
    # 重新加载数据
    if data_loader:
        stats = data_loader.load_all_data()
        logger.info(f"数据重新加载完成: {stats}")
        
        # 通知所有连接的客户端（使用线程安全的方式）
        if main_event_loop:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    notify_clients_about_update(change_type, file_path),
                    main_event_loop
                )
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

async def notify_clients_about_memory_switch(new_memory_id: str, stats: Dict):
    """通知所有连接的客户端关于memory切换"""
    if not connected_clients:
        return
    
    message = {
        "type": "memory_switched",
        "new_memory_id": new_memory_id,
        "timestamp": datetime.now().isoformat(),
        "stats": stats
    }
    
    disconnected_clients = []
    for client in connected_clients:
        try:
            await client.send_json(message)
        except Exception as e:
            logger.error(f"发送memory切换消息失败: {e}")
            disconnected_clients.append(client)
    
    # 移除断开连接的客户端
    for client in disconnected_clients:
        if client in connected_clients:
            connected_clients.remove(client)

def get_available_memory_ids() -> List[str]:
    """获取可用的memory_id列表"""
    import os
    memory_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "memory")
    available_ids = []
    
    if os.path.exists(memory_dir):
        for item in os.listdir(memory_dir):
            item_path = os.path.join(memory_dir, item)
            if os.path.isdir(item_path):
                # 检查是否有kg_data目录
                kg_data_path = os.path.join(item_path, "kg_data")
                if os.path.exists(kg_data_path):
                    available_ids.append(item)
    
    return available_ids

@app.on_event("startup")
async def startup_event():
    """应用启动时初始化"""
    global data_loader, file_watcher, main_event_loop, current_memory_id
    
    logger.info("启动三维知识图谱可视化后端...")
    
    # 保存主事件循环引用
    main_event_loop = asyncio.get_running_loop()
    
    # 初始化增强数据加载器
    data_loader = EnhancedKGDataLoader(memory_id=MEMORY_ID)
    current_memory_id = MEMORY_ID
    
    logger.info(f"Memory ID: {MEMORY_ID}")
    logger.info(f"数据目录: {data_loader.data_dir}")
    logger.info(f"目录是否存在: {data_loader.data_dir.exists()}")
    
    stats = data_loader.load_all_data()
    logger.info(f"初始数据加载完成: {stats}")
    
    # 启动文件监控 - 监控整个kg_data目录
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
    
    logger.info("关闭三维知识图谱可视化后端...")
    
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
            <title>三维知识图谱可视化API</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; }
                h1 { color: #333; }
                .endpoint { background: #f5f5f5; padding: 10px; margin: 10px 0; border-radius: 5px; }
                code { background: #eee; padding: 2px 5px; border-radius: 3px; }
            </style>
        </head>
        <body>
            <h1>三维知识图谱可视化API</h1>
            <p>后端服务正在运行。可用端点：</p>
            <div class="endpoint"><code>GET /api/3d/graph</code> - 获取三维图数据</div>
            <div class="endpoint"><code>GET /api/3d/stats</code> - 获取三维统计信息</div>
            <div class="endpoint"><code>GET /api/3d/entity/{id}</code> - 获取实体详细信息</div>
            <div class="endpoint"><code>GET /api/3d/feature/{id}</code> - 获取特征详细信息</div>
            <div class="endpoint"><code>GET /api/3d/scene/{id}</code> - 获取场景详细信息</div>
            <div class="endpoint"><code>GET /api/memory/info</code> - 获取memory信息</div>
            <div class="endpoint"><code>POST /api/memory/switch/{id}</code> - 切换memory</div>
            <div class="endpoint"><code>WS /ws</code> - WebSocket连接（实时更新）</div>
            <p>三维前端界面: <a href="/3d_frontend/index.html">/3d_frontend/index.html</a></p>
            <p>二维前端界面: <a href="/frontend/index.html">/frontend/index.html</a></p>
        </body>
    </html>
    """

@app.get("/api/3d/graph", response_model=Graph3DResponse)
async def get_3d_graph_data():
    """获取三维图数据（用于前端可视化）"""
    if not data_loader:
        return Graph3DResponse(
            entities=[], features=[], scenes=[],
            horizontal_edges=[], vertical_edges=[], stats={}
        )
    
    graph_data = data_loader.get_3d_graph_data()
    return Graph3DResponse(**graph_data)

@app.get("/api/3d/stats", response_model=Stats3DResponse)
async def get_3d_stats():
    """获取三维统计信息"""
    if not data_loader:
        return Stats3DResponse(
            total_entities=0,
            total_features=0,
            total_scenes=0,
            total_horizontal_edges=0,
            total_vertical_edges=0,
            entity_types={},
            feature_distribution={},
            scene_distribution={},
            loaded_at=datetime.now().isoformat(),
            memory_id=current_memory_id
        )
    
    stats = data_loader.load_all_data()
    return Stats3DResponse(**stats)

@app.get("/api/3d/entity/{entity_id}")
async def get_3d_entity(entity_id: str):
    """获取实体详细信息（包含特征和场景）"""
    if not data_loader:
        return {"error": "数据加载器未初始化"}
    
    entity_details = data_loader.get_entity_details(entity_id)
    if not entity_details:
        return {"error": f"实体 '{entity_id}' 不存在"}
    
    return entity_details

@app.get("/api/3d/feature/{feature_id}")
async def get_3d_feature(feature_id: str):
    """获取特征详细信息"""
    if not data_loader:
        return {"error": "数据加载器未初始化"}
    
    feature_details = data_loader.get_feature_details(feature_id)
    if not feature_details:
        return {"error": f"特征 '{feature_id}' 不存在"}
    
    return feature_details

@app.get("/api/3d/scene/{scene_id}")
async def get_3d_scene(scene_id: str):
    """获取场景详细信息"""
    if not data_loader:
        return {"error": "数据加载器未初始化"}
    
    scene_details = data_loader.get_scene_details(scene_id)
    if not scene_details:
        return {"error": f"场景 '{scene_id}' 不存在"}
    
    return scene_details

@app.get("/api/memory/info", response_model=MemoryInfoResponse)
async def get_memory_info():
    """获取当前memory信息和可用memory_id列表"""
    available_ids = get_available_memory_ids()
    
    return MemoryInfoResponse(
        current_memory_id=current_memory_id,
        available_memory_ids=available_ids,
        data_dir=str(data_loader.data_dir) if data_loader else ""
    )

@app.post("/api/memory/switch/{memory_id}", response_model=SwitchMemoryResponse)
async def switch_memory(memory_id: str):
    """切换到指定的memory_id"""
    global data_loader, file_watcher, current_memory_id
    
    logger.info(f"尝试切换到memory_id: {memory_id}")
    
    # 检查memory_id是否可用
    available_ids = get_available_memory_ids()
    if memory_id not in available_ids:
        return SwitchMemoryResponse(
            success=False,
            message=f"memory_id '{memory_id}' 不可用。可用ID: {available_ids}",
            new_memory_id=current_memory_id
        )
    
    try:
        # 停止当前的文件监控
        if file_watcher:
            file_watcher.stop()
            logger.info("已停止当前文件监控")
        
        # 创建新的数据加载器
        new_data_loader = EnhancedKGDataLoader(memory_id=memory_id)
        
        # 检查数据目录是否存在
        if not new_data_loader.data_dir.exists():
            return SwitchMemoryResponse(
                success=False,
                message=f"数据目录不存在: {new_data_loader.data_dir}",
                new_memory_id=current_memory_id
            )
        
        # 加载数据
        stats = new_data_loader.load_all_data()
        logger.info(f"切换到memory_id '{memory_id}' 成功，加载数据: {stats}")
        
        # 更新全局变量
        data_loader = new_data_loader
        current_memory_id = memory_id
        
        # 启动新的文件监控
        new_file_watcher = KGFileWatcher(
            data_dir=str(data_loader.data_dir),
            on_change_callback=on_file_change
        )
        if new_file_watcher.start():
            file_watcher = new_file_watcher
            logger.info(f"✅ 新的文件监控已启动，监控目录: {data_loader.data_dir}")
        else:
            logger.warning("⚠️ 新的文件监控启动失败，实时更新功能可能不可用")
        
        # 通知所有连接的客户端
        if main_event_loop:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    notify_clients_about_memory_switch(memory_id, stats),
                    main_event_loop
                )
                future.result(timeout=5)
                logger.debug("客户端通知发送成功")
            except Exception as e:
                logger.warning(f"客户端通知失败: {e}")
        
        return SwitchMemoryResponse(
            success=True,
            message=f"成功切换到memory_id: {memory_id}",
            new_memory_id=memory_id,
            stats=stats
        )
        
    except Exception as e:
        logger.error(f"切换memory_id失败: {e}")
        import traceback
        traceback.print_exc()
        
        return SwitchMemoryResponse(
            success=False,
            message=f"切换失败: {str(e)}",
            new_memory_id=current_memory_id
        )

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
                "graph": data_loader.get_3d_graph_data(),
                "current_memory_id": current_memory_id
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
# 三维前端
frontend_3d_dir = os.path.join(os.path.dirname(__file__), "../3d_frontend")
if os.path.exists(frontend_3d_dir):
    app.mount("/3d_frontend", StaticFiles(directory=frontend_3d_dir, html=True), name="3d_frontend")

# 二维前端（保持兼容）
frontend_dir = os.path.join(os.path.dirname(__file__), "../frontend")
if os.path.exists(frontend_dir):
    app.mount("/frontend", StaticFiles(directory=frontend_dir, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")