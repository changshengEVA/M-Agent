// Memory数据可视化前端应用 - 模块化版本
import WebSocketManager from './modules/websocket-manager.js';
import DataLoader from './modules/data-loader.js';
import UIUpdater from './modules/ui-updater.js';
import ItemRenderer from './modules/item-renderer.js';
import DetailViewer from './modules/detail-viewer.js';
import SearchManager from './modules/search-manager.js';
import EpisodePoolRenderer from './modules/episode-pool-renderer.js';
import ChartManager from './modules/chart-manager.js';

class MemoryVisualization {
    constructor() {
        this.apiBaseUrl = 'http://localhost:8001';
        
        // 数据缓存
        this.dialogues = [];
        this.episodes = [];
        this.scenes = [];
        this.stats = {};
        this.episodeSituation = {};
        
        // 初始化模块
        this.initModules();
        
        // 初始化应用
        this.init();
    }
    
    initModules() {
        // 创建模块实例
        this.dataLoader = new DataLoader(this.apiBaseUrl);
        this.uiUpdater = new UIUpdater();
        this.itemRenderer = new ItemRenderer(
            this.selectDialogue.bind(this),
            this.selectEpisode.bind(this),
            this.selectScene.bind(this)
        );
        this.detailViewer = new DetailViewer(this.dataLoader);
        this.searchManager = new SearchManager(
            this.dataLoader,
            this.itemRenderer,
            this.uiUpdater
        );
        this.wsManager = new WebSocketManager(this.apiBaseUrl);
        // 创建episode元素池渲染器
        this.episodePoolRenderer = new EpisodePoolRenderer(
            this.selectEpisode.bind(this)
        );
        // 创建图表管理器
        this.chartManager = new ChartManager();
    }
    
    init() {
        console.log('MemoryVisualization初始化开始');
        this.bindEvents();
        this.connectWebSocket();
        this.initCharts();
        this.loadInitialData();
        console.log('MemoryVisualization初始化完成');
    }
    
