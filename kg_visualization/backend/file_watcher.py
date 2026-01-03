#!/usr/bin/env python3
"""
文件系统监控模块
监控KG数据目录的变化，触发数据重新加载
"""

import logging
import time
from pathlib import Path
from typing import Callable, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

logger = logging.getLogger(__name__)

class KGFileEventHandler(FileSystemEventHandler):
    """KG文件事件处理器"""
    
    def __init__(self, data_dir: Path, on_change_callback: Callable):
        """
        初始化事件处理器
        
        Args:
            data_dir: 监控的数据目录
            on_change_callback: 文件变化时的回调函数
        """
        self.data_dir = data_dir
        self.on_change_callback = on_change_callback
        self.last_event_time = 0
        self.debounce_seconds = 1.0  # 防抖时间（秒）
    
    def on_created(self, event: FileSystemEvent):
        """文件创建事件"""
        if self._is_kg_file(event.src_path):
            self._handle_change("created", event.src_path)
    
    def on_modified(self, event: FileSystemEvent):
        """文件修改事件"""
        if self._is_kg_file(event.src_path):
            self._handle_change("modified", event.src_path)
    
    def on_deleted(self, event: FileSystemEvent):
        """文件删除事件"""
        if self._is_kg_file(event.src_path):
            self._handle_change("deleted", event.src_path)
    
    def _is_kg_file(self, file_path: str) -> bool:
        """检查是否为KG候选文件"""
        path = Path(file_path)
        return path.suffix == ".json" and "kg_candidate" in path.name
    
    def _handle_change(self, change_type: str, file_path: str):
        """处理文件变化事件（带防抖）"""
        current_time = time.time()
        if current_time - self.last_event_time < self.debounce_seconds:
            logger.debug(f"忽略重复事件: {change_type} {file_path}")
            return
        
        self.last_event_time = current_time
        logger.info(f"检测到文件变化: {change_type} {file_path}")
        
        # 调用回调函数
        try:
            self.on_change_callback(change_type, file_path)
        except Exception as e:
            logger.error(f"处理文件变化回调失败: {e}")

class KGFileWatcher:
    """KG文件监控器"""
    
    def __init__(self, data_dir: str, on_change_callback: Callable):
        """
        初始化文件监控器
        
        Args:
            data_dir: 监控的数据目录路径
            on_change_callback: 文件变化时的回调函数
        """
        self.data_dir = Path(data_dir)
        self.on_change_callback = on_change_callback
        self.observer: Optional[Observer] = None
        self.event_handler: Optional[KGFileEventHandler] = None
    
    def start(self):
        """启动文件监控"""
        if not self.data_dir.exists():
            logger.error(f"❌ 监控目录不存在: {self.data_dir}")
            return False
        
        try:
            self.event_handler = KGFileEventHandler(self.data_dir, self.on_change_callback)
            self.observer = Observer()
            self.observer.schedule(self.event_handler, str(self.data_dir), recursive=False)
            self.observer.start()
            logger.info(f"✅ 开始监控目录: {self.data_dir}")
            logger.info(f"   目录路径: {self.data_dir.absolute()}")
            logger.info(f"   监控模式: 非递归 (仅顶层目录)")
            logger.info(f"   文件过滤器: *.json 且包含 'kg_candidate'")
            logger.info(f"   防抖时间: {self.event_handler.debounce_seconds}秒")
            return True
        except Exception as e:
            logger.error(f"❌ 启动文件监控失败: {e}")
            logger.error(f"   错误类型: {type(e).__name__}")
            import traceback
            logger.error(f"   堆栈跟踪: {traceback.format_exc()}")
            return False
    
    def stop(self):
        """停止文件监控"""
        if self.observer:
            self.observer.stop()
            self.observer.join()
            logger.info("文件监控已停止")
    
    def is_running(self) -> bool:
        """检查监控是否在运行"""
        return self.observer is not None and self.observer.is_alive()