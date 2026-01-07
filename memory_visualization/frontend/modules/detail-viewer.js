// 详情内容查看器
class DetailViewer {
    constructor(dataLoader) {
        this.dataLoader = dataLoader;
    }

    displayDialogueDetail(detail) {
        const detailElement = document.getElementById('dialogue-detail');
        if (!detailElement) return;
        
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

    displayEpisodeDetail(episode, dialogues) {
        const detailElement = document.getElementById('episode-detail');
        if (!detailElement) return;
        
        let html = `
            <div class="detail-header">
                <h3>${episode.episode_id}</h3>
                <div class="detail-meta">
                    <span><i class="fas fa-comments"></i> ${episode.dialogue_id}</span>
                    <span><i class="fas fa-exchange-alt"></i> 轮次 ${episode.turn_span[0]} - ${episode.turn_span[1]}</span>
                </div>
            </div>
        `;
        
        const dialogue = dialogues.find(d => d.dialogue_id === episode.dialogue_id);
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
    }

    displayEpisodeDetailWithScore(episodeDetail) {
        const detailElement = document.getElementById('episode-detail');
        if (!detailElement) return;
        
        const episode = episodeDetail.episode;
        const qualification = episodeDetail.qualification;
        const dialogue = episodeDetail.dialogue;
        
        let html = `
            <div class="detail-header">
                <h3>${episode.episode_id}</h3>
                <div class="detail-meta">
                    <span><i class="fas fa-comments"></i> ${episode.dialogue_id}</span>
                    <span><i class="fas fa-exchange-alt"></i> 轮次 ${episode.turn_span[0]} - ${episode.turn_span[1]}</span>
                </div>
            </div>
        `;
        
        // 显示评分信息
        if (qualification) {
            const score = qualification.scene_potential_score || {};
            const decision = qualification.decision || 'unknown';
            const rationale = qualification.rationale || {};
            
            // 调试：记录实际接收到的评分数据
            console.log('评分数据详情:', {
                qualification,
                score,
                scoreFields: Object.keys(score),
                scoreValues: score
            });
            
            // 动态生成评分网格
            let scoreGridHtml = '';
            const scoreFields = Object.keys(score);
            
            // 过滤掉不需要的字段，只显示实际的评分字段
            const actualScoreFields = scoreFields.filter(field => {
                // 只保留我们关心的评分字段
                const validFields = ['factual_novelty', 'emotional_novelty', 'emotion_novelty', 'information_density', 'novelty', 'density'];
                return validFields.includes(field);
            });
            
            if (actualScoreFields.length > 0) {
                scoreGridHtml = '<div class="score-grid">';
                actualScoreFields.forEach(field => {
                    const value = score[field];
                    // 将字段名转换为中文显示（可扩展）
                    const fieldLabels = {
                        'factual_novelty': '事实新颖性',
                        'emotional_novelty': '情感新颖性',
                        'emotion_novelty': '情感新颖性', // 可能的别名
                        'information_density': '信息密度',
                        'novelty': '新颖性',
                        'density': '信息密度' // information_score.density 的别名
                    };
                    const label = fieldLabels[field] || field.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                    
                    // 调试：记录字段映射
                    console.log(`字段映射: ${field} -> ${label}, 值: ${value}`);
                    
                    // 根据字段确定评分范围
                    let maxScore = 2; // 默认最大值
                    if (field === 'emotional_novelty' || field === 'emotion_novelty') {
                        maxScore = 1; // 情感新颖性范围是0-1
                    }
                    
                    scoreGridHtml += `
                        <div class="score-item">
                            <div class="score-label">${label}</div>
                            <div class="score-value ${this.getScoreClass(value, field)}">${value}/${maxScore}</div>
                        </div>
                    `;
                });
                
                // 决策项
                scoreGridHtml += `
                    <div class="score-item">
                        <div class="score-label">决策</div>
                        <div class="score-decision ${decision === 'scene_candidate' ? 'decision-candidate' : 'decision-reject'}">${decision}</div>
                    </div>
                `;
                
                scoreGridHtml += '</div>';
            }
            
            html += `
                <div class="detail-section">
                    <h4><i class="fas fa-chart-bar"></i> 评分信息</h4>
                    <div class="score-info">
                        ${scoreGridHtml}
            `;
            
            // 动态生成评分理由
            const rationaleFields = Object.keys(rationale);
            if (rationaleFields.length > 0) {
                let rationaleHtml = '<div class="rationale-section"><h5>评分理由</h5>';
                rationaleFields.forEach(field => {
                    const value = rationale[field];
                    const fieldLabels = {
                        'factual_novelty': '事实新颖性',
                        'emotional_novelty': '情感新颖性',
                        'emotion_novelty': '情感新颖性', // 可能的别名
                        'information_density': '信息密度',
                        'novelty': '新颖性',
                        'density': '信息密度' // information_score.density 的别名
                    };
                    const label = fieldLabels[field] || field.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                    rationaleHtml += `<p><strong>${label}:</strong> ${value}</p>`;
                });
                rationaleHtml += '</div>';
                html += rationaleHtml;
            }
            
            html += `
                    </div>
                </div>
            `;
        }
        
        // 显示对话内容
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
    }

    getScoreClass(score, field = null) {
        // 根据字段确定评分范围
        let maxScore = 2; // 默认最大值
        if (field === 'emotional_novelty' || field === 'emotion_novelty') {
            maxScore = 1; // 情感新颖性范围是0-1
        }
        
        // 根据评分范围和值返回CSS类
        if (maxScore === 1) {
            // 0-1范围：1为高，0为低
            if (score >= 1) return 'score-high';
            return 'score-low';
        } else {
            // 0-2范围：2为高，1为中，0为低
            if (score >= 2) return 'score-high';
            if (score >= 1) return 'score-medium';
            return 'score-low';
        }
    }

    getTotalScoreClass(score) {
        if (score >= 3) return 'score-high';
        if (score >= 2) return 'score-medium';
        return 'score-low';
    }

    displaySceneDetail(scene) {
        const detailElement = document.getElementById('scene-detail');
        if (!detailElement) return;
        
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
    }

    displayError(elementId, message = '加载详情失败') {
        const element = document.getElementById(elementId);
        if (element) {
            element.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-exclamation-triangle fa-3x"></i>
                    <p>${message}</p>
                </div>
            `;
        }
    }

    // 从episodes数组中查找特定的episode
    findEpisodeInData(episodes, episodeId, dialogueId) {
        let episode = null;
        
        // 检查数据格式：如果是嵌套结构（有episodes属性）
        const firstItem = episodes[0];
        if (firstItem && firstItem.episodes !== undefined) {
            // 嵌套结构：{dialogue_id: '...', episodes: [...]}
            for (const episodeData of episodes) {
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
            episode = episodes.find(e => e.episode_id === episodeId && e.dialogue_id === dialogueId);
        }
        
        return episode;
    }
}

export default DetailViewer;