    bindEvents() {
        // 标签页切换
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const tabId = e.target.dataset.tab;
                this.uiUpdater.switchTab(tabId);
            });
        });
        
        // 搜索功能
        this.searchManager.bindSearchEvents((query) => {
            const activeTab = this.searchManager.getActiveTab();
            this.searchManager.handleSearch(
                query, 
                activeTab, 
                this.dialogues, 
                this.episodes, 
                this.scenes
            );
        });
        
        // 刷新按钮
        const refreshBtn = document.getElementById('refresh-btn');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => {
                this.loadInitialData();
            });
        }
    }
    
    connectWebSocket() {
        this.wsManager.connect(
            () => {
                // onOpen
                this.uiUpdater.updateConnectionStatus(true);
            },
            (data) => {
                // onMessage
                this.handleWebSocketMessage(data);
            },
            (error) => {
                // onError
                console.error('WebSocket错误:', error);
                this.uiUpdater.updateConnectionStatus(false);
            },
            () => {
                // onClose
                this.uiUpdater.updateConnectionStatus(false);
            }
        );
    }
    
    handleWebSocketMessage(data) {
        const timestamp = new Date().toLocaleTimeString();
        
        switch (data.type) {
            case 'initial_data':
                console.log('收到初始数据');
                this.stats = data.stats;
                this.uiUpdater.updateStats(this.stats, this.wsManager.isConnected);
                break;
                
            case 'data_updated':
                console.log('数据已更新');
                this.uiUpdater.addUpdateLog('数据已更新');
                this.stats = data.stats;
                this.uiUpdater.updateStats(this.stats, this.wsManager.isConnected);
                this.loadInitialData();
                break;
        }
        
        this.uiUpdater.updateLastUpdateTime();
    }
    
    // 初始化图表
    initCharts() {
        if (this.chartManager) {
            this.chartManager.initCharts();
        }
    }
    
    async loadInitialData() {
        try {
            this.uiUpdater.updateSystemStatus('正在加载数据...');
            
            const { stats, dialogues, episodes, qualifications, scenes, episodeSituation } = await this.dataLoader.loadAllData();
            
            this.stats = stats;
            this.dialogues = dialogues;
            this.episodes = episodes;
            this.qualifications = qualifications;
            this.scenes = scenes;
            this.episodeSituation = episodeSituation;
            
            // 更新UI
            this.uiUpdater.updateStats(this.stats, this.wsManager.isConnected);
            this.updateDataLists();
            this.updateCharts();
            
            this.uiUpdater.updateSystemStatus('数据加载完成');
            this.uiUpdater.addUpdateLog('数据加载完成');
            
        } catch (error) {
            console.error('加载数据失败:', error);
            this.uiUpdater.updateSystemStatus('数据加载失败');
            this.uiUpdater.addUpdateLog(`数据加载失败: ${error.message}`);
        }
    }
    
    // 更新图表
    updateCharts() {
        if (this.chartManager) {
            // 更新策略分布图表
            this.chartManager.updateStrategyDistribution(this.episodeSituation);
            
            // 更新用户分布图表
            this.chartManager.updateUserDistribution(this.dialogues);
        }
    }
    
    updateDataLists() {
        this.itemRenderer.renderDialoguesList(this.dialogues, 'dialogues-list');
        this.itemRenderer.renderEpisodesList(this.episodes, 'episodes-list');
        this.itemRenderer.renderScenesList(this.scenes, 'scenes-list');
        
        // 渲染episode元素池
        if (this.episodePoolRenderer) {
            this.episodePoolRenderer.setEpisodes(this.episodes, this.qualifications, this.episodeSituation);
        }
    }
    
    // 选择事件处理
    selectDialogue(dialogueId) {
        this.uiUpdater.selectItem('dialogues-list', dialogueId);
        this.loadDialogueDetail(dialogueId);
    }
    
    selectEpisode(episodeId, dialogueId) {
        this.uiUpdater.selectEpisode(episodeId, dialogueId);
        this.displayEpisodeDetail(episodeId, dialogueId);
    }
    
    selectScene(sceneId) {
        this.uiUpdater.selectItem('scenes-list', sceneId);
        this.displaySceneDetail(sceneId);
    }
    
    // 详情加载和显示
    async loadDialogueDetail(dialogueId) {
        try {
            const detail = await this.dataLoader.loadDialogueDetail(dialogueId);
            this.detailViewer.displayDialogueDetail(detail);
        } catch (error) {
            console.error('加载dialogue详情失败:', error);
            this.detailViewer.displayError('dialogue-detail', '加载详情失败');
        }
    }
    
    async displayEpisodeDetail(episodeId, dialogueId) {
        try {
            // 使用新的API获取episode详情（包含评分）
            const episodeDetail = await this.dataLoader.loadEpisodeDetail(dialogueId, episodeId);
            
            // 获取episode的situation信息
            let episodeSituation = null;
            if (this.episodeSituation && this.episodeSituation.episodes) {
                const episodeKey = `${dialogueId}:${episodeId}`;
                episodeSituation = this.episodeSituation.episodes[episodeKey];
            }
            
            this.detailViewer.displayEpisodeDetailWithScore(episodeDetail, episodeSituation);
        } catch (error) {
            console.error('显示episode详情失败:', error);
            // 如果新API失败，回退到旧的方法
            try {
                const episode = this.detailViewer.findEpisodeInData(this.episodes, episodeId, dialogueId);
                if (!episode) {
                    throw new Error('Episode未找到');
                }
                this.detailViewer.displayEpisodeDetail(episode, this.dialogues);
            } catch (fallbackError) {
                this.detailViewer.displayError('episode-detail', '加载详情失败');
            }
        }
    }
    
    async displaySceneDetail(sceneId) {
        try {
            const scene = await this.dataLoader.loadSceneDetail(sceneId);
            this.detailViewer.displaySceneDetail(scene);
        } catch (error) {
            console.error('显示scene详情失败:', error);
            this.detailViewer.displayError('scene-detail', '加载详情失败');
        }
    }
}

// 初始化应用
document.addEventListener('DOMContentLoaded', () => {
    window.memoryVisualization = new MemoryVisualization();
});