// 数据项DOM渲染器
class ItemRenderer {
    constructor(onDialogueSelect, onEpisodeSelect, onSceneSelect) {
        this.onDialogueSelect = onDialogueSelect;
        this.onEpisodeSelect = onEpisodeSelect;
        this.onSceneSelect = onSceneSelect;
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
        
        if (this.onDialogueSelect) {
            item.addEventListener('click', () => {
                this.onDialogueSelect(dialogue.dialogue_id);
            });
        }
        
        return item;
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
        
        if (this.onEpisodeSelect) {
            item.addEventListener('click', () => {
                this.onEpisodeSelect(episode.episode_id, episode.dialogue_id);
            });
        }
        
        return item;
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
        
        if (this.onSceneSelect) {
            item.addEventListener('click', () => {
                this.onSceneSelect(scene.scene_id);
            });
        }
        
        return item;
    }

    // 批量渲染列表
    renderDialoguesList(dialogues, listId) {
        const listElement = document.getElementById(listId);
        if (!listElement) return;
        
        listElement.innerHTML = '';
        
        if (dialogues.length === 0) {
            listElement.innerHTML = '<div class="empty-state"><i class="fas fa-comments fa-3x"></i><p>暂无dialogues数据</p></div>';
            return;
        }
        
        dialogues.forEach(dialogue => {
            const item = this.createDialogueItem(dialogue);
            listElement.appendChild(item);
        });
    }

    renderEpisodesList(episodes, listId) {
        const listElement = document.getElementById(listId);
        if (!listElement) return;
        
        listElement.innerHTML = '';
        
        if (episodes.length === 0) {
            listElement.innerHTML = '<div class="empty-state"><i class="fas fa-layer-group fa-3x"></i><p>暂无episodes数据</p></div>';
            return;
        }
        
        const allEpisodes = [];
        
        // 检查数据格式：如果是嵌套结构（有episodes属性）
        const firstItem = episodes[0];
        if (firstItem && firstItem.episodes !== undefined) {
            // 嵌套结构：{dialogue_id: '...', episodes: [...]}
            episodes.forEach(episodeData => {
                const episodeList = episodeData.episodes || [];
                episodeList.forEach(episode => {
                    allEpisodes.push({
                        ...episode,
                        dialogue_id: episodeData.dialogue_id || episodeData.dialogue_id
                    });
                });
            });
        } else {
            // 扁平化结构：直接就是episode对象数组
            allEpisodes.push(...episodes);
        }
        
        allEpisodes.forEach(episode => {
            const item = this.createEpisodeItem(episode);
            listElement.appendChild(item);
        });
    }

    renderScenesList(scenes, listId) {
        const listElement = document.getElementById(listId);
        if (!listElement) return;
        
        listElement.innerHTML = '';
        
        if (scenes.length === 0) {
            listElement.innerHTML = '<div class="empty-state"><i class="fas fa-scroll fa-3x"></i><p>暂无scenes数据</p></div>';
            return;
        }
        
        scenes.forEach(scene => {
            const item = this.createSceneItem(scene);
            listElement.appendChild(item);
        });
    }
}

export default ItemRenderer;