#!/usr/bin/env python3
"""
Memory可视化后端主应用
提供REST API和WebSocket实时更新
"""

import json
import logging
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from data_loader import MemoryDataLoader
from file_watcher import MemoryFileWatcher

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 创建FastAPI应用
app = FastAPI(title="Memory可视化API", version="1.0.0")

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制来源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局变量
data_loader: Optional[MemoryDataLoader] = None
file_watcher: Optional[MemoryFileWatcher] = None
connected_clients: List[WebSocket] = []
main_event_loop: Optional[asyncio.AbstractEventLoop] = None

# Pydantic模型
class DialogueResponse(BaseModel):
    dialogue_id: str
    user_id: str
    start_time: str
    end_time: str
    turn_count: int
    file_path: str

class EpisodeResponse(BaseModel):
    episode_id: str
    dialogue_id: str
    turn_span: List[int]
    segmentation_reason: List[str]

class QualificationResponse(BaseModel):
    episode_id: str
    dialogue_id: str
    scene_potential_score: Dict[str, int]
    decision: str
    rationale: Dict[str, str]

class SceneResponse(BaseModel):
    scene_id: str
    user_id: str
    scene_type: str
    content_type: str
    diary: str
    intent: str
    confidence: float

class StatsResponse(BaseModel):
    total_dialogues: int
    total_episodes: int
    total_qualifications: int
    total_scenes: int
    loaded_at: str
    dialogues_by_user: Dict[str, int]
    episodes_by_dialogue: Dict[str, int]
    scenes_by_user: Dict[str, int]
    score_distribution: Dict[str, Dict[str, int]]
    episode_situation_loaded: bool

class EpisodeSituationResponse(BaseModel):
    statistics: Dict[str, Any]
    episodes: Dict[str, Dict[str, Any]]
    metadata: Dict[str, Any]

class DialogueDetailResponse(BaseModel):
    dialogue: Dict
    episodes: List[Dict]
    qualifications: List[Dict]

class EpisodeDetailResponse(BaseModel):
    episode: Dict
    qualification: Optional[Dict]
    dialogue: Optional[Dict]

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
        "stats": data_loader.get_stats() if data_loader else {}
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
    
    logger.info("启动Memory可视化后端...")
    
    # 保存主事件循环引用
    main_event_loop = asyncio.get_running_loop()
    
    # 初始化数据加载器
    data_loader = MemoryDataLoader()
    
    logger.info(f"数据目录: {data_loader.data_dir}")
    logger.info(f"目录是否存在: {data_loader.data_dir.exists()}")
    
    stats = data_loader.load_all_data()
    logger.info(f"初始数据加载完成: {stats}")
    
    # 启动文件监控
    file_watcher = MemoryFileWatcher(
        data_dir=str(data_loader.data_dir),
        on_change_callback=on_file_change
    )
    if file_watcher.start():
        logger.info("✅ 文件监控已成功启动")
        logger.info(f"监控目录: {data_loader.data_dir}")
        logger.info(f"监控器运行状态: {file_watcher.get_running_status()}")
    else:
        logger.error("❌ 文件监控启动失败")
        logger.warning("实时更新功能可能不可用")

@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时清理"""
    global file_watcher
    
    logger.info("关闭Memory可视化后端...")
    
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
            <title>Memory可视化API</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; }
                h1 { color: #333; }
                .endpoint { background: #f5f5f5; padding: 10px; margin: 10px 0; border-radius: 5px; }
                code { background: #eee; padding: 2px 5px; border-radius: 3px; }
            </style>
        </head>
        <body>
            <h1>Memory可视化API</h1>
            <p>后端服务正在运行。可用端点：</p>
            <div class="endpoint"><code>GET /api/dialogues</code> - 获取所有dialogues</div>
            <div class="endpoint"><code>GET /api/episodes</code> - 获取所有episodes</div>
            <div class="endpoint"><code>GET /api/qualifications</code> - 获取所有qualifications</div>
            <div class="endpoint"><code>GET /api/scenes</code> - 获取所有scenes</div>
            <div class="endpoint"><code>GET /api/stats</code> - 获取统计信息</div>
            <div class="endpoint"><code>GET /api/episode_situation</code> - 获取episode策略信息</div>
            <div class="endpoint"><code>GET /api/episode_situation/{dialogue_id}/{episode_id}</code> - 获取特定episode的策略信息</div>
            <div class="endpoint"><code>GET /api/dialogue/{id}</code> - 获取特定dialogue及其详细信息</div>
            <div class="endpoint"><code>GET /api/episode/{dialogue_id}/{episode_id}</code> - 获取特定episode及其评分信息</div>
            <div class="endpoint"><code>GET /api/scene/{id}</code> - 获取特定scene信息</div>
            <div class="endpoint"><code>WS /ws</code> - WebSocket连接（实时更新）</div>
            <p>前端界面: <a href="/frontend/index.html">/frontend/index.html</a></p>
        </body>
    </html>
    """

