// 图表管理器 - 处理策略统计分布饼状图
class ChartManager {
    constructor() {
        this.charts = {
            strategyDistribution: null,
            userDistribution: null
        };
        
        this.colors = {
            // 策略状态颜色
            sceneAvailable: '#4CAF50', // 绿色
            sceneUnavailable: '#F44336', // 红色
            kgAvailable: '#2196F3', // 蓝色
            kgUnavailable: '#FF9800', // 橙色
            emoAvailable: '#9C27B0', // 紫色
            emoUnavailable: '#795548', // 棕色
            eligible: '#00BCD4', // 青色
            ineligible: '#607D8B', // 蓝灰色
            
            // 新颖性评分颜色
            noveltyHigh: '#FF5722', // 深橙色
            noveltyMedium: '#FFC107', // 琥珀色
            noveltyLow: '#CDDC39', // 浅绿色
        };
    }
    
    // 初始化所有图表
    initCharts() {
        this.initStrategyDistributionChart();
        this.initUserDistributionChart();
    }
    
    // 初始化策略分布饼状图
    initStrategyDistributionChart() {
        const ctx = document.getElementById('score-distribution-chart');
        if (!ctx) {
            console.warn('策略分布图表canvas元素未找到');
            return;
        }
        
        // 销毁现有图表
        if (this.charts.strategyDistribution) {
            this.charts.strategyDistribution.destroy();
        }
        
        this.charts.strategyDistribution = new Chart(ctx, {
            type: 'pie',
            data: {
                labels: ['加载中...'],
                datasets: [{
                    data: [1],
                    backgroundColor: ['#CCCCCC']
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                aspectRatio: 1, // 保持1:1比例
                plugins: {
                    legend: {
                        position: 'right',
                        labels: {
                            font: {
                                size: 10
                            },
                            padding: 8,
                            boxWidth: 12,
                            boxHeight: 12
                        }
                    },
                    title: {
                        display: true,
                        text: 'Episode策略分布',
                        font: {
                            size: 12,
                            weight: 'bold'
                        },
                        padding: {
                            top: 5,
                            bottom: 10
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const label = context.label || '';
                                const value = context.raw || 0;
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = total > 0 ? Math.round((value / total) * 100) : 0;
                                return `${label}: ${value} (${percentage}%)`;
                            }
                        }
                    }
                }
            }
        });
    }
    
    // 初始化用户分布图表
    initUserDistributionChart() {
        const ctx = document.getElementById('user-distribution-chart');
        if (!ctx) {
            console.warn('用户分布图表canvas元素未找到');
            return;
        }
        
        // 销毁现有图表
        if (this.charts.userDistribution) {
            this.charts.userDistribution.destroy();
        }
        
        this.charts.userDistribution = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: ['加载中...'],
                datasets: [{
                    data: [1],
                    backgroundColor: ['#CCCCCC']
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                aspectRatio: 1, // 保持1:1比例
                plugins: {
                    legend: {
                        position: 'right',
                        labels: {
                            font: {
                                size: 10
                            },
                            padding: 8,
                            boxWidth: 12,
                            boxHeight: 12
                        }
                    },
                    title: {
                        display: true,
                        text: '用户分布',
                        font: {
                            size: 12,
                            weight: 'bold'
                        },
                        padding: {
                            top: 5,
                            bottom: 10
                        }
                    }
                }
            }
        });
    }
    
