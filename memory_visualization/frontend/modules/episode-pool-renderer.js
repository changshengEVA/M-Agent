// Episode元素池渲染器
class EpisodePoolRenderer {
    constructor(onEpisodeSelect) {
        this.onEpisodeSelect = onEpisodeSelect;
        this.episodes = [];
        this.filteredEpisodes = [];
        this.selectedEpisodeId = null;
        
        // 绑定事件
        this.bindEvents();
    }
    
    bindEvents() {
        // 过滤选择器事件
        const filterSelect = document.getElementById('episode-filter');
        if (filterSelect) {
            filterSelect.addEventListener('change', () => {
                this.applyFilters();
            });
        }
        
        // 排序选择器事件
        const sortSelect = document.getElementById('episode-sort');
        if (sortSelect) {
            sortSelect.addEventListener('change', () => {
                this.applyFilters();
            });
        }
    }
    
    // 设置episodes和qualifications数据
    setEpisodes(episodes, qualifications) {
        console.log('setEpisodes called with:', {
            episodesCount: episodes?.length || 0,
            qualificationsCount: qualifications?.length || 0,
            qualificationsSample: qualifications?.[0]
        });
        
        this.episodes = this.processEpisodes(episodes, qualifications);
        
        // 调试：检查处理后的episodes
        console.log('Processed episodes:', this.episodes.slice(0, 3).map(e => ({
            episode_id: e.episode_id,
            dialogue_id: e.dialogue_id,
            score: e.score,
            decision: e.decision
        })));
        
        this.applyFilters();
        this.updateStats();
    }
    
    // 处理episodes数据，提取评分信息
    processEpisodes(episodes, qualifications) {
        const processed = [];
        
        // 扁平化episodes数据
        const allEpisodes = this.flattenEpisodes(episodes);
        
        // 创建qualifications映射表：dialogue_id -> {episode_id -> qualification}
        const qualMap = this.createQualificationsMap(qualifications);
        
        // 为每个episode提取评分信息
        allEpisodes.forEach(episode => {
            const dialogueId = episode.dialogue_id;
            const episodeId = episode.episode_id;
            const qualification = this.findQualification(qualMap, dialogueId, episodeId);
            
            const processedEpisode = {
                ...episode,
                score: this.extractScoreFromQualification(qualification),
                decision: this.extractDecisionFromQualification(qualification),
                qualification: qualification || {}
            };
            processed.push(processedEpisode);
        });
        
        return processed;
    }
    
    // 创建qualifications映射表
    createQualificationsMap(qualifications) {
        const map = {};
        
        if (!qualifications || !Array.isArray(qualifications)) {
            console.log('createQualificationsMap: qualifications is not array or empty', qualifications);
            return map;
        }
        
        console.log(`createQualificationsMap: processing ${qualifications.length} qualifications`);
        
        // 检查数据结构：是扁平数组还是嵌套结构
        const firstItem = qualifications[0];
        const isNestedStructure = firstItem && firstItem.qualifications !== undefined;
        
        if (isNestedStructure) {
            // 嵌套结构：{dialogue_id: "...", qualifications: [...]}
            console.log('createQualificationsMap: detected nested structure');
            qualifications.forEach((qualData, index) => {
                const dialogueId = qualData.dialogue_id;
                if (!dialogueId) {
                    console.log(`createQualificationsMap: qualData[${index}] missing dialogue_id`, qualData);
                    return;
                }
                
                if (!map[dialogueId]) {
                    map[dialogueId] = {};
                }
                
                const quals = qualData.qualifications || [];
                console.log(`createQualificationsMap: dialogue ${dialogueId} has ${quals.length} qualifications`);
                
                quals.forEach((qual, qualIndex) => {
                    const episodeId = qual.episode_id;
                    if (episodeId) {
                        map[dialogueId][episodeId] = qual;
                        console.log(`createQualificationsMap: mapped ${dialogueId}/${episodeId} =`, {
                            score: qual.scene_potential_score?.total,
                            decision: qual.decision
                        });
                    } else {
                        console.log(`createQualificationsMap: qualification[${qualIndex}] missing episode_id`, qual);
                    }
                });
            });
        } else {
            // 扁平结构：直接就是qualification对象数组
            console.log('createQualificationsMap: detected flat structure');
            qualifications.forEach((qual, index) => {
                const dialogueId = qual.dialogue_id;
                const episodeId = qual.episode_id;
                
                if (!dialogueId || !episodeId) {
                    console.log(`createQualificationsMap: qualification[${index}] missing dialogue_id or episode_id`, qual);
                    return;
                }
                
                if (!map[dialogueId]) {
                    map[dialogueId] = {};
                }
                
                map[dialogueId][episodeId] = qual;
                console.log(`createQualificationsMap: mapped ${dialogueId}/${episodeId} =`, {
                    score: qual.scene_potential_score?.total,
                    decision: qual.decision
                });
            });
        }
        
        console.log('createQualificationsMap: final map keys', Object.keys(map));
        return map;
    }
    