@app.get("/api/dialogues", response_model=List[DialogueResponse])
async def get_dialogues():
    """获取所有dialogues"""
    if not data_loader:
        return []
    
    dialogues = data_loader.get_all_dialogues()
    result = []
    for dialogue in dialogues:
        turns = dialogue.get("turns", [])
        meta = dialogue.get("meta", {})
        result.append(DialogueResponse(
            dialogue_id=dialogue.get("dialogue_id", ""),
            user_id=dialogue.get("user_id", ""),
            start_time=meta.get("start_time", ""),
            end_time=meta.get("end_time", ""),
            turn_count=len(turns),
            file_path=dialogue.get("file_path", "")
        ))
    return result

@app.get("/api/episodes", response_model=List[EpisodeResponse])
async def get_episodes():
    """获取所有episodes"""
    if not data_loader:
        return []
    
    episodes_data = data_loader.get_all_episodes()
    result = []
    for episode_data in episodes_data:
        dialogue_id = episode_data.get("dialogue_id", "")
        episodes = episode_data.get("episodes", [])
        for episode in episodes:
            result.append(EpisodeResponse(
                episode_id=episode.get("episode_id", ""),
                dialogue_id=dialogue_id,
                turn_span=episode.get("turn_span", []),
                segmentation_reason=episode.get("segmentation_reason", [])
            ))
    return result

@app.get("/api/qualifications", response_model=List[QualificationResponse])
async def get_qualifications():
    """获取所有qualifications"""
    if not data_loader:
        return []
    
    qualifications_data = data_loader.get_all_qualifications()
    result = []
    for qual_data in qualifications_data:
        dialogue_id = qual_data.get("dialogue_id", "")
        qualifications = qual_data.get("qualifications", [])
        for qual in qualifications:
            result.append(QualificationResponse(
                episode_id=qual.get("episode_id", ""),
                dialogue_id=dialogue_id,
                scene_potential_score=qual.get("scene_potential_score", {}),
                decision=qual.get("decision", ""),
                rationale=qual.get("rationale", {})
            ))
    return result

@app.get("/api/scenes", response_model=List[SceneResponse])
async def get_scenes():
    """获取所有scenes"""
    if not data_loader:
        return []
    
    scenes = data_loader.get_all_scenes()
    result = []
    for scene in scenes:
        result.append(SceneResponse(
            scene_id=scene.get("scene_id", ""),
            user_id=scene.get("user_id", ""),
            scene_type=scene.get("scene_type", ""),
            content_type=scene.get("content_type", ""),
            diary=scene.get("diary", ""),
            intent=scene.get("intent", ""),
            confidence=scene.get("confidence", 0.0)
        ))
    return result

@app.get("/api/stats", response_model=StatsResponse)
async def get_stats():
    """获取统计信息"""
    if not data_loader:
        return StatsResponse(
            total_dialogues=0,
            total_episodes=0,
            total_qualifications=0,
            total_scenes=0,
            loaded_at=datetime.now().isoformat(),
            dialogues_by_user={},
            episodes_by_dialogue={},
            scenes_by_user={},
            score_distribution={}
        )
    
    stats = data_loader.get_stats()
    return StatsResponse(**stats)

@app.get("/api/episode_situation", response_model=EpisodeSituationResponse)
async def get_episode_situation():
    """获取episode_situation数据"""
    if not data_loader:
        return {"error": "数据加载器未初始化"}
    
    episode_situation = data_loader.get_episode_situation()
    if not episode_situation:
        return {"error": "episode_situation数据未加载"}
    
    return EpisodeSituationResponse(**episode_situation)

@app.get("/api/episode_situation/{dialogue_id}/{episode_id}")
async def get_episode_situation_by_ids(dialogue_id: str, episode_id: str):
    """获取特定episode的situation信息"""
    if not data_loader:
        return {"error": "数据加载器未初始化"}
    
    situation = data_loader.get_episode_situation_by_ids(dialogue_id, episode_id)
    if not situation:
        return {"error": f"Episode '{episode_id}' 在dialogue '{dialogue_id}' 的situation信息不存在"}
    
    return situation

@app.get("/api/dialogue/{dialogue_id}", response_model=DialogueDetailResponse)
async def get_dialogue_detail(dialogue_id: str):
    """获取特定dialogue及其详细信息"""
    if not data_loader:
        return {"error": "数据加载器未初始化"}
    
    dialogue_detail = data_loader.get_dialogue_with_details(dialogue_id)
    if not dialogue_detail:
        return {"error": f"Dialogue '{dialogue_id}' 不存在"}
    
    return DialogueDetailResponse(**dialogue_detail)

@app.get("/api/scene/{scene_id}")
async def get_scene_detail(scene_id: str):
    """获取特定scene信息"""
    if not data_loader:
        return {"error": "数据加载器未初始化"}
    
    scene = data_loader.get_scene_by_id(scene_id)
    if not scene:
        return {"error": f"Scene '{scene_id}' 不存在"}
    
    return scene

@app.get("/api/episode/{dialogue_id}/{episode_id}", response_model=EpisodeDetailResponse)
async def get_episode_detail(dialogue_id: str, episode_id: str):
    """获取特定episode及其评分信息"""
    if not data_loader:
        return {"error": "数据加载器未初始化"}
    
    episode_detail = data_loader.get_episode_with_details(dialogue_id, episode_id)
    if not episode_detail:
        return {"error": f"Episode '{episode_id}' 在dialogue '{dialogue_id}' 中不存在"}
    
    return EpisodeDetailResponse(**episode_detail)

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
                "stats": data_loader.get_stats()
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
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")