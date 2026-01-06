// Memory数据可视化前端应用

class MemoryVisualization {
    constructor() {
        this.apiBaseUrl = 'http://localhost:8001';
        this.wsUrl = 'ws://localhost:8001/ws';
        this.ws = null;
        this.isConnected = false;
        
        // 数据缓存
        this.dialogues = [];
        this.episodes = [];
        this.scenes = [];
        this.stats = {};
        
        this.init();
    }
    
    init() {
        console.log('MemoryVisualization初始化开始');
        this.bindEvents();
        this.connectWebSocket();
        this.loadInitialData();
        console.log('MemoryVisualization初始化完成');
    }
    
    bindEvents() {
        // 标签页切换
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const tabId = e.target.dataset.tab;
                this.switchTab(tabId);
            });
        });
        
        // 搜索功能
        document.getElementById('search-btn').addEventListener('click', () => {
            this.handleSearch();
        });
        
        document.getElementById('search-input').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.handleSearch();
            }
        });
        
        // 刷新按钮
        document.getElementById('refresh-btn').addEventListener('click', () => {
            this.loadInitialData();
        });
    }
    
    connectWebSocket() {
        try {
            this.ws = new WebSocket(this.wsUrl);
            
            this.ws.onopen = () => {
                console.log('WebSocket连接已建立');
                this.isConnected = true;
                this.updateConnectionStatus(true);
            };
            
            this.ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                this.handleWebSocketMessage(data);
            };
            
            this.ws.onerror = (error) => {
                console.error('WebSocket错误:', error);
                this.isConnected = false;
                this.updateConnectionStatus(false);
            };
            
            this.ws.onclose = () => {
                console.log('WebSocket连接已关闭');
                this.isConnected = false;
                this.updateConnectionStatus(false);
            };
            
        } catch (error) {
            console.error('创建WebSocket连接失败:', error);
            this.isConnected = false;
            this.updateConnectionStatus(false);
        }
    }
    
    handleWebSocketMessage(data) {
        const timestamp = new Date().toLocaleTimeString();
        
        switch (data.type) {
            case 'initial_data':
                console.log('收到初始数据');
                this.stats = data.stats;
                this.updateStats();
                break;
                
            case 'data_updated':
                console.log('数据已更新');
                this.addUpdateLog('数据已更新');
                this.stats = data.stats;
                this.updateStats();
                this.loadInitialData();
                break;
        }
        
        document.getElementById('last-update').innerHTML = `<i class="fas fa-clock"></i> 最后更新: ${timestamp}`;
    }
    
    async loadInitialData() {
        try {
            this.updateSystemStatus('正在加载数据...');
            
            const [statsRes, dialoguesRes, episodesRes, scenesRes] = await Promise.all([
                fetch(`${this.apiBaseUrl}/api/stats`),
                fetch(`${this.apiBaseUrl}/api/dialogues`),
                fetch(`${this.apiBaseUrl}/api/episodes`),
                fetch(`${this.apiBaseUrl}/api/scenes`)
            ]);
            
            if (!statsRes.ok || !dialoguesRes.ok || !episodesRes.ok || !scenesRes.ok) {
                throw new Error('API请求失败');
            }
            
            this.stats = await statsRes.json();
            this.dialogues = await dialoguesRes.json();
            this.episodes = await episodesRes.json();
            this.scenes = await scenesRes.json();
            
            this.updateStats();
            this.updateDataLists();
            
            this.updateSystemStatus('数据加载完成');
            this.addUpdateLog('数据加载完成');
            
        } catch (error) {
            console.error('加载数据失败:', error);
            this.updateSystemStatus('数据加载失败');
            this.addUpdateLog(`数据加载失败: ${error.message}`);
        }
    }
    
    updateStats() {
        // 更新统计卡片
        document.getElementById('stat-dialogues').textContent = this.stats.total_dialogues || 0;
        document.getElementById('stat-episodes').textContent = this.stats.total_episodes || 0;
        document.getElementById('stat-qualifications').textContent = this.stats.total_qualifications || 0;
        document.getElementById('stat-scenes').textContent = this.stats.total_scenes || 0;
        
        // 更新API状态
        document.getElementById('api-status').textContent = this.isConnected ? '已连接' : '未连接';
        document.getElementById('api-status').className = this.isConnected ? 'status-connected' : 'status-disconnected';
    }
    
    updateConnectionStatus(connected) {
        const statusElement = document.getElementById('connection-status');
        if (connected) {
            statusElement.innerHTML = '<i class="fas fa-circle"></i> 已连接';
            statusElement.className = 'status-connected';
        } else {
            statusElement.innerHTML = '<i class="fas fa-circle"></i> 连接断开';
            statusElement.className = 'status-disconnected';
        }
    }
    
    updateSystemStatus(status) {
        document.getElementById('system-status').textContent = status;
    }
    
    addUpdateLog(message) {
        const updatesList = document.getElementById('updates-list');
        const timestamp = new Date().toLocaleTimeString();
        const updateItem = document.createElement('div');
        updateItem.className = 'update-item';
        updateItem.innerHTML = `<i class="fas fa-info-circle"></i> [${timestamp}] ${message}`;
        
        updatesList.insertBefore(updateItem, updatesList.firstChild);
        
        if (updatesList.children.length > 10) {
            updatesList.removeChild(updatesList.lastChild);
        }
    }
    
    updateDataLists() {
        this.updateDialoguesList();
        this.updateEpisodesList();
        this.updateScenesList();
    }
    
    updateDialoguesList() {
        const listElement = document.getElementById('dialogues-list');
        listElement.innerHTML = '';
        
        if (this.dialogues.length === 0) {
            listElement.innerHTML = '<div class="empty-state"><i class="fas fa-comments fa-3x"></i><p>暂无dialogues数据</p></div>';
            return;
        }
        
        this.dialogues.forEach(dialogue => {
            const item = this.createDialogueItem(dialogue);
            listElement.appendChild(item);
        });
    }
    
    createDialogueItem(dialogue) {
        const item = document.createElement('div');
        item.className = 'data-item';
        item.dataset.id = dialogue.dialogue_id;
        
        const startTime = new Date(dialogue.start_time).toLocaleString();
        const turnCount = dialogue.turn_count || 0;
        
        item.innerHTML = `
            <div class="data-item-header">
                <div class="data-item-id">${dialogue.dialogue_id}</div>
                <div class="data-item-meta">${startTime}</div>
            </div>
            <div class="data-item-content">
                <div><strong>用户:</strong> ${dialogue.user_id}</div>
                <div><strong>轮次:</strong> ${turnCount} 轮</div>
            </div>
        `;
        
        item.addEventListener('click', () => {
            this.selectDialogue(dialogue.dialogue_id);
        });
        
        return item;
    }
    
    updateEpisodesList() {
        const listElement = document.getElementById('episodes-list');
        listElement.innerHTML = '';
        
        if (this.episodes.length === 0) {
            listElement.innerHTML = '<div class="empty-state"><i class="fas fa-layer-group fa-3x"></i><p>暂无episodes数据</p></div>';
            return;
        }
        
        const allEpisodes = [];
        
        // 检查数据格式：如果是嵌套结构（有episodes属性）
        const firstItem = this.episodes[0];
        if (firstItem && firstItem.episodes !== undefined) {
            // 嵌套结构：{dialogue_id: '...', episodes: [...]}
            this.episodes.forEach(episodeData => {
                const episodes = episodeData.episodes || [];
                episodes.forEach(episode => {
                    allEpisodes.push({
                        ...episode,
                        dialogue_id: episodeData.dialogue_id || episodeData.dialogue_id
                    });
                });
            });
        } else {
            // 扁平化结构：直接就是episode对象数组
            allEpisodes.push(...this.episodes);
        }
        
        allEpisodes.forEach(episode => {
            const item = this.createEpisodeItem(episode);
            listElement.appendChild(item);
        });
    }
    
    createEpisodeItem(episode) {
        const item = document.createElement('div');
        item.className = 'data-item';
        // 使用组合ID确保唯一性：dialogue_id + '_' + episode_id
        const uniqueId = `${episode.dialogue_id}_${episode.episode_id}`;
        item.dataset.id = uniqueId;
        item.dataset.episodeId = episode.episode_id;
        item.dataset.dialogueId = episode.dialogue_id;
        
        const turnSpan = episode.turn_span || [0, 0];
        
        item.innerHTML = `
            <div class="data-item-header">
                <div class="data-item-id">${episode.episode_id}</div>
                <div class="data-item-meta">对话: ${episode.dialogue_id}</div>
            </div>
            <div class="data-item-content">
                <div><strong>轮次范围:</strong> ${turnSpan[0]} - ${turnSpan[1]}</div>
            </div>
        `;
        
        item.addEventListener('click', () => {
            this.selectEpisode(episode.episode_id, episode.dialogue_id);
        });
        
        return item;
    }
    
    updateScenesList() {
        const listElement = document.getElementById('scenes-list');
        listElement.innerHTML = '';
        
        if (this.scenes.length === 0) {
            listElement.innerHTML = '<div class="empty-state"><i class="fas fa-scroll fa-3x"></i><p>暂无scenes数据</p></div>';
            return;
        }
        
        this.scenes.forEach(scene => {
            const item = this.createSceneItem(scene);
            listElement.appendChild(item);
        });
    }
    
    createSceneItem(scene) {
        const item = document.createElement('div');
        item.className = 'data-item';
        item.dataset.id = scene.scene_id;
        
        const confidence = (scene.confidence * 100).toFixed(1);
        
        item.innerHTML = `
            <div class="data-item-header">
                <div class="data-item-id">${scene.scene_id}</div>
                <div class="data-item-meta">置信度: ${confidence}%</div>
            </div>
            <div class="data-item-content">
                <div><strong>用户:</strong> ${scene.user_id}</div>
                <div><strong>类型:</strong> ${scene.scene_type}</div>
            </div>
        `;
        
        item.addEventListener('click', () => {
            this.selectScene(scene.scene_id);
        });
        
        return item;
    }
    
    switchTab(tabId) {
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.classList.remove('active');
        });
        document.querySelector(`.tab-btn[data-tab="${tabId}"]`).classList.add('active');
        
        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.remove('active');
        });
        document.getElementById(tabId).classList.add('active');
    }
    
    selectDialogue(dialogueId) {
        document.querySelectorAll('#dialogues-list .data-item').forEach(item => {
            item.classList.remove('selected');
            if (item.dataset.id === dialogueId) {
                item.classList.add('selected');
            }
        });
        
        this.loadDialogueDetail(dialogueId);
    }
    
    async loadDialogueDetail(dialogueId) {
        try {
            const response = await fetch(`${this.apiBaseUrl}/api/dialogue/${dialogueId}`);
            if (!response.ok) throw new Error('加载详情失败');
            
            const detail = await response.json();
            this.displayDialogueDetail(detail);
            
        } catch (error) {
            console.error('加载dialogue详情失败:', error);
            document.getElementById('dialogue-detail').innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-exclamation-triangle fa-3x"></i>
                    <p>加载详情失败</p>
                </div>
            `;
        }
    }
    
    displayDialogueDetail(detail) {
        const detailElement = document.getElementById('dialogue-detail');
        const dialogue = detail.dialogue;
        
        let html = `
            <div class="detail-header">
                <h3>${dialogue.dialogue_id}</h3>
                <div class="detail-meta">
                    <span><i class="fas fa-user"></i> ${dialogue.user_id}</span>
                    <span><i class="fas fa-clock"></i> ${new Date(dialogue.meta.start_time).toLocaleString()}</span>
                    <span><i class="fas fa-comments"></i> ${dialogue.turns.length} 轮对话</span>
                </div>
            </div>
            
            <div class="detail-section">
                <h4><i class="fas fa-comment-dots"></i> 对话内容</h4>
                <div class="dialogue-content">
        `;
        
        dialogue.turns.forEach(turn => {
            html += `
                <div class="turn-item ${turn.speaker === 'ZQR' ? 'user-turn' : 'ai-turn'}">
                    <div class="turn-speaker">${turn.speaker}:</div>
                    <div class="turn-text">${turn.text}</div>
                </div>
            `;
        });
        
        html += `
                </div>
            </div>
        `;
        
        detailElement.innerHTML = html;
    }
    
    selectEpisode(episodeId, dialogueId) {
        document.querySelectorAll('#episodes-list .data-item').forEach(item => {
            item.classList.remove('selected');
            // 检查组合ID或单独的属性
            const uniqueId = `${dialogueId}_${episodeId}`;
            if (item.dataset.id === uniqueId ||
                (item.dataset.episodeId === episodeId && item.dataset.dialogueId === dialogueId)) {
                item.classList.add('selected');
            }
        });
        
        this.displayEpisodeDetail(episodeId, dialogueId);
    }
    
    async displayEpisodeDetail(episodeId, dialogueId) {
        try {
            let episode = null;
            
            // 检查数据格式：如果是嵌套结构（有episodes属性）
            const firstItem = this.episodes[0];
            if (firstItem && firstItem.episodes !== undefined) {
                // 嵌套结构：{dialogue_id: '...', episodes: [...]}
                for (const episodeData of this.episodes) {
                    if (episodeData.dialogue_id === dialogueId) {
                        const found = episodeData.episodes.find(e => e.episode_id === episodeId);
                        if (found) {
                            episode = { ...found, dialogue_id: dialogueId };
                            break;
                        }
                    }
                }
            } else {
                // 扁平化结构：直接就是episode对象数组
                episode = this.episodes.find(e => e.episode_id === episodeId && e.dialogue_id === dialogueId);
            }
            
            if (!episode) {
                throw new Error('Episode未找到');
            }
            
            const detailElement = document.getElementById('episode-detail');
            let html = `
                <div class="detail-header">
                    <h3>${episode.episode_id}</h3>
                    <div class="detail-meta">
                        <span><i class="fas fa-comments"></i> ${episode.dialogue_id}</span>
                        <span><i class="fas fa-exchange-alt"></i> 轮次 ${episode.turn_span[0]} - ${episode.turn_span[1]}</span>
                    </div>
                </div>
            `;
            
            const dialogue = this.dialogues.find(d => d.dialogue_id === dialogueId);
            if (dialogue && dialogue.turns) {
                const [start, end] = episode.turn_span;
                const episodeTurns = dialogue.turns.slice(start, end + 1);
                
                html += `
                    <div class="detail-section">
                        <h4><i class="fas fa-comment-dots"></i> 对话内容 (${episodeTurns.length} 轮)</h4>
                        <div class="dialogue-content">
                `;
                
                episodeTurns.forEach(turn => {
                    html += `
                        <div class="turn-item ${turn.speaker === 'ZQR' ? 'user-turn' : 'ai-turn'}">
                            <div class="turn-speaker">${turn.speaker}:</div>
                            <div class="turn-text">${turn.text}</div>
                        </div>
                    `;
                });
                
                html += `
                        </div>
                    </div>
                `;
            }
            
            detailElement.innerHTML = html;
            
        } catch (error) {
            console.error('显示episode详情失败:', error);
            document.getElementById('episode-detail').innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-exclamation-triangle fa-3x"></i>
                    <p>加载详情失败</p>
                </div>
            `;
        }
    }
    
    selectScene(sceneId) {
        document.querySelectorAll('#scenes-list .data-item').forEach(item => {
            item.classList.remove('selected');
            if (item.dataset.id === sceneId) {
                item.classList.add('selected');
            }
        });
        
        this.displaySceneDetail(sceneId);
    }
    
    async displaySceneDetail(sceneId) {
        try {
            const response = await fetch(`${this.apiBaseUrl}/api/scene/${sceneId}`);
            if (!response.ok) throw new Error('加载详情失败');
            
            const scene = await response.json();
            
            const detailElement = document.getElementById('scene-detail');
            const confidence = (scene.confidence * 100).toFixed(1);
            
            let html = `
                <div class="detail-header">
                    <h3>${scene.scene_id}</h3>
                    <div class="detail-meta">
                        <span><i class="fas fa-user"></i> ${scene.user_id}</span>
                        <span><i class="fas fa-chart-line"></i> 置信度: ${confidence}%</span>
                    </div>
                </div>
                
                <div class="detail-section">
                    <h4><i class="fas fa-book"></i> 日记</h4>
                    <div class="diary-content">${scene.diary || '无'}</div>
                </div>
            `;
            
            detailElement.innerHTML = html;
            
        } catch (error) {
            console.error('显示scene详情失败:', error);
            document.getElementById('scene-detail').innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-exclamation-triangle fa-3x"></i>
                    <p>加载详情失败</p>
                </div>
            `;
        }
    }
    
    handleSearch() {
        const query = document.getElementById('search-input').value.trim().toLowerCase();
        if (!query) return;

        const activeTab = document.querySelector('.tab-btn.active').dataset.tab;

        switch (activeTab) {
            case 'dialogues-tab':
                this.searchDialogues(query);
                break;
            case 'episodes-tab':
                this.searchEpisodes(query);
                break;
            case 'scenes-tab':
                this.searchScenes(query);
                break;
        }
    }
    
    searchDialogues(query) {
        const filtered = this.dialogues.filter(dialogue => {
            return dialogue.dialogue_id.toLowerCase().includes(query) ||
                   dialogue.user_id.toLowerCase().includes(query) ||
                   (dialogue.turns && dialogue.turns.some(turn =>
                       turn.text.toLowerCase().includes(query)
                   ));
        });
        
        this.displaySearchResults('dialogues-list', filtered, this.createDialogueItem.bind(this));
    }
    
    searchEpisodes(query) {
        const allEpisodes = [];
        this.episodes.forEach(episodeData => {
            const episodes = episodeData.episodes || [];
            episodes.forEach(episode => {
                allEpisodes.push({
                    ...episode,
                    dialogue_id: episodeData.dialogue_id
                });
            });
        });
        
        const filtered = allEpisodes.filter(episode => {
            return episode.episode_id.toLowerCase().includes(query) ||
                   episode.dialogue_id.toLowerCase().includes(query);
        });
        
        this.displaySearchResults('episodes-list', filtered, this.createEpisodeItem.bind(this));
    }
    
    searchScenes(query) {
        const filtered = this.scenes.filter(scene => {
            return scene.scene_id.toLowerCase().includes(query) ||
                   scene.user_id.toLowerCase().includes(query) ||
                   scene.scene_type.toLowerCase().includes(query) ||
                   (scene.diary && scene.diary.toLowerCase().includes(query));
        });
        
        this.displaySearchResults('scenes-list', filtered, this.createSceneItem.bind(this));
    }
    
    displaySearchResults(listId, items, createItemFunc) {
        const listElement = document.getElementById(listId);
        listElement.innerHTML = '';
        
        if (items.length === 0) {
            listElement.innerHTML = '<div class="empty-state"><i class="fas fa-search fa-3x"></i><p>未找到匹配的结果</p></div>';
            return;
        }
        
        items.forEach(item => {
            const itemElement = createItemFunc(item);
            listElement.appendChild(itemElement);
        });
    }
}

// 初始化应用
document.addEventListener('DOMContentLoaded', () => {
    window.memoryVisualization = new MemoryVisualization();
});
