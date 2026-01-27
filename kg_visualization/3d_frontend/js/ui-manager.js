/**
 * UI管理器
 * 负责管理用户界面和交互
 */

class UIManager {
    constructor(dataLoader, sceneManager) {
        this.dataLoader = dataLoader;
        this.sceneManager = sceneManager;
        
        // UI元素引用
        this.elements = {
            // 状态显示
            connectionStatus: document.getElementById('connection-status'),
            lastUpdate: document.getElementById('last-update'),
            systemStatus: document.getElementById('system-status'),
            apiStatus: document.getElementById('api-status'),
            
            // 统计信息
            statEntities: document.getElementById('stat-entities'),
            statFeatures: document.getElementById('stat-features'),
            statScenes: document.getElementById('stat-scenes'),
            statConnections: document.getElementById('stat-connections'),
            
            // 层级信息
            layerEntitiesCount: document.getElementById('layer-entities-count'),
            layerFeaturesCount: document.getElementById('layer-features-count'),
            layerScenesCount: document.getElementById('layer-scenes-count'),
            
            // 控制面板
            memorySelect: document.getElementById('memory-select'),
            memorySwitchBtn: document.getElementById('memory-switch-btn'),
            currentMemory: document.getElementById('current-memory'),
            confidenceFilter: document.getElementById('confidence-filter'),
            confidenceValue: document.getElementById('confidence-value'),
            
            // 层级控制
            toggleEntityLayer: document.getElementById('toggle-entity-layer'),
            toggleFeatureLayer: document.getElementById('toggle-feature-layer'),
            toggleSceneLayer: document.getElementById('toggle-scene-layer'),
            
            // 连接线控制
            toggleHorizontalEdges: document.getElementById('toggle-horizontal-edges'),
            toggleVerticalEdges: document.getElementById('toggle-vertical-edges'),
            
            // 搜索
            searchNode: document.getElementById('search-node'),
            searchBtn: document.getElementById('search-btn'),
            
            // 视图控制
            resetViewBtn: document.getElementById('reset-view-btn'),
            exportBtn: document.getElementById('export-btn'),
            toggleStatsBtn: document.getElementById('toggle-stats-btn'),
            
            // 三维控制
            toggleRotation: document.getElementById('toggle-rotation'),
            toggleGrid: document.getElementById('toggle-grid'),
            toggleAxes: document.getElementById('toggle-axes'),
            
            // 信息显示
            selectedInfo: document.getElementById('selected-info'),
            
            // 更新日志
            updatesList: document.getElementById('updates-list'),
            
            // 性能面板
            statsPanel: document.getElementById('stats-panel')
        };
        
        // 状态
        this.currentSelectedNode = null;
        this.updateCount = 0;
        this.isStatsVisible = false;
        
        // 初始化
        this._init();
    }
    
    /**
     * 初始化UI
     */
    _init() {
        // 绑定事件
        this._bindEvents();
        
        // 设置初始状态
        this._updateConnectionStatus(false);
        this._updateSystemStatus('初始化中...');
        
        // 监听数据加载器事件
        this._setupDataLoaderListeners();
        
        // 监听场景管理器事件
        this._setupSceneManagerListeners();
        
        console.log('UI管理器初始化完成');
    }
    