    // 查找对应的qualification
    findQualification(qualMap, dialogueId, episodeId) {
        if (!qualMap[dialogueId]) {
            console.log(`findQualification: no qualifications found for dialogue ${dialogueId}`);
            return null;
        }
        
        const qual = qualMap[dialogueId][episodeId];
        if (!qual) {
            console.log(`findQualification: no qualification found for ${dialogueId}/${episodeId}`);
            console.log(`Available episode_ids for dialogue ${dialogueId}:`, Object.keys(qualMap[dialogueId]));
        } else {
            console.log(`findQualification: found qualification for ${dialogueId}/${episodeId}:`, {
                score: qual.scene_potential_score?.total,
                decision: qual.decision
            });
        }
        
        return qual || null;
    }
    
    // 从qualification中提取分数
    extractScoreFromQualification(qualification) {
        if (!qualification || !qualification.scene_potential_score) {
            return 0;
        }
        const score = qualification.scene_potential_score;
        
        // 计算总分：将所有评分字段的值相加
        // 支持多种评分字段：factual_novelty, emotional_novelty, information_density, novelty 等
        let total = 0;
        for (const [key, value] of Object.entries(score)) {
            if (typeof value === 'number') {
                total += value;
            }
        }
        
        return total;
    }
    
    // 从qualification中提取决策
    extractDecisionFromQualification(qualification) {
        if (!qualification || !qualification.decision) {
            return 'unknown';
        }
        return qualification.decision;
    }
    
    // 扁平化episodes数据（与data-loader中的方法类似）
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
    
    
    // 应用过滤和排序
    applyFilters() {
        const filterValue = document.getElementById('episode-filter').value;
        const sortValue = document.getElementById('episode-sort').value;
        
        // 应用过滤
        let filtered = [...this.episodes];
        
        switch (filterValue) {
            case 'candidate':
                filtered = filtered.filter(e => e.decision === 'scene_candidate');
                break;
            case 'reject':
                filtered = filtered.filter(e => e.decision === 'reject');
                break;
            case 'high-score':
                filtered = filtered.filter(e => e.score >= 3);
                break;
            case 'medium-score':
                filtered = filtered.filter(e => e.score === 2);
                break;
            case 'low-score':
                filtered = filtered.filter(e => e.score <= 1);
                break;
            // 'all' 不进行过滤
        }
        
        // 应用排序
        switch (sortValue) {
            case 'score-desc':
                filtered.sort((a, b) => b.score - a.score);
                break;
            case 'score-asc':
                filtered.sort((a, b) => a.score - b.score);
                break;
            case 'id':
                filtered.sort((a, b) => a.episode_id.localeCompare(b.episode_id));
                break;
            case 'dialogue':
                filtered.sort((a, b) => {
                    const dialogueCompare = a.dialogue_id.localeCompare(b.dialogue_id);
                    if (dialogueCompare !== 0) return dialogueCompare;
                    return a.episode_id.localeCompare(b.episode_id);
                });
                break;
        }
        
        this.filteredEpisodes = filtered;
        this.render();
        this.updateStats();
    }
    
