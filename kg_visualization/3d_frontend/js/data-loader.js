/**
 * 数据加载器
 * 负责从后端API加载三维知识图谱数据
 */

class DataLoader {
    constructor() {
        this.baseUrl = window.location.origin;
        this.wsUrl = this._getWebSocketUrl();
        this.ws = null;
        this.isConnected = false;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        
        // 事件监听器
        this.eventListeners = new Map();
        
        // 缓存数据
        this.cachedData = {
            graph: null,
            stats: null,
            memoryInfo: null
        };
    }
    
    /**
     * 获取WebSocket URL
     */
    _getWebSocketUrl() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${protocol}//${window.location.host}/ws`;
    }
    
    /**
     * 连接WebSocket
     */
    connectWebSocket() {
        if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
            console.log('WebSocket已经连接或正在连接');
            return;
        }
        
        console.log(`连接WebSocket: ${this.wsUrl}`);
        this.trigger('status', { type: 'info', message: `连接WebSocket: ${this.wsUrl}` });
        
        try {
            this.ws = new WebSocket(this.wsUrl);
            
            this.ws.onopen = () => {
                console.log('WebSocket连接已建立');
                this.isConnected = true;
                this.reconnectAttempts = 0;
                this.trigger('status', { type: 'success', message: 'WebSocket连接成功' });
                this.trigger('connected', {});
            };
            
            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    console.log('收到WebSocket消息:', data.type);
                    this._handleWebSocketMessage(data);
                } catch (e) {
                    console.error('解析WebSocket消息失败:', e);
                    this.trigger('error', { type: 'parse', error: e });
                }
            };
            
            this.ws.onclose = (event) => {
                console.log('WebSocket连接已关闭', event.code, event.reason);
                this.isConnected = false;
                this.trigger('status', { 
                    type: 'error', 
                    message: `WebSocket连接断开 (代码: ${event.code})，5秒后重连...` 
                });
                
                // 尝试重新连接
                this._scheduleReconnect();
            };
            
            this.ws.onerror = (error) => {
                console.error('WebSocket错误:', error);
                this.trigger('error', { type: 'websocket', error: error });
                this.trigger('status', { 
                    type: 'error', 
                    message: 'WebSocket连接错误，检查后端服务是否运行' 
                });
            };
            
        } catch (error) {
            console.error('创建WebSocket失败:', error);
            this.trigger('error', { type: 'connection', error: error });
            this.trigger('status', { type: 'error', message: '创建WebSocket连接失败' });
            
            // 10秒后重试
            setTimeout(() => this.connectWebSocket(), 10000);
        }
    }
    
    /**
     * 安排重新连接
     */
    _scheduleReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.log('达到最大重连次数，停止重连');
            this.trigger('status', { 
                type: 'error', 
                message: '达到最大重连次数，请检查网络连接' 
            });
            return;
        }
        
        this.reconnectAttempts++;
        const delay = Math.min(5000 * this.reconnectAttempts, 30000); // 指数退避，最大30秒
        
        console.log(`第 ${this.reconnectAttempts} 次重连，${delay}ms后重试`);
        
        setTimeout(() => {
            if (!this.isConnected) {
                this.connectWebSocket();
            }
        }, delay);
    }
    
    /**
     * 处理WebSocket消息
     */
    _handleWebSocketMessage(data) {
        switch (data.type) {
            case 'initial_data':
                this.cachedData.graph = data.graph;
                this.cachedData.stats = data.stats;
                this.trigger('initialData', data);
                break;
                
            case 'data_updated':
                this.trigger('dataUpdated', data);
                // 重新加载数据
                this.loadGraphData();
                break;
                
            case 'memory_switched':
                this.trigger('memorySwitched', data);
                // 重新加载数据
                this.loadGraphData();
                break;
                
            default:
                console.log('未知消息类型:', data.type);
                this.trigger('unknownMessage', data);
        }
    }
    
    /**
     * 加载图数据
     */
    async loadGraphData() {
        try {
            this.trigger('loading', { type: 'graph' });
            
            const response = await fetch('/api/3d/graph');
            if (!response.ok) {
                throw new Error(`HTTP错误: ${response.status}`);
            }
            
            const data = await response.json();
            this.cachedData.graph = data;
            
            console.log('图数据加载成功:', data.stats);
            this.trigger('graphLoaded', data);
            
            return data;
            
        } catch (error) {
            console.error('加载图数据失败:', error);
            this.trigger('error', { type: 'load', error: error, source: 'graph' });
            throw error;
        }
    }
    
    /**
     * 加载统计信息
     */
    async loadStats() {
        try {
            this.trigger('loading', { type: 'stats' });
            
            const response = await fetch('/api/3d/stats');
            if (!response.ok) {
                throw new Error(`HTTP错误: ${response.status}`);
            }
            
            const data = await response.json();
            this.cachedData.stats = data;
            
            console.log('统计信息加载成功:', data);
            this.trigger('statsLoaded', data);
            
            return data;
            
        } catch (error) {
            console.error('加载统计信息失败:', error);
            this.trigger('error', { type: 'load', error: error, source: 'stats' });
            throw error;
        }
    }
    
    /**
     * 加载Memory信息
     */
    async loadMemoryInfo() {
        try {
            this.trigger('loading', { type: 'memory' });
            
            const response = await fetch('/api/memory/info');
            if (!response.ok) {
                throw new Error(`HTTP错误: ${response.status}`);
            }
            
            const data = await response.json();
            this.cachedData.memoryInfo = data;
            
            console.log('Memory信息加载成功:', data);
            this.trigger('memoryInfoLoaded', data);
            
            return data;
            
        } catch (error) {
            console.error('加载Memory信息失败:', error);
            this.trigger('error', { type: 'load', error: error, source: 'memory' });
            throw error;
        }
    }
    
    /**
     * 切换Memory
     */
    async switchMemory(memoryId) {
        try {
            this.trigger('loading', { type: 'switchMemory', memoryId });
            
            const response = await fetch(`/api/memory/switch/${memoryId}`, {
                method: 'POST'
            });
            
            if (!response.ok) {
                throw new Error(`HTTP错误: ${response.status}`);
            }
            
            const data = await response.json();
            
            if (data.success) {
                console.log(`切换到Memory成功: ${memoryId}`);
                this.trigger('memorySwitched', data);
                
                // 重新加载数据
                await this.loadGraphData();
                await this.loadStats();
                
                return data;
            } else {
                throw new Error(data.message || '切换Memory失败');
            }
            
        } catch (error) {
            console.error('切换Memory失败:', error);
            this.trigger('error', { type: 'switch', error: error, memoryId });
            throw error;
        }
    }
    
    /**
     * 获取实体详细信息
     */
    async getEntityDetails(entityId) {
        try {
            const response = await fetch(`/api/3d/entity/${entityId}`);
            if (!response.ok) {
                throw new Error(`HTTP错误: ${response.status}`);
            }
            
            const data = await response.json();
            return data;
            
        } catch (error) {
            console.error(`获取实体详情失败 ${entityId}:`, error);
            throw error;
        }
    }
    
    /**
     * 获取特征详细信息
     */
    async getFeatureDetails(featureId) {
        try {
            const response = await fetch(`/api/3d/feature/${featureId}`);
            if (!response.ok) {
                throw new Error(`HTTP错误: ${response.status}`);
            }
            
            const data = await response.json();
            return data;
            
        } catch (error) {
            console.error(`获取特征详情失败 ${featureId}:`, error);
            throw error;
        }
    }
    
    /**
     * 获取场景详细信息
     */
    async getSceneDetails(sceneId) {
        try {
            const response = await fetch(`/api/3d/scene/${sceneId}`);
            if (!response.ok) {
                throw new Error(`HTTP错误: ${response.status}`);
            }
            
            const data = await response.json();
            return data;
            
        } catch (error) {
            console.error(`获取场景详情失败 ${sceneId}:`, error);
            throw error;
        }
    }
    
    /**
     * 搜索节点
     */
    async searchNode(query) {
        try {
            // 首先尝试在缓存数据中搜索
            if (this.cachedData.graph) {
                const results = this._searchInCachedData(query);
                if (results.length > 0) {
                    return results;
                }
            }
            
            // 如果没有缓存数据或没找到，尝试API搜索
            // 这里可以根据需要实现具体的搜索API
            return [];
            
        } catch (error) {
            console.error('搜索节点失败:', error);
            throw error;
        }
    }
    
    /**
     * 在缓存数据中搜索
     */
    _searchInCachedData(query) {
        const results = [];
        const lowerQuery = query.toLowerCase();
        
        if (!this.cachedData.graph) {
            return results;
        }
        
        // 搜索实体
        this.cachedData.graph.entities.forEach(entity => {
            if (entity.id.toLowerCase().includes(lowerQuery) || 
                entity.label.toLowerCase().includes(lowerQuery)) {
                results.push({
                    type: 'entity',
                    id: entity.id,
                    label: entity.label,
                    data: entity
                });
            }
        });
        
        // 搜索特征
        this.cachedData.graph.features.forEach(feature => {
            if (feature.id.toLowerCase().includes(lowerQuery) || 
                feature.label.toLowerCase().includes(lowerQuery) ||
                feature.full_text.toLowerCase().includes(lowerQuery)) {
                results.push({
                    type: 'feature',
                    id: feature.id,
                    label: feature.label,
                    data: feature
                });
            }
        });
        
        // 搜索场景
        this.cachedData.graph.scenes.forEach(scene => {
            if (scene.id.toLowerCase().includes(lowerQuery) || 
                scene.label.toLowerCase().includes(lowerQuery)) {
                results.push({
                    type: 'scene',
                    id: scene.id,
                    label: scene.label,
                    data: scene
                });
            }
        });
        
        return results;
    }
    
    /**
     * 添加事件监听器
     */
    on(eventName, callback) {
        if (!this.eventListeners.has(eventName)) {
            this.eventListeners.set(eventName, []);
        }
        this.eventListeners.get(eventName).push(callback);
    }
    
    /**
     * 移除事件监听器
     */
    off(eventName, callback) {
        if (this.eventListeners.has(eventName)) {
            const listeners = this.eventListeners.get(eventName);
            const index = listeners.indexOf(callback);
            if (index > -1) {
                listeners.splice(index, 1);
            }
        }
    }
    
    /**
     * 触发事件
     */
    trigger(eventName, data) {
        if (this.eventListeners.has(eventName)) {
            this.eventListeners.get(eventName).forEach(callback => {
                try {
                    callback(data);
                } catch (error) {
                    console.error(`事件监听器错误 (${eventName}):`, error);
                }
            });
        }
    }
    
    /**
     * 断开WebSocket连接
     */
    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        this.isConnected = false;
    }
    
    /**
     * 获取连接状态
     */
    getConnectionStatus() {
        return {
            isConnected: this.isConnected,
            reconnectAttempts: this.reconnectAttempts,
            maxReconnectAttempts: this.maxReconnectAttempts
        };
    }
    
    /**
     * 获取缓存数据
     */
    getCachedData() {
        return { ...this.cachedData };
    }
}

// 创建全局实例
const dataLoader = new DataLoader();

// 导出
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DataLoader;
    module.exports.dataLoader = dataLoader;
}