    /**
     * 绑定事件
     */
    _bindEvents() {
        // Memory切换
        this.elements.memorySwitchBtn.addEventListener('click', () => this._onMemorySwitch());
        
        // 置信度过滤
        this.elements.confidenceFilter.addEventListener('input', (e) => {
            this.elements.confidenceValue.textContent = e.target.value;
            this._onConfidenceFilter(parseFloat(e.target.value));
        });
        
        // 层级控制
        this.elements.toggleEntityLayer.addEventListener('change', (e) => {
            this.sceneManager.toggleLayer('entity', e.target.checked);
        });
        
        this.elements.toggleFeatureLayer.addEventListener('change', (e) => {
            this.sceneManager.toggleLayer('feature', e.target.checked);
        });
        
        this.elements.toggleSceneLayer.addEventListener('change', (e) => {
            this.sceneManager.toggleLayer('scene', e.target.checked);
        });
        
        // 连接线控制
        this.elements.toggleHorizontalEdges.addEventListener('change', (e) => {
            this.sceneManager.toggleEdges('horizontal', e.target.checked);
        });
        
        this.elements.toggleVerticalEdges.addEventListener('change', (e) => {
            this.sceneManager.toggleEdges('vertical', e.target.checked);
        });
        
        // 搜索
        this.elements.searchBtn.addEventListener('click', () => this._onSearch());
        this.elements.searchNode.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') this._onSearch();
        });
        
        // 视图控制
        this.elements.resetViewBtn.addEventListener('click', () => this._onResetView());
        this.elements.exportBtn.addEventListener('click', () => this._onExport());
        this.elements.toggleStatsBtn.addEventListener('click', () => this._onToggleStats());
        
        // 三维控制
        this.elements.toggleRotation.addEventListener('click', () => this._onToggleRotation());
        this.elements.toggleGrid.addEventListener('click', () => this._onToggleGrid());
        this.elements.toggleAxes.addEventListener('click', () => this._onToggleAxes());
    }
    
    /**
     * 设置数据加载器监听器
     */
    _setupDataLoaderListeners() {
        // 连接状态
        this.dataLoader.on('connected', () => {
            this._updateConnectionStatus(true);
            this._addUpdateLog('WebSocket连接成功', 'success');
        });
        
        this.dataLoader.on('status', (data) => {
            this._addUpdateLog(data.message, data.type);
        });
        
        // 数据加载
        this.dataLoader.on('initialData', (data) => {
            this._onInitialData(data);
        });
        
        this.dataLoader.on('graphLoaded', (data) => {
            this._onGraphLoaded(data);
        });
        
        this.dataLoader.on('statsLoaded', (data) => {
            this._onStatsLoaded(data);
        });
        
        this.dataLoader.on('memoryInfoLoaded', (data) => {
            this._onMemoryInfoLoaded(data);
        });
        
        this.dataLoader.on('dataUpdated', (data) => {
            this._onDataUpdated(data);
        });
        
        this.dataLoader.on('memorySwitched', (data) => {
            this._onMemorySwitched(data);
        });
        
        // 错误处理
        this.dataLoader.on('error', (error) => {
            this._onDataError(error);
        });
    }
    
    /**
     * 设置场景管理器监听器
     */
    _setupSceneManagerListeners() {
        // 节点点击事件
        document.addEventListener('threeScene:nodeClick', (event) => {
            this._onNodeClick(event.detail);
        });
    }
    
    /**
     * 处理初始数据
     */
    _onInitialData(data) {
        this._updateStats(data.stats);
        this._updateGraphData(data.graph);
        this._updateLastUpdate();
        this._updateSystemStatus('数据加载完成');
        this._addUpdateLog('初始数据加载完成', 'success');
    }
    
    /**
     * 处理图数据加载
     */
    _onGraphLoaded(data) {
        // 更新场景
        this.sceneManager.loadGraphData(data);
        
        // 更新统计显示
        this._updateStatsDisplay(data.stats);
        
        this._addUpdateLog('图数据加载完成', 'success');
    }
    
    /**
     * 处理统计信息加载
     */
    _onStatsLoaded(data) {
        this._updateStats(data);
        this._addUpdateLog('统计信息更新', 'info');
    }
    
    /**
     * 处理Memory信息加载
     */
    _onMemoryInfoLoaded(data) {
        this._updateMemoryUI(data);
        this._addUpdateLog('Memory信息加载完成', 'success');
    }
    
    /**
     * 处理数据更新
     */
    _onDataUpdated(data) {
        this.updateCount++;
        
        // 更新显示
        this.elements.lastUpdate.textContent = `最后更新: ${new Date().toLocaleTimeString()}`;
        
        // 添加更新日志
        const changeTypeMap = {
            'created': '创建',
            'modified': '修改',
            'deleted': '删除'
        };
        const changeText = changeTypeMap[data.change_type] || data.change_type;
        this._addUpdateLog(`检测到文件${changeText}: ${data.file_path}`, 'new');
        
        // 重新加载数据
        this._loadAllData();
    }
    
    /**
     * 处理Memory切换
     */
    _onMemorySwitched(data) {
        this._updateMemoryUI({
            current_memory_id: data.new_memory_id,
            available_memory_ids: this.dataLoader.getCachedData().memoryInfo?.available_memory_ids || []
        });
        
        this._addUpdateLog(`Memory已切换到: ${data.new_memory_id}`, 'success');
    }
    
    /**
     * 处理数据错误
     */
    _onDataError(error) {
        console.error('数据错误:', error);
        
        let errorMessage = '数据加载失败';
        if (error.error && error.error.message) {
            errorMessage += `: ${error.error.message}`;
        }
        
        this._addUpdateLog(errorMessage, 'error');
        this._updateSystemStatus('数据加载失败');
    }
    
    /**
     * 处理节点点击
     */
    async _onNodeClick(nodeData) {
        this.currentSelectedNode = nodeData;
        
        // 高亮节点
        this.sceneManager.highlightNode(nodeData.nodeId);
        
        // 显示节点信息
        await this._displayNodeInfo(nodeData);
    }
    
    /**
     * 显示节点信息
     */
    async _displayNodeInfo(nodeData) {
        // 添加防御性编程，确保nodeData不为空
        if (!nodeData) {
            console.error('节点数据为空');
            this.elements.selectedInfo.innerHTML = `<div class="error">节点数据为空</div>`;
            return;
        }
        
        const { nodeId, nodeData: data } = nodeData;
        
        // 检查必要的数据是否存在
        if (!nodeId) {
            console.error('节点ID为空', nodeData);
            this.elements.selectedInfo.innerHTML = `<div class="error">节点ID为空</div>`;
            return;
        }
        
        let infoHtml = '';
        
        try {
            // 如果nodeData.data存在，使用它（这是从three-scene.js传递的完整节点数据）
            const nodeInfo = data || nodeData;
            
            // 检查节点类型
            const nodeType = nodeInfo.nodeType || nodeInfo.type || 'unknown';
            
            switch (nodeType) {
                case 'entity':
                    const entityDetails = await this.dataLoader.getEntityDetails(nodeId);
                    infoHtml = this._formatEntityInfo(entityDetails);
                    break;
                    
                case 'feature':
                    const featureDetails = await this.dataLoader.getFeatureDetails(nodeId);
                    infoHtml = this._formatFeatureInfo(featureDetails);
                    break;
                    
                case 'scene':
                    const sceneDetails = await this.dataLoader.getSceneDetails(nodeId);
                    infoHtml = this._formatSceneInfo(sceneDetails);
                    break;
                    
                default:
                    infoHtml = this._formatBasicNodeInfo({
                        nodeId,
                        nodeType,
                        nodeData: nodeInfo
                    });
            }
        } catch (error) {
            console.error('获取节点详情失败:', error);
            infoHtml = `<div class="error">获取节点详情失败: ${error.message}</div>`;
        }
        
        this.elements.selectedInfo.innerHTML = infoHtml;
    }
    
    /**
     * 格式化实体信息
     */
    _formatEntityInfo(entityDetails) {
        const { entity, features } = entityDetails;
        
        let html = `<div class="node-info entity-info">`;
        html += `<h4>实体信息</h4>`;
        html += `<div class="info-row"><strong>ID:</strong> ${entity.id}</div>`;
        html += `<div class="info-row"><strong>类型:</strong> ${entity.type}</div>`;
        html += `<div class="info-row"><strong>置信度:</strong> ${entity.confidence}</div>`;
        
        if (entity.sources && entity.sources.length > 0) {
            html += `<div class="info-row"><strong>来源数:</strong> ${entity.sources.length}</div>`;
        }
        
        if (features && features.length > 0) {
            html += `<div class="info-section"><strong>特征 (${features.length}个):</strong></div>`;
            html += `<div class="features-list">`;
            features.forEach((feature, index) => {
                html += `<div class="feature-item">`;
                html += `<span class="feature-index">${index + 1}.</span> `;
                html += `<span class="feature-text">${feature.feature}</span>`;
                html += `</div>`;
            });
            html += `</div>`;
        }
        
        html += `</div>`;
        return html;
    }
    
    /**
     * 格式化特征信息
     */
    _formatFeatureInfo(featureDetails) {
        const { feature, entity, scenes } = featureDetails;
        
        let html = `<div class="node-info feature-info">`;
        html += `<h4>特征信息</h4>`;
        html += `<div class="info-row"><strong>特征:</strong> ${feature.feature}</div>`;
        html += `<div class="info-row"><strong>置信度:</strong> ${feature.confidence}</div>`;
        
        if (entity) {
            html += `<div class="info-row"><strong>所属实体:</strong> ${entity.id} (${entity.type})</div>`;
        }
        
        if (scenes && scenes.length > 0) {
            html += `<div class="info-section"><strong>来源场景 (${scenes.length}个):</strong></div>`;
            html += `<div class="scenes-list">`;
            scenes.forEach((scene, index) => {
                html += `<div class="scene-item">`;
                html += `<span class="scene-index">${index + 1}.</span> `;
                html += `<span class="scene-id">${scene.id}</span>`;
                if (scene.generated_at) {
                    html += `<span class="scene-time"> (${new Date(scene.generated_at).toLocaleString()})</span>`;
                }
                html += `</div>`;
            });
            html += `</div>`;
        }
        
        html += `</div>`;
        return html;
    }
    
    /**
     * 格式化场景信息
     */
    _formatSceneInfo(sceneDetails) {
        const { scene, related_features, dialogue_content } = sceneDetails;
        
        let html = `<div class="node-info scene-info">`;
        html += `<h4>场景信息</h4>`;
        html += `<div class="info-row"><strong>ID:</strong> ${scene.id}</div>`;
        
        if (scene.dialogue_id) {
            html += `<div class="info-row"><strong>对话ID:</strong> ${scene.dialogue_id}</div>`;
        }
        
        if (scene.episode_id) {
            html += `<div class="info-row"><strong>篇章ID:</strong> ${scene.episode_id}</div>`;
        }
        
        if (scene.generated_at) {
            html += `<div class="info-row"><strong>生成时间:</strong> ${new Date(scene.generated_at).toLocaleString()}</div>`;
        }
        
        // 显示theme（如果有）
        if (scene.theme) {
            html += `<div class="info-section"><strong>主题 (Theme):</strong></div>`;
            html += `<div class="theme-content">${this._formatTextWithLineBreaks(scene.theme)}</div>`;
        }
        
        // 显示diary（如果有）
        if (scene.diary) {
            html += `<div class="info-section"><strong>日记 (Diary):</strong></div>`;
            html += `<div class="diary-content">${this._formatTextWithLineBreaks(scene.diary)}</div>`;
        }
        
        // 显示原始对话内容（如果有）
        if (dialogue_content) {
            html += `<div class="info-section"><strong>原始对话内容:</strong></div>`;
            html += `<div class="dialogue-info">`;
            html += `<div class="info-row"><strong>对话ID:</strong> ${dialogue_content.dialogue_id}</div>`;
            html += `<div class="info-row"><strong>参与者:</strong> ${dialogue_content.participants?.join(', ') || '未知'}</div>`;
            
            if (dialogue_content.turn_span && dialogue_content.turn_span.length === 2) {
                html += `<div class="info-row"><strong>对话轮次范围:</strong> ${dialogue_content.turn_span[0]} - ${dialogue_content.turn_span[1]}</div>`;
            }
            
            html += `<div class="info-row"><strong>总轮次:</strong> ${dialogue_content.total_turns || 0}</div>`;
            html += `<div class="info-row"><strong>提取轮次:</strong> ${dialogue_content.selected_turns?.length || 0}</div>`;
            html += `</div>`;
            
            // 显示对话轮次
            if (dialogue_content.selected_turns && dialogue_content.selected_turns.length > 0) {
                html += `<div class="dialogue-turns">`;
                dialogue_content.selected_turns.forEach((turn, index) => {
                    const turnNumber = dialogue_content.turn_span ? dialogue_content.turn_span[0] + index : turn.turn_id;
                    html += `<div class="dialogue-turn">`;
                    html += `<div class="turn-header">`;
                    html += `<span class="turn-number">轮次 ${turnNumber}</span>`;
                    html += `<span class="turn-speaker">${turn.speaker}</span>`;
                    if (turn.timestamp) {
                        html += `<span class="turn-time">${new Date(turn.timestamp).toLocaleTimeString()}</span>`;
                    }
                    html += `</div>`;
                    html += `<div class="turn-text">${this._formatTextWithLineBreaks(turn.text)}</div>`;
                    html += `</div>`;
                });
                html += `</div>`;
            }
        } else if (scene.source && scene.source.episodes && scene.source.episodes.length > 0) {
            // 如果没有完整的对话内容，但source信息存在，显示source信息
            html += `<div class="info-section"><strong>原始对话来源:</strong></div>`;
            scene.source.episodes.forEach((episode, index) => {
                html += `<div class="episode-source">`;
                html += `<div class="info-row"><strong>篇章${index + 1}:</strong> ${episode.episode_id}</div>`;
                if (episode.turn_span && episode.turn_span.length === 2) {
                    html += `<div class="info-row"><strong>对话轮次范围:</strong> ${episode.turn_span[0]} - ${episode.turn_span[1]}</div>`;
                }
                html += `</div>`;
            });
        }
        
        // 显示meta信息（如果有）
        if (scene.meta) {
            html += `<div class="info-section"><strong>元数据:</strong></div>`;
            html += `<div class="meta-info">`;
            if (scene.meta.created_at) {
                html += `<div class="info-row"><strong>创建时间:</strong> ${new Date(scene.meta.created_at).toLocaleString()}</div>`;
            }
            if (scene.meta.memory_owner) {
                html += `<div class="info-row"><strong>记忆所有者:</strong> ${scene.meta.memory_owner}</div>`;
            }
            if (scene.meta.language) {
                html += `<div class="info-row"><strong>语言:</strong> ${scene.meta.language}</div>`;
            }
            html += `</div>`;
        }
        
        if (related_features && related_features.length > 0) {
            html += `<div class="info-section"><strong>相关特征 (${related_features.length}个):</strong></div>`;
            html += `<div class="related-features-list">`;
            related_features.forEach((item, index) => {
                html += `<div class="related-feature-item">`;
                html += `<span class="feature-index">${index + 1}.</span> `;
                html += `<span class="feature-text">${item.feature.feature}</span>`;
                if (item.entity) {
                    html += `<span class="entity-ref"> (实体: ${item.entity.id})</span>`;
                }
                html += `</div>`;
            });
            html += `</div>`;
        }
        
        html += `</div>`;
        return html;
    }
    
    /**
     * 格式化文本，将换行符转换为HTML换行
     */
    _formatTextWithLineBreaks(text) {
        if (!text) return '';
        // 将换行符转换为<br>标签，并转义HTML特殊字符
        return text
            .replace(/&/g, '&')
            .replace(/</g, '<')
            .replace(/>/g, '>')
            .replace(/"/g, '"')
            .replace(/'/g, '&#039;')
            .replace(/\n/g, '<br>');
    }
    
    /**
     * 格式化基本节点信息
     */
    _formatBasicNodeInfo(data) {
        let html = `<div class="node-info basic-info">`;
        html += `<h4>节点信息</h4>`;
        html += `<div class="info-row"><strong>ID:</strong> ${data.nodeId || '未知'}</div>`;
        html += `<div class="info-row"><strong>类型:</strong> ${data.nodeType || '未知'}</div>`;
        
        // 安全地访问标签信息
        const label = data.nodeData?.label || data.label || data.nodeData?.id || data.nodeId || '无标签';
        html += `<div class="info-row"><strong>标签:</strong> ${label}</div>`;
        
        // 如果有其他信息，也显示出来
        if (data.nodeData) {
            if (data.nodeData.type && data.nodeData.type !== data.nodeType) {
                html += `<div class="info-row"><strong>实体类型:</strong> ${data.nodeData.type}</div>`;
            }
            if (data.nodeData.confidence !== undefined) {
                html += `<div class="info-row"><strong>置信度:</strong> ${data.nodeData.confidence}</div>`;
            }
        }
        
        html += `</div>`;
        return html;
    }
    
    /**
     * 处理Memory切换
     */
    async _onMemorySwitch() {
        const memoryId = this.elements.memorySelect.value;
        if (!memoryId) {
            alert('请选择要切换的Memory ID');
            return;
        }
        
        const currentMemory = this.elements.currentMemory.textContent.replace('当前: ', '');
        if (memoryId === currentMemory) {
            this._addUpdateLog(`已经是当前Memory: ${memoryId}`, 'info');
            return;
        }
        
        try {
            await this.dataLoader.switchMemory(memoryId);
        } catch (error) {
            alert(`切换Memory失败: ${error.message}`);
        }
    }
    
    /**
     * 处理置信度过滤
     */
    _onConfidenceFilter(threshold) {
        // 这里可以实现基于置信度的过滤逻辑
        console.log('置信度过滤:', threshold);
        this._addUpdateLog(`设置置信度阈值: ${threshold}`, 'info');
    }
    
    /**
     * 处理搜索
     */
    async _onSearch() {
        const query = this.elements.searchNode.value.trim();
        if (!query) {
            alert('请输入搜索关键词');
            return;
        }
        
        try {
            const results = await this.dataLoader.searchNode(query);
            
            if (results.length > 0) {
                // 聚焦到第一个结果
                const firstResult = results[0];
                this.sceneManager.focusOnNode(firstResult.id);
                this._addUpdateLog(`找到 ${results.length} 个匹配结果`, 'success');
            } else {
                this._addUpdateLog(`未找到匹配结果: ${query}`, 'error');
                alert(`未找到匹配结果: ${query}`);
            }
        } catch (error) {
            console.error('搜索失败:', error);
            this._addUpdateLog(`搜索失败: ${error.message}`, 'error');
        }
    }
    
    /**
     * 处理重置视图
     */
    _onResetView() {
        // 重置相机位置
        this.sceneManager.camera.position.set(200, 200, 300);
        this.sceneManager.controls.target.set(0, 0, 100);
        this.sceneManager.controls.update();
        
        this._addUpdateLog('视图已重置', 'info');
    }
    
    /**
     * 处理导出
     */
    _onExport() {
        const success = this.sceneManager.exportScreenshot();
        if (success) {
            this._addUpdateLog('截图已导出', 'success');
        }
    }
    
    /**
     * 处理切换性能显示
     */
    _onToggleStats() {
        this.isStatsVisible = !this.isStatsVisible;
        this.elements.statsPanel.style.display = this.isStatsVisible ? 'block' : 'none';
        
        const buttonText = this.isStatsVisible ? '隐藏性能' : '显示性能';
        this.elements.toggleStatsBtn.textContent = `📊 ${buttonText}`;
        
        this._addUpdateLog(`性能显示 ${this.isStatsVisible ? '开启' : '关闭'}`, 'info');
    }
    
    /**
     * 处理切换自动旋转
     */
    _onToggleRotation() {
        const isRotating = this.sceneManager.toggleRotation();
        
        const buttonText = isRotating ? '停止旋转' : '自动旋转';
        this.elements.toggleRotation.textContent = `🔄 ${buttonText}`;
        
        this._addUpdateLog(`自动旋转 ${isRotating ? '开启' : '关闭'}`, 'info');
    }
    
    /**
     * 处理切换网格显示
     */
    _onToggleGrid() {
        const showGrid = this.sceneManager.toggleGrid();
        
        const buttonText = showGrid ? '隐藏网格' : '显示网格';
        this.elements.toggleGrid.textContent = `📐 ${buttonText}`;
        
        this._addUpdateLog(`网格显示 ${showGrid ? '开启' : '关闭'}`, 'info');
    }
    
    /**
     * 处理切换坐标轴显示
     */
    _onToggleAxes() {
        const showAxes = this.sceneManager.toggleAxes();
        
        const buttonText = showAxes ? '隐藏坐标轴' : '显示坐标轴';
        this.elements.toggleAxes.textContent = `🧭 ${buttonText}`;
        
        this._addUpdateLog(`坐标轴显示 ${showAxes ? '开启' : '关闭'}`, 'info');
    }
    
    /**
     * 更新连接状态
     */
    _updateConnectionStatus(connected) {
        if (connected) {
            this.elements.connectionStatus.textContent = '● 已连接';
            this.elements.connectionStatus.className = 'status-connected';
            this.elements.apiStatus.textContent = '已连接';
            this.elements.apiStatus.style.color = '#27ae60';
        } else {
            this.elements.connectionStatus.textContent = '● 连接断开';
            this.elements.connectionStatus.className = 'status-disconnected';
            this.elements.apiStatus.textContent = '未连接';
            this.elements.apiStatus.style.color = '#e74c3c';
        }
    }
    
    /**
     * 更新系统状态
     */
    _updateSystemStatus(status) {
        this.elements.systemStatus.textContent = status;
    }
    
    /**
     * 更新最后更新时间
     */
    _updateLastUpdate() {
        this.elements.lastUpdate.textContent = `最后更新: ${new Date().toLocaleTimeString()}`;
    }
    
    /**
     * 更新统计信息
     */
    _updateStats(stats) {
        if (!stats) return;
        
        this.elements.statEntities.textContent = stats.total_entities || 0;
        this.elements.statFeatures.textContent = stats.total_features || 0;
        this.elements.statScenes.textContent = stats.total_scenes || 0;
        this.elements.statConnections.textContent =
            (stats.total_horizontal_edges || 0) + (stats.total_vertical_edges || 0);
    }
    
    /**
     * 更新统计显示
     */
    _updateStatsDisplay(stats) {
        this._updateStats(stats);
        
        // 更新层级计数
        if (stats.total_entities !== undefined) {
            this.elements.layerEntitiesCount.textContent = stats.total_entities;
        }
        if (stats.total_features !== undefined) {
            this.elements.layerFeaturesCount.textContent = stats.total_features;
        }
        if (stats.total_scenes !== undefined) {
            this.elements.layerScenesCount.textContent = stats.total_scenes;
        }
    }
    
    /**
     * 更新图数据
     */
    _updateGraphData(graphData) {
        // 更新场景
        this.sceneManager.loadGraphData(graphData);
        
        // 更新统计显示
        this._updateStatsDisplay(graphData.stats);
    }
    
    /**
     * 更新Memory UI
     */
    _updateMemoryUI(memoryInfo) {
        // 更新当前Memory显示
        this.elements.currentMemory.textContent = `当前: ${memoryInfo.current_memory_id}`;
        
        // 更新下拉选择框
        const select = this.elements.memorySelect;
        select.innerHTML = '';
        
        // 添加选项
        memoryInfo.available_memory_ids.forEach(memoryId => {
            const option = document.createElement('option');
            option.value = memoryId;
            option.textContent = memoryId;
            if (memoryId === memoryInfo.current_memory_id) {
                option.selected = true;
            }
            select.appendChild(option);
        });
        
        // 如果没有选项，添加默认选项
        if (memoryInfo.available_memory_ids.length === 0) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = '无可用Memory';
            select.appendChild(option);
        }
    }
    
    /**
     * 添加更新日志
     */
    _addUpdateLog(message, type = 'info') {
        const updatesList = this.elements.updatesList;
        const updateItem = document.createElement('div');
        
        updateItem.className = `update-item ${type}`;
        updateItem.innerHTML = `
            <span class="update-time">${new Date().toLocaleTimeString()}</span>
            <span class="update-message">${message}</span>
        `;
        
        // 添加到列表顶部
        updatesList.insertBefore(updateItem, updatesList.firstChild);
        
        // 限制日志数量
        while (updatesList.children.length > 20) {
            updatesList.removeChild(updatesList.lastChild);
        }
    }
    
    /**
     * 加载所有数据
     */
    async _loadAllData() {
        try {
            this._updateSystemStatus('正在加载数据...');
            
            // 并行加载数据
            await Promise.all([
                this.dataLoader.loadGraphData(),
                this.dataLoader.loadStats(),
                this.dataLoader.loadMemoryInfo()
            ]);
            
            this._updateSystemStatus('数据加载完成');
            
        } catch (error) {
            console.error('加载数据失败:', error);
            this._updateSystemStatus('数据加载失败');
        }
    }
    
    /**
     * 启动应用
     */
    async start() {
        console.log('启动三维知识图谱可视化应用...');
        
        // 初始化场景
        this.sceneManager.init();
        
        // 连接WebSocket
        this.dataLoader.connectWebSocket();
        
        // 加载初始数据
        await this._loadAllData();
        
        console.log('应用启动完成');
        this._addUpdateLog('应用启动完成', 'success');
    }
    
    /**
     * 销毁应用
     */
    destroy() {
        // 断开WebSocket
        this.dataLoader.disconnect();
        
        // 销毁场景
        this.sceneManager.destroy();
        
        console.log('应用已销毁');
    }
}

// 导出类
if (typeof module !== 'undefined' && module.exports) {
    module.exports = UIManager;
}