    // 渲染元素池
    render() {
        const poolElement = document.getElementById('episode-pool');
        if (!poolElement) return;
        
        if (this.filteredEpisodes.length === 0) {
            poolElement.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-layer-group fa-3x"></i>
                    <p>没有匹配的episodes</p>
                </div>
            `;
            return;
        }
        
        poolElement.innerHTML = '';
        
        this.filteredEpisodes.forEach(episode => {
            const element = this.createEpisodeElement(episode);
            poolElement.appendChild(element);
        });
    }
    
    // 创建单个episode元素
    createEpisodeElement(episode) {
        const element = document.createElement('div');
        element.className = `episode-element with-border bg-score-${episode.score}`;
        element.dataset.episodeId = episode.episode_id;
        element.dataset.dialogueId = episode.dialogue_id;
        
        // 如果被选中，添加selected类
        const uniqueId = `${episode.dialogue_id}_${episode.episode_id}`;
        if (this.selectedEpisodeId === uniqueId) {
            element.classList.add('selected');
        }
        
        const scoreClass = `score-${episode.score}`;
        const decisionClass = episode.decision === 'scene_candidate' ? 'decision-dot-candidate' : 'decision-dot-reject';
        
        element.innerHTML = `
            <div class="episode-element-decision ${decisionClass}"></div>
            <div class="episode-element-header">
                <div class="episode-element-id">${episode.episode_id}</div>
                <div class="episode-element-score ${scoreClass}">${episode.score}/3</div>
            </div>
            <div class="episode-element-content">
                <div>对话: ${episode.dialogue_id}</div>
                <div>轮次: ${episode.turn_span?.[0] || 0} - ${episode.turn_span?.[1] || 0}</div>
            </div>
            <div class="episode-element-dialogue">
                ${episode.decision === 'scene_candidate' ? '候选' : '拒绝'}
            </div>
        `;
        
        // 点击事件
        element.addEventListener('click', () => {
            this.selectEpisode(episode);
        });
        
        return element;
    }
    
    // 选择episode
    selectEpisode(episode) {
        const uniqueId = `${episode.dialogue_id}_${episode.episode_id}`;
        
        // 更新选中状态
        this.selectedEpisodeId = uniqueId;
        
        // 更新UI选中状态
        document.querySelectorAll('.episode-element').forEach(el => {
            el.classList.remove('selected');
        });
        
        const selectedElement = document.querySelector(`.episode-element[data-episode-id="${episode.episode_id}"][data-dialogue-id="${episode.dialogue_id}"]`);
        if (selectedElement) {
            selectedElement.classList.add('selected');
        }
        
        // 调用回调函数
        if (this.onEpisodeSelect) {
            this.onEpisodeSelect(episode.episode_id, episode.dialogue_id);
        }
    }
    
    // 更新统计信息
    updateStats() {
        const total = this.filteredEpisodes.length;
        const candidates = this.filteredEpisodes.filter(e => e.decision === 'scene_candidate').length;
        const avgScore = total > 0 
            ? (this.filteredEpisodes.reduce((sum, e) => sum + e.score, 0) / total).toFixed(1)
            : '0.0';
        
        // 更新DOM元素
        const totalElement = document.getElementById('episode-total');
        const candidatesElement = document.getElementById('episode-candidates');
        const avgScoreElement = document.getElementById('episode-avg-score');
        
        if (totalElement) totalElement.textContent = total;
        if (candidatesElement) candidatesElement.textContent = candidates;
        if (avgScoreElement) avgScoreElement.textContent = avgScore;
    }
    
    // 清除选中状态
    clearSelection() {
        this.selectedEpisodeId = null;
        document.querySelectorAll('.episode-element').forEach(el => {
            el.classList.remove('selected');
        });
    }
}

export default EpisodePoolRenderer;