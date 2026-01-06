// API数据加载器
class DataLoader {
    constructor(apiBaseUrl) {
        this.apiBaseUrl = apiBaseUrl;
    }

    async loadAllData() {
        try {
            const [statsRes, dialoguesRes, episodesRes, qualificationsRes, scenesRes] = await Promise.all([
                fetch(`${this.apiBaseUrl}/api/stats`),
                fetch(`${this.apiBaseUrl}/api/dialogues`),
                fetch(`${this.apiBaseUrl}/api/episodes`),
                fetch(`${this.apiBaseUrl}/api/qualifications`),
                fetch(`${this.apiBaseUrl}/api/scenes`)
            ]);
            
            if (!statsRes.ok || !dialoguesRes.ok || !episodesRes.ok || !qualificationsRes.ok || !scenesRes.ok) {
                throw new Error('API请求失败');
            }
            
            const stats = await statsRes.json();
            const dialogues = await dialoguesRes.json();
            const episodes = await episodesRes.json();
            const qualifications = await qualificationsRes.json();
            const scenes = await scenesRes.json();
            
            return { stats, dialogues, episodes, qualifications, scenes };
            
        } catch (error) {
            console.error('加载数据失败:', error);
            throw error;
        }
    }

    async loadDialogueDetail(dialogueId) {
        try {
            const response = await fetch(`${this.apiBaseUrl}/api/dialogue/${dialogueId}`);
            if (!response.ok) throw new Error('加载详情失败');
            return await response.json();
        } catch (error) {
            console.error('加载dialogue详情失败:', error);
            throw error;
        }
    }

    async loadSceneDetail(sceneId) {
        try {
            const response = await fetch(`${this.apiBaseUrl}/api/scene/${sceneId}`);
            if (!response.ok) throw new Error('加载详情失败');
            return await response.json();
        } catch (error) {
            console.error('加载scene详情失败:', error);
            throw error;
        }
    }

    async loadEpisodeDetail(dialogueId, episodeId) {
        try {
            const response = await fetch(`${this.apiBaseUrl}/api/episode/${dialogueId}/${episodeId}`);
            if (!response.ok) throw new Error('加载episode详情失败');
            return await response.json();
        } catch (error) {
            console.error('加载episode详情失败:', error);
            throw error;
        }
    }

    // 辅助方法：扁平化episodes数据
    flattenEpisodes(episodes) {
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
        
        return allEpisodes;
    }

    // 搜索相关方法
    searchDialogues(dialogues, query) {
        return dialogues.filter(dialogue => {
            return dialogue.dialogue_id.toLowerCase().includes(query) ||
                   dialogue.user_id.toLowerCase().includes(query) ||
                   (dialogue.turns && dialogue.turns.some(turn =>
                       turn.text.toLowerCase().includes(query)
                   ));
        });
    }

    searchEpisodes(episodes, query) {
        const allEpisodes = this.flattenEpisodes(episodes);
        return allEpisodes.filter(episode => {
            return episode.episode_id.toLowerCase().includes(query) ||
                   episode.dialogue_id.toLowerCase().includes(query);
        });
    }

    searchScenes(scenes, query) {
        return scenes.filter(scene => {
            return scene.scene_id.toLowerCase().includes(query) ||
                   scene.user_id.toLowerCase().includes(query) ||
                   scene.scene_type.toLowerCase().includes(query) ||
                   (scene.diary && scene.diary.toLowerCase().includes(query));
        });
    }
}

export default DataLoader;