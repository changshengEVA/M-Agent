// 搜索功能管理器
class SearchManager {
    constructor(dataLoader, itemRenderer, uiUpdater) {
        this.dataLoader = dataLoader;
        this.itemRenderer = itemRenderer;
        this.uiUpdater = uiUpdater;
    }

    handleSearch(query, activeTab, dialogues, episodes, scenes) {
        if (!query) return;

        switch (activeTab) {
            case 'dialogues-tab':
                this.searchDialogues(query, dialogues);
                break;
            case 'episodes-tab':
                this.searchEpisodes(query, episodes);
                break;
            case 'scenes-tab':
                this.searchScenes(query, scenes);
                break;
        }
    }

    searchDialogues(query, dialogues) {
        const filtered = this.dataLoader.searchDialogues(dialogues, query);
        this.uiUpdater.displaySearchResults('dialogues-list', filtered, 
            this.itemRenderer.createDialogueItem.bind(this.itemRenderer));
    }

    searchEpisodes(query, episodes) {
        const filtered = this.dataLoader.searchEpisodes(episodes, query);
        this.uiUpdater.displaySearchResults('episodes-list', filtered, 
            this.itemRenderer.createEpisodeItem.bind(this.itemRenderer));
    }

    searchScenes(query, scenes) {
        const filtered = this.dataLoader.searchScenes(scenes, query);
        this.uiUpdater.displaySearchResults('scenes-list', filtered, 
            this.itemRenderer.createSceneItem.bind(this.itemRenderer));
    }

    // 获取当前活动的标签页
    getActiveTab() {
        const activeBtn = document.querySelector('.tab-btn.active');
        return activeBtn ? activeBtn.dataset.tab : 'dialogues-tab';
    }

    // 绑定搜索事件
    bindSearchEvents(onSearch) {
        const searchBtn = document.getElementById('search-btn');
        const searchInput = document.getElementById('search-input');
        
        if (searchBtn) {
            searchBtn.addEventListener('click', () => {
                const query = searchInput ? searchInput.value.trim().toLowerCase() : '';
                if (onSearch) onSearch(query);
            });
        }
        
        if (searchInput) {
            searchInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    const query = searchInput.value.trim().toLowerCase();
                    if (onSearch) onSearch(query);
                }
            });
        }
    }

    // 重置搜索（显示所有数据）
    resetSearch(dialogues, episodes, scenes, activeTab) {
        switch (activeTab) {
            case 'dialogues-tab':
                this.itemRenderer.renderDialoguesList(dialogues, 'dialogues-list');
                break;
            case 'episodes-tab':
                this.itemRenderer.renderEpisodesList(episodes, 'episodes-list');
                break;
            case 'scenes-tab':
                this.itemRenderer.renderScenesList(scenes, 'scenes-list');
                break;
        }
    }
}

export default SearchManager;