    // 更新策略分布图表
    updateStrategyDistribution(episodeSituation) {
        const ctx = document.getElementById('score-distribution-chart');
        if (!ctx) {
            console.warn('策略分布图表canvas元素未找到');
            return;
        }
        
        if (!episodeSituation || !episodeSituation.episodes) {
            console.warn('没有episode_situation数据可用于更新策略分布图表');
            return;
        }
        
        const episodes = Object.values(episodeSituation.episodes);
        if (episodes.length === 0) {
            console.warn('episode_situation数据为空');
            return;
        }
        
        // 计算策略统计
        const stats = this.calculateStrategyStats(episodes);
        
        // 销毁现有图表
        if (this.charts.strategyDistribution) {
            this.charts.strategyDistribution.destroy();
        }
        
        // 创建新图表
        this.charts.strategyDistribution = new Chart(ctx, {
            type: 'pie',
            data: {
                labels: stats.labels,
                datasets: [{
                    data: stats.data,
                    backgroundColor: stats.colors
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                aspectRatio: 1,
                plugins: {
                    legend: {
                        position: 'right',
                        labels: {
                            font: {
                                size: 10
                            },
                            padding: 8,
                            boxWidth: 12,
                            boxHeight: 12
                        }
                    },
                    title: {
                        display: true,
                        text: `Episode策略分布 (${episodes.length}个)`,
                        font: {
                            size: 12,
                            weight: 'bold'
                        },
                        padding: {
                            top: 5,
                            bottom: 10
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const label = context.label || '';
                                const value = context.raw || 0;
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = total > 0 ? Math.round((value / total) * 100) : 0;
                                return `${label}: ${value} (${percentage}%)`;
                            }
                        }
                    }
                }
            }
        });
        
        console.log('策略分布图表已更新:', stats);
    }
    
    // 计算策略统计
    calculateStrategyStats(episodes) {
        // 场景可用性统计
        const sceneAvailable = episodes.filter(e => e.scene_available).length;
        const sceneUnavailable = episodes.length - sceneAvailable;
        
        // 知识图谱可用性统计
        const kgAvailable = episodes.filter(e => e.kg_available).length;
        const kgUnavailable = episodes.length - kgAvailable;
        
        // 情感信息可用性统计
        const emoAvailable = episodes.filter(e => e.emo_available).length;
        const emoUnavailable = episodes.length - emoAvailable;
        
        // 合格状态统计
        const eligible = episodes.filter(e => e.eligible).length;
        const ineligible = episodes.length - eligible;
        
        // 事实新颖性统计
        const factualNoveltyHigh = episodes.filter(e => e.factual_novelty >= 2).length;
        const factualNoveltyMedium = episodes.filter(e => e.factual_novelty === 1).length;
        const factualNoveltyLow = episodes.filter(e => e.factual_novelty === 0).length;
        
        // 情感新颖性统计
        const emotionalNoveltyHigh = episodes.filter(e => e.emotional_novelty >= 1).length;
        const emotionalNoveltyLow = episodes.filter(e => e.emotional_novelty === 0).length;
        
        // 返回主要策略统计（场景可用性、知识图谱可用性、情感信息可用性、合格状态）
        return {
            labels: [
                '场景可用', '场景不可用',
                '知识图谱可用', '知识图谱不可用',
                '情感信息可用', '情感信息不可用',
                '合格', '不合格'
            ],
            data: [
                sceneAvailable, sceneUnavailable,
                kgAvailable, kgUnavailable,
                emoAvailable, emoUnavailable,
                eligible, ineligible
            ],
            colors: [
                this.colors.sceneAvailable, this.colors.sceneUnavailable,
                this.colors.kgAvailable, this.colors.kgUnavailable,
                this.colors.emoAvailable, this.colors.emoUnavailable,
                this.colors.eligible, this.colors.ineligible
            ],
            // 额外统计信息（可用于其他图表或显示）
            detailedStats: {
                sceneAvailable,
                sceneUnavailable,
                kgAvailable,
                kgUnavailable,
                emoAvailable,
                emoUnavailable,
                eligible,
                ineligible,
                factualNovelty: {
                    high: factualNoveltyHigh,
                    medium: factualNoveltyMedium,
                    low: factualNoveltyLow
                },
                emotionalNovelty: {
                    high: emotionalNoveltyHigh,
                    low: emotionalNoveltyLow
                }
            }
        };
    }
    
    // 更新用户分布图表
    updateUserDistribution(dialogues) {
        const ctx = document.getElementById('user-distribution-chart');
        if (!ctx) {
            console.warn('用户分布图表canvas元素未找到');
            return;
        }
        
        if (!dialogues || dialogues.length === 0) {
            console.warn('没有dialogues数据可用于更新用户分布图表');
            return;
        }
        
        // 统计用户分布
        const userCounts = {};
        dialogues.forEach(dialogue => {
            const userId = dialogue.user_id || 'unknown';
            userCounts[userId] = (userCounts[userId] || 0) + 1;
        });
        
        const labels = Object.keys(userCounts);
        const data = Object.values(userCounts);
        
        // 生成颜色
        const colors = this.generateColors(labels.length);
        
        // 销毁现有图表
        if (this.charts.userDistribution) {
            this.charts.userDistribution.destroy();
        }
        
        // 创建新图表
        this.charts.userDistribution = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: data,
                    backgroundColor: colors
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                aspectRatio: 1,
                plugins: {
                    legend: {
                        position: 'right',
                        labels: {
                            font: {
                                size: 10
                            },
                            padding: 8,
                            boxWidth: 12,
                            boxHeight: 12
                        }
                    },
                    title: {
                        display: true,
                        text: `用户分布 (${dialogues.length}个对话)`,
                        font: {
                            size: 12,
                            weight: 'bold'
                        },
                        padding: {
                            top: 5,
                            bottom: 10
                        }
                    }
                }
            }
        });
    }
    
    // 生成颜色数组
    generateColors(count) {
        const baseColors = [
            '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', 
            '#9966FF', '#FF9F40', '#8AC926', '#1982C4',
            '#6A4C93', '#F15BB5', '#00BBF9', '#00F5D4'
        ];
        
        const colors = [];
        for (let i = 0; i < count; i++) {
            colors.push(baseColors[i % baseColors.length]);
        }
        return colors;
    }
    
    // 销毁所有图表
    destroyCharts() {
        Object.values(this.charts).forEach(chart => {
            if (chart) {
                chart.destroy();
            }
        });
        this.charts = {
            strategyDistribution: null,
            userDistribution: null
        };
    }
    
    // 获取策略统计摘要
    getStrategySummary(episodeSituation) {
        if (!episodeSituation || !episodeSituation.episodes) {
            return {
                total: 0,
                sceneAvailable: 0,
                kgAvailable: 0,
                emoAvailable: 0,
                eligible: 0,
                factualNoveltyHigh: 0,
                emotionalNoveltyHigh: 0
            };
        }
        
        const episodes = Object.values(episodeSituation.episodes);
        const stats = this.calculateStrategyStats(episodes);
        
        return {
            total: episodes.length,
            sceneAvailable: stats.detailedStats.sceneAvailable,
            kgAvailable: stats.detailedStats.kgAvailable,
            emoAvailable: stats.detailedStats.emoAvailable,
            eligible: stats.detailedStats.eligible,
            factualNoveltyHigh: stats.detailedStats.factualNovelty.high,
            emotionalNoveltyHigh: stats.detailedStats.emotionalNovelty.high
        };
    }
}

export default ChartManager;