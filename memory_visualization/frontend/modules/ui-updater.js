// UI状态和列表更新器
class UIUpdater {
    constructor() {
        // 缓存DOM元素引用
        this.elements = {
            statDialogues: document.getElementById('stat-dialogues'),
            statEpisodes: document.getElementById('stat-episodes'),
            statQualifications: document.getElementById('stat-qualifications'),
            statScenes: document.getElementById('stat-scenes'),
            apiStatus: document.getElementById('api-status'),
            connectionStatus: document.getElementById('connection-status'),
            systemStatus: document.getElementById('system-status'),
            lastUpdate: document.getElementById('last-update'),
            updatesList: document.getElementById('updates-list')
        };
    }

    updateStats(stats, isConnected) {
        // 更新统计卡片
        if (this.elements.statDialogues) {
            this.elements.statDialogues.textContent = stats.total_dialogues || 0;
        }
        if (this.elements.statEpisodes) {
            this.elements.statEpisodes.textContent = stats.total_episodes || 0;
        }
        if (this.elements.statQualifications) {
            this.elements.statQualifications.textContent = stats.total_qualifications || 0;
        }
        if (this.elements.statScenes) {
            this.elements.statScenes.textContent = stats.total_scenes || 0;
        }
        
        // 更新API状态
        if (this.elements.apiStatus) {
            this.elements.apiStatus.textContent = isConnected ? '已连接' : '未连接';
            this.elements.apiStatus.className = isConnected ? 'status-connected' : 'status-disconnected';
        }
    }

    updateConnectionStatus(connected) {
        if (!this.elements.connectionStatus) return;
        
        if (connected) {
            this.elements.connectionStatus.innerHTML = '<i class="fas fa-circle"></i> 已连接';
            this.elements.connectionStatus.className = 'status-connected';
        } else {
            this.elements.connectionStatus.innerHTML = '<i class="fas fa-circle"></i> 连接断开';
            this.elements.connectionStatus.className = 'status-disconnected';
        }
    }

    updateSystemStatus(status) {
        if (this.elements.systemStatus) {
            this.elements.systemStatus.textContent = status;
        }
    }

    updateLastUpdateTime() {
        if (this.elements.lastUpdate) {
            const timestamp = new Date().toLocaleTimeString();
            this.elements.lastUpdate.innerHTML = `<i class="fas fa-clock"></i> 最后更新: ${timestamp}`;
        }
    }

    addUpdateLog(message) {
        if (!this.elements.updatesList) return;
        
        const timestamp = new Date().toLocaleTimeString();
        const updateItem = document.createElement('div');
        updateItem.className = 'update-item';
        updateItem.innerHTML = `<i class="fas fa-info-circle"></i> [${timestamp}] ${message}`;
        
        this.elements.updatesList.insertBefore(updateItem, this.elements.updatesList.firstChild);
        
        if (this.elements.updatesList.children.length > 10) {
            this.elements.updatesList.removeChild(this.elements.updatesList.lastChild);
        }
    }

    // 清空列表容器
    clearList(listId) {
        const listElement = document.getElementById(listId);
        if (listElement) {
            listElement.innerHTML = '';
        }
    }

    // 显示空状态
    showEmptyState(listId, iconClass, message) {
        const listElement = document.getElementById(listId);
        if (listElement) {
            listElement.innerHTML = `<div class="empty-state"><i class="${iconClass} fa-3x"></i><p>${message}</p></div>`;
        }
    }

    // 显示搜索结果
    displaySearchResults(listId, items, createItemFunc) {
        const listElement = document.getElementById(listId);
        if (!listElement) return;
        
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

    // 切换标签页
    switchTab(tabId) {
        // 更新标签按钮状态
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.classList.remove('active');
        });
        const activeBtn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
        if (activeBtn) {
            activeBtn.classList.add('active');
        }
        
        // 更新标签内容
        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.remove('active');
        });
        const activeContent = document.getElementById(tabId);
        if (activeContent) {
            activeContent.classList.add('active');
        }
    }

    // 选中数据项
    selectItem(listId, itemId) {
        const selector = `#${listId} .data-item`;
        document.querySelectorAll(selector).forEach(item => {
            item.classList.remove('selected');
            if (item.dataset.id === itemId) {
                item.classList.add('selected');
            }
        });
    }

    // 选中episode（特殊处理，因为有组合ID）
    selectEpisode(episodeId, dialogueId) {
        const uniqueId = `${dialogueId}_${episodeId}`;
        document.querySelectorAll('#episodes-list .data-item').forEach(item => {
            item.classList.remove('selected');
            if (item.dataset.id === uniqueId ||
                (item.dataset.episodeId === episodeId && item.dataset.dialogueId === dialogueId)) {
                item.classList.add('selected');
            }
        });
    }
}

export default UIUpdater;